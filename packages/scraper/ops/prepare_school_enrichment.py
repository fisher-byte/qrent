#!/usr/bin/env python3
"""Freeze the latest captured school cohorts for the bounded daily queue; no APIs or MySQL writes."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import mysql.connector

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ops.enrichment_queue import cohort_subset,save_frozen,write_json,verify_sources
from ops.recover_school_capture import campaign_root,require_inventory
from ops.publishable_capture import capture_summary,validate_publishable_capture
from ops.select_school_inventory import audit
from src.models import PropertyData
from src.services.school_scope import targets_from_capture
from src.services.inventory_selection import address_key,select_inventory
from src.services.address_identity import normalized_address
from src.utils.helpers import truncate_string
from src.services.publication import publication_errors
from src.services.publication import source_from_url
from ops.backup_mysql import config_from_env
from enrichment_runner import ENRICHED_ERRORS


def published_source_priority(mysql_env,*,with_addresses=False):
    connection=mysql.connector.connect(**config_from_env(mysql_env),connection_timeout=12,
        compress=True,autocommit=False,allow_local_infile=False)
    cursor=None
    try:
        connection.start_transaction(readonly=True,consistent_snapshot=True)
        cursor=connection.cursor()
        cursor.execute('SELECT id,house_id,url'+(',address' if with_addresses else '')+' FROM properties')
        result={};owners={}
        for row in cursor.fetchall():
            identity,house_id,url=row[:3]
            source=source_from_url(url)
            if source and house_id is not None:
                key=(source,str(house_id))
                result[key]=min(result.get(key,int(identity)),int(identity))
                if with_addresses and row[3]:
                    owners.setdefault(normalized_address(row[3]),set()).add(key)
        return (result,owners) if with_addresses else result
    finally:
        connection.rollback()
        if cursor is not None:cursor.close()
        connection.close()


def published_address_owners(db,published):
    owners={}
    with sqlite3.connect(Path(db).resolve().as_uri()+'?mode=ro',uri=True) as connection:
        connection.row_factory=sqlite3.Row
        for row in connection.execute('SELECT source,house_id,address_line1,address_line2,state FROM properties'):
            identity=(row['source'],str(row['house_id']))
            if identity not in published:continue
            key=address_key(PropertyData.from_dict(dict(row)))
            if key:owners.setdefault(key,set()).add(identity)
    return owners


def identity_safe(prop,owners,published,streets=None):
    return ((prop.source.value,str(prop.house_id)) in published
            or (not owners.get(address_key(prop)) and not (streets or {}).get(
                normalized_address(truncate_string(prop.address_line1,60)))))


def prepare(db,capture_path,output,mysql_env):
    capture=validate_publishable_capture(capture_path)
    original=Path(capture_path).resolve().parent/(capture['run']+'_result.json')
    if Path(db).resolve()!=campaign_root(original)/'properties.db':
        raise ValueError('Daily enrichment must use the original captured inventory')
    require_inventory(db,capture['captured_source_ids'])
    job_path=output/'job.json'
    if job_path.exists():
        job=json.loads(job_path.read_text())
        verify_sources(job)
        if (job.get('capture_sha256')!=hashlib.sha256(capture_path.read_bytes()).hexdigest()
                or Path(job['mysql_env']).resolve()!=mysql_env.resolve()):
            raise ValueError('Frozen daily capture or writer changed; preserve its checkpoint')
        for name,path in [('input',output/'input.json'),('cohorts',output/'cohorts.json'),
                          ('selection',output/'selected-source-groups.json'),('preparation',output/'prepare-receipt.json')]:
            if hashlib.sha256(path.read_bytes()).hexdigest()!=job.get(name+'_sha256'):
                raise ValueError('Frozen daily artifacts changed; explicit recovery is required')
        if any(Path(job[name]).resolve()!=(output/(name+'.json')).resolve() for name in ('input','cohorts')):
            raise ValueError('Frozen daily input paths changed')
        # Publication changes the live identity set. Never re-freeze that
        # dynamic state when resuming a partially published capture.
        return {**json.loads((output/'prepare-receipt.json').read_text()),'resumed_frozen':True}
    if not capture.get('school_scope') or capture.get('requested_cities')!=['sydney']:
        raise ValueError('Daily enrichment requires the original Sydney school capture')
    report=audit(db,capture_path.parent,output/'inventory',capture_result=capture_path)
    targets=targets_from_capture(capture)
    raw=json.loads((output/'inventory/scoped-properties.json').read_text())
    props=[PropertyData.from_dict(dict(p)) for p in raw]
    for p in props:
        identity=(p.source.value,str(p.house_id))
        targets[identity]=[s for s in targets.get(identity,[]) if p.state=='NSW' and s in {'UNSW','USYD','UTS'}]
    published,streets=published_source_priority(mysql_env,with_addresses=True)
    owners=published_address_owners(db,published)
    selection=select_inventory(props,targets,published_priority=published)
    chosen=[g for g in selection['groups'] if not g['review'] and g['schools'] and
            not any(e not in ENRICHED_ERRORS for e in publication_errors(g['primary'],g['schools'][0]))
            and identity_safe(g['primary'],owners,published,streets)]
    records=[g['primary'].to_dict() for g in chosen]
    union={(g['primary'].source.value,str(g['primary'].house_id)):g['schools'] for g in chosen}
    input_path=output/'input.json';cohorts=output/'cohorts.json'
    save_frozen(input_path,records);save_frozen(cohorts,cohort_subset(records,union))
    save_frozen(output/'selected-source-groups.json',[{'primary':[g['primary'].source.value,str(g['primary'].house_id)],
        'sources':[[p.source.value,str(p.house_id),p.url] for p in g['members']],
        'schools':g['schools'],'published_identity_preserved':(g['primary'].source.value,str(g['primary'].house_id)) in published}
        for g in chosen])
    release=Path(__file__).resolve().parents[1]
    deployment=json.loads((release/'DEPLOYMENT.json').read_text())
    job={'release':str(release),'state':'/root/qrent-scraper/enrichment/state.db',
         'input':str(input_path),'cohorts':str(cohorts),'output':str(output/'queue'),
         'mysql_env':str(mysql_env),'batch_size':50,'source_hashes':deployment['files_sha256'],
         'pause_windows':[]}
    for name in ('input','cohorts'):job[name+'_sha256']=hashlib.sha256(Path(job[name]).read_bytes()).hexdigest()
    receipt={'status':'prepared_no_api_calls','capture_run':capture['run'],
        'capture_status':capture['status'],**capture_summary(capture),
        'scope_basis':'original_query_school_cohorts','source_ids':report['selected_source_ids'],
        'address_groups':len(selection['groups']),'eligible_addresses':len(records),
        'required_school_pairs':sum(len(g['schools']) for g in chosen),
        'published_identities_preserved':sum((g['primary'].source.value,str(g['primary'].house_id)) in published for g in chosen),
        'published_address_conflicts_held':sum(not identity_safe(g['primary'],owners,published,streets) for g in selection['groups']),
        'job':str(output/'job.json'),'model_monthly_cny_limit':50,'commute_cash_usd':0}
    write_json(output/'prepare-receipt.json',receipt)
    job.update(capture_sha256=hashlib.sha256(capture_path.read_bytes()).hexdigest(),
        selection_sha256=hashlib.sha256((output/'selected-source-groups.json').read_bytes()).hexdigest(),
        preparation_sha256=hashlib.sha256((output/'prepare-receipt.json').read_bytes()).hexdigest())
    save_frozen(job_path,job)  # Commit marker: every immutable artifact is already durable.
    return receipt


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',type=Path,required=True)
    parser.add_argument('--capture-result',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--mysql-env',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(prepare(args.db,args.capture_result,args.output,args.mysql_env)))

#!/usr/bin/env python3
"""Freeze the latest captured school cohorts for the bounded daily queue; no APIs or MySQL writes."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ops.enrichment_queue import cohort_subset,save_frozen,write_json
from ops.select_school_inventory import audit
from src.models import PropertyData
from src.services.school_scope import targets_from_capture
from src.services.inventory_selection import select_inventory
from src.services.publication import publication_errors
from enrichment_runner import ENRICHED_ERRORS


def prepare(db,capture_path,output,mysql_env):
    capture=json.loads(capture_path.read_text())
    if not capture.get('school_scope') or capture.get('requested_cities')!=['sydney']:
        raise ValueError('Daily enrichment requires the original Sydney school capture')
    report=audit(db,capture_path.parent,output/'inventory',capture_result=capture_path)
    targets=targets_from_capture(capture)
    raw=json.loads((output/'inventory/scoped-properties.json').read_text())
    props=[PropertyData.from_dict(dict(p)) for p in raw]
    for p in props:
        identity=(p.source.value,str(p.house_id))
        targets[identity]=[s for s in targets.get(identity,[]) if p.state=='NSW' and s in {'UNSW','USYD','UTS'}]
    selection=select_inventory(props,targets)
    chosen=[g for g in selection['groups'] if not g['review'] and g['schools'] and
            not any(e not in ENRICHED_ERRORS for e in publication_errors(g['primary'],g['schools'][0]))]
    records=[g['primary'].to_dict() for g in chosen]
    union={(g['primary'].source.value,str(g['primary'].house_id)):g['schools'] for g in chosen}
    input_path=output/'input.json';cohorts=output/'cohorts.json'
    save_frozen(input_path,records);save_frozen(cohorts,cohort_subset(records,union))
    release=Path(__file__).resolve().parents[1]
    deployment=json.loads((release/'DEPLOYMENT.json').read_text())
    job={'release':str(release),'state':'/root/qrent-scraper/enrichment/state.db',
         'input':str(input_path),'cohorts':str(cohorts),'output':str(output/'queue'),
         'mysql_env':str(mysql_env),'batch_size':50,'source_hashes':deployment['files_sha256'],
         'pause_windows':[]}
    for name in ('input','cohorts'):job[name+'_sha256']=hashlib.sha256(Path(job[name]).read_bytes()).hexdigest()
    save_frozen(output/'job.json',job)
    receipt={'status':'prepared_no_api_calls','capture_run':capture['run'],
        'capture_status':capture['status'],'capture_window_complete':capture['status']=='success',
        'scope_basis':'original_query_school_cohorts','source_ids':report['selected_source_ids'],
        'address_groups':len(selection['groups']),'eligible_addresses':len(records),
        'required_school_pairs':sum(len(g['schools']) for g in chosen),
        'job':str(output/'job.json'),'model_monthly_cny_limit':50,'commute_cash_usd':0}
    write_json(output/'prepare-receipt.json',receipt)
    return receipt


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',type=Path,required=True)
    parser.add_argument('--capture-result',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--mysql-env',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(prepare(args.db,args.capture_result,args.output,args.mysql_env)))

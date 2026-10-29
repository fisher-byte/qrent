#!/usr/bin/env python3
"""Process a frozen Sydney cohort through the official, bounded runner in batches.

Only the scoring stage can make model requests. Optional daily transit uses
the existing free NSW provider. Auditing and publication use
cached model/transit results with both request caps zero. No market collection,
new database schema, API purchase or automatic retry is performed here.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from datetime import datetime, timezone


OFFICIAL_STATE = Path('/root/qrent-scraper/enrichment/state.db')


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp=path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str))
    temp.chmod(0o600)
    temp.replace(path)


def save_frozen(path, value):
    if path.exists():
        if json.loads(path.read_text())!=value:
            raise ValueError('Frozen queue input changed; checkpoint preserved')
    else:
        write_json(path,value)


def cohort_subset(properties, targets):
    result={'captured_source_ids':[],'school_cohorts':{}}
    for prop in properties:
        identity=(prop['source'],str(prop['house_id']))
        schools=targets.get(identity,[])
        if prop['state']!='NSW' or not schools or set(schools)-{'UNSW','USYD','UTS'}:
            raise ValueError('Queue requires the original Sydney school membership')
        result['captured_source_ids'].append(list(identity))
        for school in schools:
            result['school_cohorts'].setdefault(school,{}).setdefault(identity[0],[]).append(identity[1])
    return result


def ready_subset(properties,result):
    if len(properties)!=len(result['records']):
        raise ValueError('Audit result does not cover its complete input')
    ready=[]
    for prop,record in zip(properties,result['records']):
        if (prop['source'],str(prop['house_id']))!=(record['source'],str(record['house_id'])):
            raise ValueError('Audit identity does not match its input')
        schools=record['schools']
        if schools and not record['input_errors'] and all(record['checks'].get(s,['missing_check'])==[] for s in schools):
            ready.append(prop)
    return ready


def runner_command(release,state,input_path,cohorts,output,count,stage,mysql_env):
    if stage not in {'score','commute','audit','publish'} or not 0<count<=50:
        raise ValueError('Invalid bounded queue stage')
    cmd=[str(release/'.venv/bin/python'),'-u',str(release/'enrichment_runner.py'),
         '--input',str(input_path),'--cohorts',str(cohorts),'--output',str(output),
         '--state',str(state),'--enrich','--limit',str(count),
         '--max-model-calls',str(count*5 if stage=='score' else 0),
         '--max-maps-calls','2000' if stage=='commute' else '0']
    if stage=='score':cmd.append('--score-only')
    if stage=='commute':cmd.append('--commute-only')
    if stage=='publish':cmd.extend(['--publish','--mysql-env',str(mysql_env)])
    return cmd


def wait_for_free_queue(job):
    # No ledger lock is held here. Keep tomorrow's scheduled free route batch
    # clear even if a long model run reaches its reserved processing window.
    for start,end in job.get('pause_windows',[]):
        start=datetime.fromisoformat(start);end=datetime.fromisoformat(end)
        while start<=datetime.now(timezone.utc)<end:
            time.sleep(30)


def verify_sources(job):
    release=Path(job['release'])
    for name,sha in job['source_hashes'].items():
        if Path(name).is_absolute() or '..' in Path(name).parts or hashlib.sha256((release/name).read_bytes()).hexdigest()!=sha:
            raise ValueError('Source changed; queue requires review')


def run_step(job,folder,count,stage,environment,input_path=None,cohorts=None):
    wait_for_free_queue(job)
    verify_sources(job)
    release=Path(job['release']);stage_out=folder/stage
    cmd=runner_command(release,Path(job['state']),input_path or folder/'input.json',
        cohorts or folder/'cohorts.json',stage_out,count,stage,Path(job['mysql_env']))
    with (folder/(stage+'.log')).open('a') as log:
        completed=subprocess.run(cmd,cwd=release,env=environment,stdout=log,
                                 stderr=subprocess.STDOUT,timeout=1200)
    if completed.returncode not in (0,2) or not (stage_out/'result.json').is_file():
        raise RuntimeError(stage+' stage failed; review its private log before resuming')
    result=json.loads((stage_out/'result.json').read_text())
    if stage=='publish' and result['status']!='published':
        raise RuntimeError('Publication failed; review backup and result before resuming')
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('job',type=Path)
    parser.add_argument('--cache-only',action='store_true')
    parser.add_argument('--nsw-free',action='store_true',help='Fill missing routes using only the free NSW provider')
    args=parser.parse_args()
    if args.nsw_free and args.cache_only:parser.error('--cache-only cannot request NSW routes')
    os.umask(0o077)
    job=json.loads(args.job.read_text());release=Path(job['release'])
    if release.resolve()!=Path(__file__).resolve().parents[1] or Path(job['state']).resolve()!=OFFICIAL_STATE:
        raise ValueError('Use the deployed release and the sole official ledger')
    verify_sources(job)
    for name in ('input','cohorts'):
        if hashlib.sha256(Path(job[name]).read_bytes()).hexdigest()!=job[name+'_sha256']:
            raise ValueError('Frozen cohort changed; queue requires review')
    batch_size=int(job.get('batch_size',50))
    if not 1<=batch_size<=50:raise ValueError('Batch size must be between 1 and 50')
    sys.path.insert(0,str(release))
    from src.services.school_scope import targets_from_capture
    from src.services.inventory_selection import address_key
    from src.models import PropertyData
    targets=targets_from_capture(json.loads(Path(job['cohorts']).read_text()))
    properties=json.loads(Path(job['input']).read_text())
    cohort_subset(properties,targets)
    addresses=[address_key(PropertyData.from_dict(dict(p))) for p in properties]
    if any(x is None for x in addresses) or len(set(addresses))!=len(addresses):
        raise ValueError('Queue input must contain unique complete addresses')
    output=Path(job['output']);output.mkdir(parents=True,exist_ok=True)
    queue_lock=os.open(output/'.queue.lock',os.O_CREAT|os.O_RDWR,0o600)
    fcntl.flock(queue_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    serial_lock=os.open(OFFICIAL_STATE.parent/'queue.lock',os.O_CREAT|os.O_RDWR,0o600)
    fcntl.flock(serial_lock,fcntl.LOCK_EX)
    environment=dict(os.environ,SCRAPER_ENV_FILE=str(release/'.env'),COMMUTE_PROVIDER='nsw',
                     QRENT_MODEL_MONTHLY_CNY_LIMIT='50')
    for key in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy'):
        environment.pop(key,None)
    held=[];receipts=[]
    for offset in range(0,len(properties),batch_size):
        batch=properties[offset:offset+batch_size];folder=output/('batch-%04d'%(offset//batch_size+1))
        save_frozen(folder/'input.json',batch)
        save_frozen(folder/'cohorts.json',cohort_subset(batch,targets))
        receipt_path=folder/'receipt.json'
        if receipt_path.exists():
            receipt=json.loads(receipt_path.read_text())
        else:
            if args.nsw_free:run_step(job,folder,len(batch),'commute',environment)
            score=None if args.cache_only else run_step(job,folder,len(batch),'score',environment)
            audit=run_step(job,folder,len(batch),'audit',environment)
            enriched=json.loads((folder/'audit/properties.json').read_text())
            ready=ready_subset(enriched,audit)
            published=None
            if ready:
                save_frozen(folder/'ready-input.json',ready)
                save_frozen(folder/'ready-cohorts.json',cohort_subset(ready,targets))
                published=run_step(job,folder,len(ready),'publish',environment,
                                   folder/'ready-input.json',folder/'ready-cohorts.json')
            published_ids={(p['source'],str(p['house_id'])) for p in ready}
            receipt={'status':'processed','addresses':len(batch),'published_addresses':len(ready),
                'published_ids':[list(x) for x in sorted(published_ids)],
                'held_ids':[[p['source'],str(p['house_id'])] for p in batch
                            if (p['source'],str(p['house_id'])) not in published_ids],
                'score_complete':None if score is None else score['score_complete_records'],
                'model_budget':audit['model_budget'],'publication':None if published is None else published['publication'],
                'completed_at':datetime.now(timezone.utc).isoformat()}
            write_json(receipt_path,receipt)
        receipts.append(receipt)
        pending={tuple(x) for x in receipt['held_ids']}
        held.extend(p for p in batch if (p['source'],str(p['house_id'])) in pending)
        write_json(output/'progress.json',{'processed_addresses':offset+len(batch),
            'total_addresses':len(properties),'published_addresses':sum(r['published_addresses'] for r in receipts),
            'held_addresses':len(held),'last_batch':receipt})
    write_json(output/'held-input.json',held)
    write_json(output/'held-cohorts.json',cohort_subset(held,targets))
    summary={'status':'processed','addresses':len(properties),
             'published_addresses':sum(r['published_addresses'] for r in receipts),'held_addresses':len(held),
             'batch_count':len(receipts),'cash_commute_requests_allowed':0,
             'model_requests_allowed':not args.cache_only,
             'nsw_free_enabled':args.nsw_free,
             'held_input_sha256':hashlib.sha256((output/'held-input.json').read_bytes()).hexdigest(),
             'held_cohorts_sha256':hashlib.sha256((output/'held-cohorts.json').read_bytes()).hexdigest(),
             'completed_at':datetime.now(timezone.utc).isoformat()}
    write_json(output/'result.json',summary)
    print(json.dumps(summary))
    os.close(serial_lock)
    os.close(queue_lock)


if __name__=='__main__':main()

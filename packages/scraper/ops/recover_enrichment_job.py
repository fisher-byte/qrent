#!/usr/bin/env python3
"""Freeze held and unprocessed inputs without changing an earlier queue receipt."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ops.enrichment_queue import cohort_subset,save_frozen,write_json
from src.services.school_scope import targets_from_capture


def recover_pending(job_path,output,also_receipts=()):
    original=json.loads(Path(job_path).read_text())
    for name in ('input','cohorts'):
        if hashlib.sha256(Path(original[name]).read_bytes()).hexdigest()!=original[name+'_sha256']:
            raise ValueError('Original frozen input changed; do not replace its checkpoint')
    records=json.loads(Path(original['input']).read_text())
    targets=targets_from_capture(json.loads(Path(original['cohorts']).read_text()))
    published=set()
    for folder in [Path(original['output']),*map(Path,also_receipts)]:
        for path in folder.rglob('receipt.json'):
            receipt=json.loads(path.read_text())
            published.update((source,str(identity)) for source,identity in receipt.get('published_ids',[]))
    pending=[p for p in records if (p['source'],str(p['house_id'])) not in published]
    output=Path(output)
    save_frozen(output/'input.json',pending)
    save_frozen(output/'cohorts.json',cohort_subset(pending,targets))
    deployment=json.loads((Path(original['release'])/'DEPLOYMENT.json').read_text())
    hashes=deployment['files_sha256']
    if not hashes:raise ValueError('A verified deployment source manifest is required')
    job={**original,'input':str(output/'input.json'),'cohorts':str(output/'cohorts.json'),
         'output':str(output/'queue'),'source_hashes':hashes}
    for name in ('input','cohorts'):job[name+'_sha256']=hashlib.sha256(Path(job[name]).read_bytes()).hexdigest()
    save_frozen(output/'job.json',job)
    result={'status':'prepared_no_api_calls','original_addresses':len(records),'pending_addresses':len(pending),
            'excluded_published_addresses':len(records)-len(pending),'original_receipts_preserved':True,
            'job':str(output/'job.json')}
    write_json(output/'recovery-receipt.json',result)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('job',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--also-receipts',type=Path,action='append',default=[])
    args=parser.parse_args()
    print(json.dumps(recover_pending(args.job,args.output,args.also_receipts)))

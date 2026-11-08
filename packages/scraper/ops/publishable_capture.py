#!/usr/bin/env python3
"""Validate real captured identities without requiring every query to succeed."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ops.recover_school_capture import (campaign_root,capture_lock,queries,read_json,
    require_inventory,validate_complete_capture,validate_data)
from src.services.school_scope import schools_for_area


def validate_publishable_capture(path):
    path=Path(path).absolute()
    if path.is_symlink():
        raise ValueError('Capture evidence must not use symlinks')
    path=path.resolve()
    data=read_json(path)
    run=data.get('run','')
    if path.name==run+'_recovered_result.json':
        return validate_complete_capture(path)
    campaign_root(path)
    if path.name!=run+'_result.json':
        raise ValueError('Publication requires the original captured run')
    actual=[(item['source'],item['area']) for item in data.get('areas',[])]
    if not actual or set(actual)-set(queries()):
        raise ValueError('Captured queries must belong to the original Sydney scope')
    validate_data(data,actual,run)
    supported=set()
    for item in data['areas']:
        if item['count']:
            expected=schools_for_area(item['area'],item['source'])
            if item.get('query_schools')!=expected:
                raise ValueError('Observed query school provenance changed')
            supported.update((item['source'],school) for school in expected)
    for school,sources in data['school_cohorts'].items():
        for source,identities in sources.items():
            if identities and (source,school) not in supported:
                raise ValueError('School membership has no supporting source query')
    return data


def capture_summary(data):
    attempted=[item for item in data.get('areas',[]) if item.get('attempts')!=0]
    actual={(item['source'],item['area']) for item in attempted}
    successful=sum(not item.get('error') for item in attempted)
    unattempted=len(set(queries())-actual)
    return {'capture_window_complete':data['status']=='success' and not unattempted,
            'successful_queries':successful,'failed_queries':len(attempted)-successful,
            'unattempted_queries':unattempted,
            'captured_source_counts':{source:sum(s==source for s,_ in data['captured_source_ids'])
                                      for source in ('domain','realestate')}}


def select_publishable_capture(folder):
    originals=[path for path in Path(folder).glob('*_result.json')
               if re.fullmatch(r'\d{8}_\d{6}_\d{6}_result\.json',path.name)]
    if not originals:
        raise ValueError('No original Sydney capture result; no publication input')
    original=max(originals,key=lambda path:path.name)
    recovered=original.with_name(original.name.replace('_result.json','_recovered_result.json'))
    chosen=recovered if recovered.exists() else original
    validate_publishable_capture(chosen)
    return chosen


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path('/root/qrent-scraper'))
    parser.add_argument('--capture-result',type=Path,required=True)
    parser.add_argument('--capture-lock-fd',type=int)
    args=parser.parse_args();os.umask(0o077)
    try:
        today=datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d')
        if args.capture_result.parent.name!='school-daily-'+today:
            raise ValueError('Scheduled publication must use the current daily capture')
        data=validate_publishable_capture(args.capture_result)
        original=args.capture_result.parent/(data['run']+'_result.json')
        if campaign_root(original)!=args.root.resolve():
            raise ValueError('Publication requires the original crawler root')
        with capture_lock(args.root,args.capture_lock_fd):
            require_inventory(args.root/'properties.db',data['captured_source_ids'])
        print(json.dumps({'status':'captured_records_verified','publication_allowed':True,
                          'capture_status':data['status'],**capture_summary(data)}))
        return 0
    except (OSError,ValueError,KeyError,TypeError) as error:
        print(json.dumps({'status':'capture_not_publishable','publication_allowed':False,'reason':str(error)}))
        return 1


if __name__=='__main__':sys.exit(main())

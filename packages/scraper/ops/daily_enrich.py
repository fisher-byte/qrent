#!/usr/bin/env python3
"""Use today's captured Sydney cohorts; paused models do not block free/cache publication."""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from zoneinfo import ZoneInfo

from dotenv import dotenv_values

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ops.enrichment_queue import write_json
from src.services.enrichment_state import EnrichmentState


def model_authorized(root,release):
    marker=root/'enrichment/model-investigation-cleared'
    if not marker.is_file():return False
    try:
        approved=json.loads(marker.read_text());values=dotenv_values(release/'.env')
        key=values.get('PROPERTY_RATING_API_KEY') or ''
        model=values.get('QWEN_MODEL')
        if (not key or model!='qwen3.7-flash-2026-07-15' or approved.get('model')!=model or
                approved.get('key_sha256')!=hashlib.sha256(key.encode()).hexdigest()):return False
        with sqlite3.connect('file:'+str(root/'enrichment/state.db')+'?mode=ro',uri=True) as db:
            hold=db.execute('SELECT 1 FROM cache WHERE key=? AND expires>?',
                (EnrichmentState.key('model-provider-hold-v1',key),time.time())).fetchone()
            return not bool(hold)
    except (OSError,ValueError,sqlite3.Error):return False


def run_daily(root,now=None):
    root=Path(root);release=root/'releases/recovery-20261027'
    today=(now or datetime.now(ZoneInfo('Asia/Shanghai'))).strftime('%Y%m%d')
    reports=sorted((root/'output'/('school-daily-'+today)).glob('*_result.json'),key=lambda p:p.stat().st_mtime)
    if not reports:raise RuntimeError('No current Sydney capture receipt; no API requests')
    capture_path=reports[-1];capture=json.loads(capture_path.read_text())
    output=root/'output'/('school-enrichment-'+capture['run'])
    environment=dict(os.environ,SCRAPER_ENV_FILE=str(release/'.env'))
    while not (root/'enrichment/bootstrap-first-pass.json').exists():
        time.sleep(30)  # Freeze published identity preferences after the first pass ends.
    command=[str(release/'.venv/bin/python'),str(release/'ops/prepare_school_enrichment.py'),
        '--db',str(root/'properties.db'),'--capture-result',str(capture_path),'--output',str(output),
        '--mysql-env',str(root/'enrichment/qrent-writer.env')]
    subprocess.run(command,cwd=release,env=environment,check=True)
    model_enabled=model_authorized(root,release)
    bonus_enabled=(root/'enrichment/distance-bonus-validated.json').is_file()
    command=[str(release/'.venv/bin/python'),str(release/'ops/enrichment_queue.py'),
             str(output/'job.json'),'--nsw-free']
    if bonus_enabled:command.append('--bonus-fallback')
    if not model_enabled:command.append('--skip-model')
    done=subprocess.run(command,cwd=release,env=environment)
    if done.returncode==3 and model_enabled:
        write_json(output/'model-paused.json',{'status':'provider_held','new_model_requests_allowed':False})
        command.append('--skip-model')
        done=subprocess.run(command,cwd=release,env=environment)
        model_enabled=False
    if done.returncode:raise RuntimeError('Daily queue failed; preserve its receipt before review')
    result=json.loads((output/'queue/result.json').read_text())
    receipt={'status':'processed' if not result['held_addresses'] else 'processed_with_held_records',
        'capture_run':capture['run'],'capture_status':capture['status'],
        'addresses':result['addresses'],'published_addresses':result['published_addresses'],
        'held_addresses':result['held_addresses'],'models_enabled':model_enabled,'bonus_enabled':bonus_enabled,
        'cash_commute_usd':0,'completed_at':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()}
    write_json(output/'daily-receipt.json',receipt)
    return receipt


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path('/root/qrent-scraper'))
    args=parser.parse_args()
    print(json.dumps(run_daily(args.root)))

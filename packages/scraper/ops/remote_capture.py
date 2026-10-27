#!/usr/bin/env python3
"""Start/check a durable server capture through an existing SSH alias."""
import argparse
import json
import re
import shlex
import subprocess


REMOTE = r'''
from pathlib import Path
import fcntl,json,os,subprocess,sys
spec=json.load(sys.stdin); root=Path(spec['root']); out=root/'output'/spec['campaign']
def load(path):
 return json.loads(path.read_text()) if path.exists() else {}
def live(launch):
 try:
  fields=Path('/proc/'+str(launch['pid'])+'/stat').read_text().split()
  return fields[2]!='Z' and fields[21]==launch['start_ticks']
 except (OSError,KeyError):return False
def status():
 launch=load(out/'launcher.json'); process=load(out/'process-status.json'); report=load(out/'result.json')
 return {'campaign':spec['campaign'],'live':live(launch),'process':process,'launch':launch,
         'report':{k:report.get(k) for k in ('status','exit_code','mode','complete_inventory','listings','counts','pages_visited','detail_requests','details_failed','quality','errors')},
         'traffic':{k:report.get('traffic',{}).get(k) for k in ('proxy_wire_bytes','proxy_bytes','direct_bytes','direct_wire_bytes','exhausted')}}
if spec['action']=='status':print(json.dumps(status()));raise SystemExit()
os.umask(0o077);out.mkdir(parents=True,exist_ok=True)
launch_lock=(out/'.launch.lock').open('a');fcntl.flock(launch_lock,fcntl.LOCK_EX)
old=status()
if old['live'] or old['report'].get('status') in ('success','pilot_success'):
 print(json.dumps(old));raise SystemExit()
if (out/'launcher.json').exists() and not spec['resume']:
 raise SystemExit('Previous campaign is terminal; inspect it and use --resume explicitly')
release=root/'releases/recovery-20261027'
command=['timeout','--kill-after=30s',str(spec['timeout']), 'xvfb-run','-a',str(release/'.venv/bin/python'),str(release/'metro_runner.py'),
 '--mode',spec['mode'],'--cities','sydney','melbourne','--scrapers','domain','realestate',
 '--db',str(root/'properties.db'),'--output',str(out),'--proxy-bytes',str(spec['proxy_bytes']),
 '--max-requests','500000','--detail-limit',str(spec['detail_limit'])]
job={'command':command,'cwd':str(release),'env_file':str(release/'.env'),'output':str(out)}
(out/'job.json').write_text(json.dumps(job,indent=2))
worker=r"""from pathlib import Path
import json,os,subprocess,sys,time
job=json.loads(Path(sys.argv[1]).read_text());out=Path(job['output']);status=out/'process-status.json'
def write(value):
 temp=status.with_suffix('.tmp');temp.write_text(json.dumps(value,indent=2));temp.replace(status)
record={'worker_pid':os.getpid(),'started_at':time.time(),'state':'running'};write(record)
environment=dict(os.environ,SCRAPER_ENV_FILE=job['env_file'])
try:
 child=subprocess.Popen(job['command'],cwd=job['cwd'],env=environment,stdin=subprocess.DEVNULL)
 record['child_pid']=child.pid;write(record);code=child.wait()
except BaseException as error:
 code=1;record['error']=type(error).__name__
record.update(state='finished',exit_code=code,finished_at=time.time());write(record)
raise SystemExit(code)
"""
(out/'run-job.py').write_text(worker)
with (out/'process.log').open('a') as log:
 child=subprocess.Popen(['python3',str(out/'run-job.py'),str(out/'job.json')],stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
try:ticks=Path('/proc/'+str(child.pid)+'/stat').read_text().split()[21]
except OSError:ticks='missing'
(out/'launcher.json').write_text(json.dumps({'pid':child.pid,'start_ticks':ticks,'command':command}))
print(json.dumps(status()))
'''


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['start', 'status'])
    parser.add_argument('--campaign', required=True)
    parser.add_argument('--host', default='qrent-prod')
    parser.add_argument('--root', default='/root/qrent-scraper')
    parser.add_argument('--mode', choices=['pilot', 'full', 'incremental', 'auto'], default='pilot')
    parser.add_argument('--proxy-bytes', type=int, default=10000000)
    parser.add_argument('--detail-limit', type=int, default=4)
    parser.add_argument('--timeout', type=int, default=2400)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,79}', args.campaign):
        parser.error('Use a short lowercase campaign name')
    if args.proxy_bytes <= 0 or args.timeout <= 0 or args.detail_limit < 0:
        parser.error('Invalid budget or timeout')
    spec = vars(args)
    result = subprocess.run(['ssh', args.host, shlex.join(['python3', '-c', REMOTE])],
                            input=json.dumps(spec), text=True, capture_output=True)
    if result.stderr:
        print(result.stderr, file=__import__('sys').stderr)
    print(result.stdout.strip())
    raise SystemExit(result.returncode)

from pathlib import Path
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
import hashlib,json,os,sqlite3,subprocess,sys
job=json.loads(Path(sys.argv[1]).read_text());release=Path(job['release'])
for name,expected in job['source_hashes'].items():
 path=release/name
 if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
  raise SystemExit('Source changed; free queue requires review before requests')
os.environ['SCRAPER_ENV_FILE']=str(release/'.env');os.environ['COMMUTE_PROVIDER']='google'
sys.path.insert(0,str(release))
from src.services.commute_pool import load_pool
entries=load_pool()
if len(entries)!=1 or entries[0]['provider']!='routes':
 raise SystemExit('Only the verified free Routes configuration is allowed')
state='/root/qrent-scraper/enrichment/state.db';command=job['command']
assert command[:2]==[str(release/'.venv/bin/python'),str(release/'enrichment_runner.py')]
assert command[command.index('--state')+1]==state and '--commute-only' in command and '--publish' not in command
assert command[command.index('--max-model-calls')+1]=='0'
zone=ZoneInfo('America/Los_Angeles');now=datetime.now(zone);today=now.date();used=0;monthly=0
with sqlite3.connect('file:'+state+'?mode=ro',uri=True) as db:
 db.execute('BEGIN')
 for run,count in db.execute('select run,count(*) from calls where service=? and quota_group=? and period=? group by run',
                             ('maps',entries[0]['billing_group'],now.strftime('%Y-%m'))):
  monthly+=count
  try:day=datetime.strptime(run,'%Y%m%dT%H%M%S%fZ').replace(tzinfo=timezone.utc).astimezone(zone).date()
  except ValueError:day=today
  if day==today:used+=count
cap=max(0,min(280,9500-monthly,300-used-4))
predecessor=Path(job['predecessor']);ready=predecessor.exists()
summary={'cash_limit_usd':0,'monthly_calls':monthly,'conservative_calls_today':used,'allowed_now':cap,
         'predecessor_ready':ready,'source_files_verified':len(job['source_hashes'])}
print(json.dumps(summary),flush=True)
if '--check' in sys.argv:raise SystemExit()
if not ready:raise SystemExit('Previous free batch has no result; review required')
previous=json.loads(predecessor.read_text())
if any(isinstance(e,dict) or e!='maps request limit reached' for e in previous.get('maps_errors',[])):
 raise SystemExit('Previous batch has a provider error; no automatic retry')
if cap<=0:raise SystemExit('Daily or monthly free allowance exhausted; no requests')
command[command.index('--max-maps-calls')+1]=str(cap)
result=subprocess.run(command,cwd=release,env=os.environ)
raise SystemExit(result.returncode)

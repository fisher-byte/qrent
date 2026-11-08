"""Execute the daily shell entry with offline child programs and the real root contract."""
import os
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ENTRY = Path(__file__).resolve().parents[1] / 'ops/run_school_capture.sh'


class SchoolCaptureEntryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.release = self.root/'releases/recovery-20261027'
        (self.release/'.venv/bin').mkdir(parents=True)
        self.bin = self.root/'bin'; self.bin.mkdir()
        df = self.bin/'df'
        df.write_text('#!/usr/bin/env bash\nprintf "Filesystem 1024-blocks Used Available Capacity Mounted-on\\n/test 4000000 1902848 2097152 48%% /\\n"\n')
        df.chmod(0o700)
        python = self.release/'.venv/bin/python'
        python.write_text('#!'+sys.executable+'''\nimport json,os,sys
from pathlib import Path
args=sys.argv[1:];root=Path(os.environ['QRENT_ROOT'])
if args[0]=='-c': print('20261031_020005_264602');sys.exit(0)
assert any(name in args for name in ('ops/recover_school_capture.py','ops/publishable_capture.py'))
assert args[args.index('--capture-lock-fd')+1]=='9'
assert os.fstat(9).st_ino==(root/'.capture.lock').stat().st_ino
with (root/'children.jsonl').open('a') as stream:stream.write(json.dumps(args)+'\\n')
sys.exit(int(os.environ.get('TEST_RECOVERY_EXIT','0')))
'''); python.chmod(0o700)
        flock = self.bin/'flock'
        flock.write_text('#!'+sys.executable+'''\nimport fcntl,sys
try:fcntl.flock(int(sys.argv[-1]),fcntl.LOCK_EX|fcntl.LOCK_NB)
except BlockingIOError:sys.exit(1)
'''); flock.chmod(0o700)
        timeout = self.bin/'timeout'
        timeout.write_text('#!'+sys.executable+'''\nimport os,sys
sys.exit(int(os.environ.get('TEST_BASE_EXIT','0')))
'''); timeout.chmod(0o700)
        self.trigger = self.root/'original-root-trigger.sh'
        self.trigger.write_text('''set -uo pipefail
bash "$TEST_SCHOOL_ENTRY" "$@"
status=$?
if [[ "$status" == 0 || "$status" == 2 ]]; then
    printf 'started' > "$QRENT_ROOT/enrichment-started"
fi
exit "$status"
''')
        self.env = dict(os.environ, QRENT_ROOT=str(self.root), QRENT_RELEASE_DIR=str(self.release),
                        PATH=str(self.bin)+os.pathsep+os.environ['PATH'], TEST_SCHOOL_ENTRY=str(ENTRY))

    def tearDown(self): self.temp.cleanup()

    def invoke(self, *args):
        return subprocess.run(['bash',str(self.trigger),*args],env=self.env,capture_output=True,text=True,timeout=10)

    def test_only_a_verified_zero_starts_the_original_daily_service(self):
        original = self.root/'original_result.json'
        for status in [1,2,75,0]:
            with self.subTest(status=status):
                marker = self.root/'enrichment-started'; marker.unlink(missing_ok=True)
                self.env['TEST_RECOVERY_EXIT'] = str(status)
                result = self.invoke('--recover-only',str(original))
                self.assertEqual(marker.exists(), status==0, result.stderr)
                self.assertEqual(result.returncode==0, status==0)

    def test_capture_tool_exit_two_cannot_trigger_enrichment(self):
        self.env['TEST_BASE_EXIT'] = '2'
        result = self.invoke()
        self.assertFalse((self.root/'enrichment-started').exists(), result.stderr)
        self.assertNotEqual(result.returncode,0)
        self.assertFalse((self.root/'children.jsonl').exists())

    def test_partial_scheduled_capture_publishes_verified_records_without_auto_recapture(self):
        self.env['TEST_BASE_EXIT']='1'
        result=self.invoke()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertTrue((self.root/'enrichment-started').exists())
        children=[json.loads(line) for line in (self.root/'children.jsonl').read_text().splitlines()]
        self.assertEqual(len(children),1)
        self.assertIn('ops/publishable_capture.py',children[0])

    def test_recovery_only_does_not_launch_the_full_capture(self):
        self.env['TEST_BASE_EXIT'] = '97'
        result = self.invoke('--recover-only',str(self.root/'original_result.json'))
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(len((self.root/'children.jsonl').read_text().splitlines()),1)

    def test_scope_and_score_overrides_are_rejected_before_any_child_request(self):
        result = self.invoke('--score')
        self.assertNotEqual(result.returncode,0)
        self.assertFalse((self.root/'enrichment-started').exists())
        self.assertFalse((self.root/'children.jsonl').exists())

    def test_low_disk_refuses_scheduled_capture_and_leaves_a_daily_receipt(self):
        df = self.bin/'df'
        df.write_text('#!/usr/bin/env bash\nprintf "Filesystem 1024-blocks Used Available Capacity Mounted-on\\n/test 3000000 2800000 200000 94%% /\\n"\n')
        df.chmod(0o700)
        result = self.invoke()
        self.assertEqual(result.returncode, 74, result.stderr)
        self.assertFalse((self.root/'enrichment-started').exists())
        self.assertFalse((self.root/'children.jsonl').exists())
        receipt = json.loads((self.root/'logs/disk-health.jsonl').read_text())
        self.assertEqual(receipt['status'], 'blocked_low_space')
        self.assertEqual(receipt['available_bytes'], 200000 * 1024)

    def test_disk_check_records_warning_without_launching_capture_or_validator(self):
        df = self.bin/'df'
        df.write_text('#!/usr/bin/env bash\nprintf "Filesystem 1024-blocks Used Available Capacity Mounted-on\\n/test 3000000 2400000 600000 80%% /\\n"\n')
        df.chmod(0o700)
        timeout = self.bin/'timeout'
        timeout.write_text('#!/usr/bin/env bash\nexit 97\n')
        result = subprocess.run(['bash', str(ENTRY), '--disk-check'], env=self.env,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.root/'children.jsonl').exists())
        receipt = json.loads((self.root/'logs/disk-health.jsonl').read_text())
        self.assertEqual(receipt['status'], 'low_space_warning')


if __name__ == '__main__': unittest.main()

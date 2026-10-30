"""Execute the daily shell entry with offline child programs and the real root contract."""
import os
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
        python = self.release/'.venv/bin/python'
        python.write_text('#!'+sys.executable+'''\nimport json,os,sys
from pathlib import Path
args=sys.argv[1:];root=Path(os.environ['QRENT_ROOT'])
if args[0]=='-c': print('20261031_020005_264602');sys.exit(0)
assert 'ops/recover_school_capture.py' in args
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


if __name__ == '__main__': unittest.main()

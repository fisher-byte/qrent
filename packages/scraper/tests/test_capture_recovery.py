"""Offline evidence and resume checks; browser/network boundaries are replaced."""
import copy
import fcntl
import hashlib
import importlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from capture_fixtures import RUN, FAILED, report, save


class CaptureRecoveryTests(unittest.TestCase):
    def module(self):
        try:
            return importlib.import_module('ops.recover_school_capture')
        except ModuleNotFoundError:
            self.fail('Bounded full-window capture recovery is missing')

    def campaign(self, temp, failed=FAILED):
        root = Path(temp).resolve()
        original = save(root / ('output/school-daily-20261031/' + RUN + '_result.json'),
                        report(failed=failed))
        release = root / 'releases/recovery-20261027'
        release.mkdir(parents=True)
        (release / 'recovery_runner.py').write_text('offline source fixture')
        (root / 'properties.db').write_bytes(b'preserved captured inventory')
        (root / 'enrichment').mkdir()
        (root / 'enrichment/state.db').write_bytes(b'preserved official ledger')
        return root, original

    def test_recovery_merges_all_41_queries_without_replacing_original_successes_or_provenance(self):
        recovery = self.module()
        with tempfile.TemporaryDirectory() as temp:
            root, original = self.campaign(temp)
            before = original.read_bytes()
            inventory = (root / 'properties.db').read_bytes()
            ledger = (root / 'enrichment/state.db').read_bytes()
            requested = []
            def run_round(root, base, areas, folder, lock_fd):
                requested.append(list(areas))
                result = report([('domain', area) for area in areas])
                result['captured_source_ids'] = [['domain', '110'], ['domain', '900']]
                result['listings'] = 2
                result['school_cohorts'] = {'UNSW': {'domain': ['110', '900']},
                                             'USYD': {'domain': ['900']}, 'UTS': {'domain': ['900']}}
                return save(folder / (RUN + '_result.json'), result)
            output = recovery.recover(root, original, run_round=run_round)
            result = recovery.validate_complete_capture(output)
            self.assertEqual(requested, [FAILED])
            self.assertEqual((result['run'], result['status'], len(result['areas'])), (RUN, 'success', 41))
            self.assertEqual(original.read_bytes(), before)
            self.assertEqual((root / 'properties.db').read_bytes(), inventory)
            self.assertEqual((root / 'enrichment/state.db').read_bytes(), ledger)
            successful = [area for area in json.loads(before)['areas'] if 'error' not in area]
            self.assertTrue(all(area in result['areas'] for area in successful))
            self.assertTrue(all(row in result['captured_source_ids'] for row in json.loads(before)['captured_source_ids']))
            for school in ['UNSW', 'USYD', 'UTS']:
                self.assertIn('110', result['school_cohorts'][school]['domain'])
            with patch.object(recovery, 'run_round', side_effect=AssertionError('Success must not be recaptured')):
                self.assertEqual(recovery.recover(root, original).read_bytes(), output.read_bytes())

    def test_second_round_requests_only_the_area_still_failed_and_no_third_round_is_allowed(self):
        recovery = self.module()
        with tempfile.TemporaryDirectory() as temp:
            root, original = self.campaign(temp)
            requested = []
            def run_round(root, base, areas, folder, lock_fd):
                requested.append(list(areas))
                return save(folder / (RUN + '_result.json'),
                            report([('domain', area) for area in areas], failed=['haymarket-nsw-2000']))
            with self.assertRaises(recovery.IncompleteCapture):
                recovery.recover(root, original, run_round=run_round)
            self.assertEqual(requested, [FAILED, ['haymarket-nsw-2000']])
            with patch.object(recovery, 'run_round', side_effect=AssertionError('A third round is forbidden')):
                with self.assertRaises(recovery.IncompleteCapture):
                    recovery.recover(root, original)
            self.assertEqual(json.loads(original.read_text())['exit_code'], 1)

    def test_restart_reuses_a_completed_round_even_if_its_parent_died_before_checkpointing(self):
        recovery = self.module()
        with tempfile.TemporaryDirectory() as temp:
            root, original = self.campaign(temp)
            def interrupted(root, base, areas, folder, lock_fd):
                save(folder / (RUN + '_result.json'),
                     report([('domain', area) for area in areas], failed=['haymarket-nsw-2000']))
                raise RuntimeError('Parent interrupted after durable child result')
            with self.assertRaises(RuntimeError):
                recovery.recover(root, original, run_round=interrupted)
            requested = []
            def finish(root, base, areas, folder, lock_fd):
                requested.append(list(areas))
                return save(folder / (RUN + '_result.json'), report([('domain', a) for a in areas]))
            result = recovery.recover(root, original, run_round=finish)
            self.assertEqual(requested, [['haymarket-nsw-2000']])
            self.assertEqual(recovery.validate_complete_capture(result)['status'], 'success')

    def test_started_round_without_a_result_is_uncertain_and_cannot_be_repeated(self):
        recovery = self.module()
        with tempfile.TemporaryDirectory() as temp:
            root, original = self.campaign(temp)
            with self.assertRaises(RuntimeError):
                recovery.recover(root, original, run_round=lambda *args: (_ for _ in ()).throw(RuntimeError('Interrupted')))
            with patch.object(recovery, 'run_round', side_effect=AssertionError('Uncertain requests cannot repeat')):
                with self.assertRaises(recovery.IncompleteCapture):
                    recovery.recover(root, original)

    def test_a_missing_earlier_round_cannot_reset_an_already_used_two_round_limit(self):
        recovery = self.module()
        with tempfile.TemporaryDirectory() as temp:
            root, original = self.campaign(temp)
            def fail(root, base, areas, folder, lock_fd):
                return save(folder/(RUN+'_result.json'), report([('domain', a) for a in areas], failed=areas))
            with self.assertRaises(recovery.IncompleteCapture):
                recovery.recover(root, original, run_round=fail)
            journal = original.parent/('recovery-'+RUN)
            (journal/'round-1').rename(journal/'saved-round-1')
            with patch.object(recovery, 'run_round', side_effect=AssertionError('Missing evidence cannot reset attempts')):
                with self.assertRaises(ValueError): recovery.recover(root, original)

    def test_modified_original_or_round_evidence_cannot_be_accepted(self):
        recovery = self.module()
        for target in ['original', 'round', 'aggregate']:
            with self.subTest(target=target), tempfile.TemporaryDirectory() as temp:
                root, original = self.campaign(temp)
                def finish(root, base, areas, folder, lock_fd):
                    return save(folder / (RUN + '_result.json'), report([('domain', a) for a in areas]))
                output = recovery.recover(root, original, run_round=finish)
                path = (original if target == 'original' else output if target == 'aggregate'
                        else next((original.parent / ('recovery-' + RUN)).glob('round-*/' + RUN + '_result.json')))
                payload = json.loads(path.read_text())
                payload['listings'] += 1
                save(path, payload)
                with self.assertRaises(ValueError):
                    recovery.validate_complete_capture(output)

    def test_subset_duplicate_or_wrong_run_cannot_claim_a_complete_window(self):
        recovery = self.module()
        for change in ['subset', 'duplicate', 'run', 'pages']:
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temp:
                root, original = self.campaign(temp, failed=[])
                data = json.loads(original.read_text())
                if change == 'subset': data['areas'] = data['areas'][:3]
                if change == 'duplicate': data['areas'][-1] = copy.deepcopy(data['areas'][0])
                if change == 'run': data['run'] = 'another-run'
                if change == 'pages': data['max_pages_per_area'] = 1
                save(original, data)
                with self.assertRaises(ValueError): recovery.validate_complete_capture(original)

    def test_original_cannot_omit_captured_school_identities_or_add_foreign_ones(self):
        recovery = self.module()
        for change in ['empty', 'missing', 'foreign']:
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temp:
                root, original = self.campaign(temp, failed=[])
                data = json.loads(original.read_text())
                if change == 'empty': data['school_cohorts'] = {}
                elif change == 'missing':
                    for sources in data['school_cohorts'].values():
                        if 'domain' in sources: sources['domain'] = [x for x in sources['domain'] if x != '100']
                else: data['school_cohorts']['UNSW']['domain'].append('999999')
                save(original, data)
                with self.assertRaises(ValueError): recovery.validate_complete_capture(original)

    def test_a_recovery_round_cannot_introduce_ids_without_the_exact_school_union(self):
        recovery = self.module()
        for change in ['empty', 'missing', 'foreign']:
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temp:
                root, original = self.campaign(temp)
                def invalid(root, base, areas, folder, lock_fd):
                    data = report([('domain', a) for a in areas])
                    data['captured_source_ids'] = [['domain', str(i)] for i in [900,901,902]]
                    data['school_cohorts'] = {'UNSW': {'domain': ['900','901']},
                        'USYD': {'domain': ['902']}, 'UTS': {'domain': ['902']}}
                    if change == 'empty': data['school_cohorts'] = {}
                    elif change == 'missing':
                        data['school_cohorts']['USYD']['domain'] = []
                        data['school_cohorts']['UTS']['domain'] = []
                    else: data['school_cohorts']['UNSW']['domain'].append('999999')
                    return save(folder/(RUN+'_result.json'), data)
                with self.assertRaises(ValueError): recovery.recover(root, original, run_round=invalid)

    def test_source_counts_bound_distinct_ids_while_allowing_cross_area_overlap(self):
        recovery = self.module()
        for change in ['too_many', 'too_few', 'overlap']:
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temp:
                root, original = self.campaign(temp, failed=[])
                data = json.loads(original.read_text())
                if change == 'too_many': data['areas'][0]['count'] = 99999
                elif change == 'too_few':
                    for area in data['areas']:
                        if area['source'] == 'domain': area['count'] = 0
                else:
                    for area in data['areas']: area['count'] = 2
                save(original, data)
                if change == 'overlap':
                    validated = recovery.validate_complete_capture(original)
                    self.assertEqual(validated['listings'], 41)
                    for school in ['UNSW','USYD','UTS']:
                        self.assertIn('100',validated['school_cohorts'][school]['domain'])
                else:
                    with self.assertRaises(ValueError): recovery.validate_complete_capture(original)

    def test_capture_dependency_drift_is_rejected_before_any_resumed_request(self):
        recovery = self.module()
        with tempfile.TemporaryDirectory() as temp:
            root, original = self.campaign(temp)
            dependency = root/'releases/recovery-20261027/src/utils/browser.py'
            dependency.parent.mkdir(parents=True); dependency.write_text('reviewed browser source')
            release = root/'releases/recovery-20261027'
            private = release/'.env'; private.write_text('FAKE_PRIVATE_VALUE=not-source')
            cache = release/'src/__pycache__/cached.py'; cache.parent.mkdir(); cache.write_text('cached code')
            venv = release/'.venv/library.py'; venv.parent.mkdir(); venv.write_text('installed dependency')
            fingerprint = recovery.source_fingerprint(root)
            self.assertIn(str(dependency), fingerprint)
            for ignored in (private,cache,venv): self.assertNotIn(str(ignored),fingerprint)
            def interrupted(root, base, areas, folder, lock_fd):
                save(folder/(RUN+'_result.json'),report([('domain', a) for a in areas],failed=['haymarket-nsw-2000']))
                raise RuntimeError('Parent interrupted after the first round')
            with self.assertRaises(RuntimeError): recovery.recover(root, original, run_round=interrupted)
            dependency.write_text('changed browser source')
            with patch.object(recovery,'run_round',side_effect=AssertionError('Dependency drift reached the network boundary')):
                with self.assertRaises(ValueError): recovery.recover(root, original)

    def test_recovery_rejects_a_missing_or_linked_original_inventory_before_requests(self):
        recovery = self.module()
        for change in ['missing','symlink']:
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temp:
                root, original = self.campaign(temp)
                inventory = root/'properties.db'; inventory.unlink()
                if change == 'symlink':
                    other = root/'another.db'; other.write_bytes(b'not the original inventory')
                    inventory.symlink_to(other)
                with patch.object(recovery,'run_round',side_effect=AssertionError('Invalid inventory reached a request')):
                    with self.assertRaises(ValueError): recovery.recover(root, original)
                self.assertFalse((original.parent/('recovery-'+RUN)).exists())

    def test_preparation_rejects_a_full_report_when_original_inventory_lost_captured_ids(self):
        from ops.prepare_school_enrichment import prepare
        with tempfile.TemporaryDirectory() as temp:
            root, original = self.campaign(temp, failed=[])
            inventory = root/'properties.db'; inventory.unlink()
            with sqlite3.connect(inventory) as db:
                db.execute('CREATE TABLE properties(source TEXT, house_id TEXT)')
                db.execute("INSERT INTO properties VALUES ('domain','100')")
            with patch('ops.prepare_school_enrichment.audit',side_effect=AssertionError('Lost captured IDs reached preparation')):
                with self.assertRaises(ValueError): prepare(inventory,original,root/'out',root/'writer.env')
            self.assertFalse((root/'out').exists())

    def test_dotenv_cannot_restore_paid_keys_or_replace_the_fixed_ordinary_route(self):
        recovery = self.module()
        with tempfile.TemporaryDirectory() as temp:
            release = Path(temp).resolve()
            (release/'.env').write_text('PROXY_SERVER=http://paid.invalid:8080\nPROXY_SOURCES=domain\n'
                'PROPERTY_RATING_API_KEY=offline-model\nDASHSCOPE_API_KEY=offline-fallback\n'
                'GOOGLE_MAPS_API_KEY=offline-map\nNSW_API_KEY=offline-nsw\nDISTANCE_MATRIX_API_KEY=offline-gift\n'
                'DOMAIN_BROWSER_PROXY=http://wrong.invalid:8080\nDOMAIN_NATIVE_NETWORK=false\n')
            environment = recovery.ordinary_environment(dict(os.environ),release)
            environment['PYTHONPATH'] = str(Path(__file__).resolve().parents[1])
            code = '''import json,os
from src.config import ScoringConfig,CommuteConfig
names=['PROXY_SERVER','PROXY_SOURCES','PROPERTY_RATING_API_KEY','DASHSCOPE_API_KEY',
       'GOOGLE_MAPS_API_KEY','NSW_API_KEY','DISTANCE_MATRIX_API_KEY']
checks=[os.environ.get(name)=='' for name in names]
checks.extend([ScoringConfig().api_key=='',CommuteConfig().api_key=='',
 os.environ['DOMAIN_BROWSER_PROXY']=='socks5://127.0.0.1:18082',os.environ['DOMAIN_NATIVE_NETWORK']=='true'])
print(json.dumps(checks))
'''
            result = subprocess.run([sys.executable,'-B','-c',code],env=environment,capture_output=True,text=True,check=True)
            self.assertEqual(json.loads(result.stdout), [True]*11)

    def test_wrong_run_scope_or_paid_transport_in_a_round_cannot_complete_recovery(self):
        recovery = self.module()
        for change in ['run', 'extra_area', 'residential', 'model', 'details', 'counts']:
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temp:
                root, original = self.campaign(temp)
                def invalid(root, base, areas, folder, lock_fd):
                    data = report([('domain', a) for a in areas])
                    if change == 'run': data['run'] = 'another-run'
                    if change == 'extra_area': data['areas'].append({'source': 'domain', 'area': 'newtown-nsw-2042'})
                    if change == 'residential': data['traffic']['proxy_wire_bytes'] = 1
                    if change == 'model': data['scored'] = 1
                    if change == 'details': data['detail_requests'] = 1
                    if change == 'counts': data['areas'][0]['count'] = 99999
                    return save(folder / (RUN + '_result.json'), data)
                with self.assertRaises(ValueError): recovery.recover(root, original, run_round=invalid)
                self.assertFalse((original.parent/(RUN+'_recovered_result.json')).exists())

    def test_non_domain_or_non_capture_errors_never_launch_recovery(self):
        recovery = self.module()
        for change in ['realestate', 'details']:
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temp:
                root, original = self.campaign(temp)
                data = json.loads(original.read_text())
                if change == 'realestate':
                    area = next(a for a in data['areas'] if a['source'] == 'realestate')
                    area['error'] = 'RuntimeError'
                    data['errors'].append({'source': area['source'], 'area': area['area'], 'error': 'RuntimeError'})
                else: data['errors'].append({'source': 'domain', 'area': 'newtown-nsw-2042', 'stage': 'details', 'failed': 1})
                save(original, data)
                with patch.object(recovery, 'run_round', side_effect=AssertionError('This error cannot use Domain recovery')):
                    with self.assertRaises(recovery.IncompleteCapture): recovery.recover(root, original)

    def test_active_capture_lock_prevents_any_recovery_side_effect(self):
        recovery = self.module()
        with tempfile.TemporaryDirectory() as temp:
            root, original = self.campaign(temp)
            with (root / '.capture.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with patch.object(recovery, 'run_round', side_effect=AssertionError('An active capture must finish first')):
                    with self.assertRaises(BlockingIOError): recovery.recover(root, original)
            self.assertFalse((original.parent / ('recovery-' + RUN)).exists())

    def test_retry_command_and_environment_cannot_use_residential_models_maps_or_details(self):
        recovery = self.module()
        root = Path('/root/qrent-scraper'); release = root / 'releases/recovery-20261027'
        command = recovery.retry_command(release, root, report(failed=FAILED), FAILED, Path('/out'))
        def value(flag): return command[command.index(flag) + 1]
        self.assertEqual(value('--scrapers'), 'domain')
        self.assertEqual(value('--retries'), '0')
        self.assertEqual(value('--proxy-bytes'), '1')
        self.assertEqual(value('--db'), str(root / 'properties.db'))
        self.assertEqual(value('--run-tag'), RUN)
        self.assertIn('--no-details', command)
        self.assertNotIn('--score', command)
        environment = recovery.ordinary_environment({'PROXY_SERVER': 'http://paid.invalid',
            'PROXY_SOURCES': 'domain,realestate', 'PROPERTY_RATING_API_KEY': 'offline-key',
            'NSW_API_KEY': 'offline-nsw', 'DISTANCE_MATRIX_API_KEY': 'offline-gift'}, release)
        self.assertEqual(environment['PROXY_SERVER'], '')
        self.assertEqual(environment['PROXY_SOURCES'], '')
        self.assertEqual(environment['PROPERTY_RATING_API_KEY'], '')
        self.assertEqual(environment['NSW_API_KEY'], '')
        self.assertEqual(environment['DISTANCE_MATRIX_API_KEY'], '')
        self.assertEqual(environment['DOMAIN_BROWSER_PROXY'], 'socks5://127.0.0.1:18082')
        self.assertEqual(environment['DOMAIN_NATIVE_NETWORK'], 'true')

    def test_newer_partial_does_not_fall_back_to_an_old_success_or_accept_a_subset(self):
        recovery = self.module()
        with tempfile.TemporaryDirectory() as temp:
            root, original = self.campaign(temp, failed=[])
            self.assertEqual(recovery.select_complete_capture(original.parent), original)
            newer = copy.deepcopy(report(failed=FAILED, run='20261031_030000_000001'))
            newer_path = save(original.parent / (newer['run'] + '_result.json'), newer)
            with self.assertRaises(ValueError): recovery.select_complete_capture(original.parent)
            newer['status'] = 'success'; newer['exit_code'] = 0; newer['errors'] = []
            newer['areas'] = newer['areas'][:3]
            save(newer_path, newer)
            with self.assertRaises(ValueError): recovery.select_complete_capture(original.parent)

    def test_fixed_run_tag_records_the_requested_report_and_never_overwrites_it(self):
        from recovery_runner import build_parser, capture
        from src.models import PropertyData, PropertySource
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            try:
                args = build_parser().parse_args(['--run-tag', RUN, '--school-scope', '--cities', 'sydney',
                    '--scrapers', 'domain', '--areas', 'maroubra-nsw-2035', '--pages', '7', '--no-details',
                    '--db', str(root/'properties.db'), '--output', str(root/'round')])
            except SystemExit:
                self.fail('Capture cannot bind its result to the parent recovery run')
            class OfflineDomain:
                browser = None
                def __init__(self, **kwargs): pass
                def scrape_area(self, area):
                    return [PropertyData(house_id='123', source=PropertySource.DOMAIN,
                        address_line1='2/10 Example Street', address_line2=area,
                        state='NSW', postcode='2035', price_per_week=600)]
            data = capture(args, factories={'domain': OfflineDomain})
            self.assertEqual(data['run'], RUN)
            path = root/'round'/(RUN+'_result.json')
            before = path.read_bytes()
            with self.assertRaises(FileExistsError): capture(args, factories={'domain': OfflineDomain})
            self.assertEqual(path.read_bytes(), before)

    def test_real_child_inherits_the_original_capture_lock(self):
        recovery = self.module()
        with tempfile.TemporaryDirectory() as temp:
            root, original = self.campaign(temp)
            folder = root/'round'; folder.mkdir()
            expected = folder/(RUN+'_result.json')
            with recovery.capture_lock(root) as fd:
                child = '''import fcntl,os,pathlib,sys
root=pathlib.Path(sys.argv[1]); fd=int(sys.argv[2]); output=pathlib.Path(sys.argv[3])
assert os.fstat(fd).st_ino==(root/'.capture.lock').stat().st_ino
other=os.open(root/'.capture.lock',os.O_RDWR)
try: fcntl.flock(other,fcntl.LOCK_EX|fcntl.LOCK_NB)
except BlockingIOError: output.write_text('{}')
else: raise AssertionError('Capture lock was not inherited')
'''
                command = [sys.executable, '-c', child, str(root), str(fd), str(expected)]
                with patch.object(recovery, 'retry_command', return_value=command):
                    output = recovery.run_round(root, json.loads(original.read_text()), FAILED, folder, fd)
            self.assertEqual(output, expected)
            self.assertEqual(expected.read_text(), '{}')

    def test_daily_and_preparation_reject_a_subset_before_any_queue_or_database_access(self):
        from datetime import datetime
        from ops.daily_enrich import run_daily
        from ops.prepare_school_enrichment import prepare
        with tempfile.TemporaryDirectory() as temp:
            root, original = self.campaign(temp, failed=[])
            data = json.loads(original.read_text()); data['areas'] = data['areas'][:3]
            save(original, data)
            save(root/'enrichment/bootstrap-first-pass.json', {'status': 'processed'})
            with patch('ops.daily_enrich.subprocess.run', side_effect=AssertionError('Incomplete window reached queue')):
                with self.assertRaises(ValueError): run_daily(root, datetime(2026,10,31))
            with patch('ops.prepare_school_enrichment.published_source_priority', side_effect=AssertionError('Incomplete window reached MySQL')):
                with self.assertRaises(ValueError): prepare(root/'properties.db', original, root/'out', root/'writer.env')


if __name__ == '__main__': unittest.main()

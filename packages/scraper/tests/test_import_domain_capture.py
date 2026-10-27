import copy
import fcntl
import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
with patch('dotenv.load_dotenv', return_value=False):
    from src.models import PropertyData, PropertySource
    from src.services.search_plan import SearchQuery
    from src.services.sqlite_store import SQLiteStore


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def node(query, ids, *, total=None, children=()):
    return {'query': query.to_dict(), 'ids': ids, 'total': len(ids) if total is None else total,
            'first_total': len(ids) if total is None else total, 'hashes': [], 'next_page': 2,
            'pages_visited': 1, 'unchanged_pages': 0, 'status': 'split' if children else 'done',
            'truncated': bool(children), 'children': list(children)}


class ImportDomainCaptureTests(unittest.TestCase):
    def test_import_preserves_sql_only_or_empty_json_details_and_keeps_both_rows_consistent(self):
        names = ('description_en', 'description_cn', 'keywords', 'thumbnail_url', 'available_date')
        expected = ('Existing body', '已有正文', 'balcony', 'https://cdn.example/existing.jpg', '2026-11-01T00:00:00')
        for kind in ('healthy', 'missing', 'empty'):
            with self.subTest(kind=kind):
                store = SQLiteStore(self.target_db)
                prop = self.prop('S1')
                for name, value in zip(names, expected):
                    setattr(prop, name, datetime.fromisoformat(value) if name == 'available_date' else value)
                store.save([prop])
                if kind == 'healthy':
                    store.connection.execute("UPDATE properties SET description_en='SQL fallback' WHERE house_id='S1' AND source='domain'")
                elif kind == 'missing':
                    store.connection.execute("DELETE FROM property_enrichment WHERE house_id='S1' AND source='domain'")
                elif kind == 'empty':
                    data = json.loads(store.connection.execute("SELECT payload FROM property_enrichment WHERE house_id='S1' AND source='domain'").fetchone()[0])
                    data.update({name: None for name in names})
                    store.connection.execute("UPDATE property_enrichment SET payload=? WHERE house_id='S1' AND source='domain'", (json.dumps(data),))
                store.connection.commit()
                store.close()
                self.assertEqual(self.call(apply=True)['status'], 'pending_validation')
                store = SQLiteStore(self.target_db)
                try:
                    sql = store.connection.execute('SELECT '+','.join(names)+" FROM properties WHERE house_id='S1' AND source='domain'").fetchone()
                    data = json.loads(store.connection.execute("SELECT payload FROM property_enrichment WHERE house_id='S1' AND source='domain'").fetchone()[0])
                    prop = next(prop for prop in store.recent(days=365) if prop.house_id == 'S1')
                    for name, value, wanted in zip(names, sql, expected):
                        if name == 'available_date':
                            self.assertEqual(datetime.fromisoformat(value), datetime.fromisoformat(wanted))
                            self.assertEqual(datetime.fromisoformat(data[name]), datetime.fromisoformat(wanted))
                            self.assertEqual(getattr(prop, name), datetime.fromisoformat(wanted))
                        else:
                            self.assertEqual((value, data[name], getattr(prop,name)), (wanted, wanted, wanted))
                finally:
                    store.close()

    def test_import_new_nonempty_details_win_and_source_sql_fills_empty_payload_fields(self):
        from ops.import_domain_capture import source_payloads
        store = SQLiteStore(self.source_db)
        prop = self.prop('S1')
        prop.description_en = 'Source body'
        prop.description_cn = '来源正文'
        prop.keywords = 'source balcony'
        prop.thumbnail_url = 'https://cdn.example/source.jpg'
        prop.available_date = datetime(2026, 11, 2)
        store.save([prop])
        data = json.loads(store.connection.execute("SELECT payload FROM property_enrichment WHERE house_id='S1' AND source='domain'").fetchone()[0])
        for name in ('description_en', 'description_cn', 'keywords', 'thumbnail_url', 'available_date'):
            data[name] = None
        store.connection.execute("UPDATE property_enrichment SET payload=? WHERE house_id='S1' AND source='domain'", (json.dumps(data),))
        store.connection.commit()
        store.close()
        self.assertEqual(source_payloads(self.source_db, self.source)['S1']['description_en'], 'Source body')
        target = SQLiteStore(self.target_db)
        old = self.prop('S1')
        old.description_en = 'Older target body'
        old.description_cn = '旧目标正文'
        old.keywords = 'old keywords'
        old.thumbnail_url = 'https://cdn.example/old.jpg'
        old.available_date = datetime(2026, 11, 1)
        target.save([old])
        target.close()
        self.call(apply=True)
        target = SQLiteStore(self.target_db)
        try:
            prop = next(prop for prop in target.recent(days=365) if prop.house_id == 'S1')
            self.assertEqual((prop.description_en, prop.description_cn, prop.keywords, prop.thumbnail_url, prop.available_date),
                             ('Source body', '来源正文', 'source balcony', 'https://cdn.example/source.jpg', datetime(2026, 11, 2)))
            sql = target.connection.execute("SELECT description_en,description_cn,keywords,thumbnail_url,available_date FROM properties WHERE house_id='S1' AND source='domain'").fetchone()
            payload = json.loads(target.connection.execute("SELECT payload FROM property_enrichment WHERE house_id='S1' AND source='domain'").fetchone()[0])
            for name, value in zip(('description_en', 'description_cn', 'keywords', 'thumbnail_url', 'available_date'), sql):
                self.assertEqual(payload[name], value)
        finally:
            target.close()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source_db = self.root / 'source' / 'p.db'
        self.target_db = self.root / 'target' / 'p.db'
        self.output = self.root / 'target' / 'campaign'
        self.source_path = self.root / 'source' / 'state.json'
        self.process_path = self.root / 'source' / 'process-status.json'
        sydney = SearchQuery('domain', 'sydney')
        left, right = sydney.split()
        melbourne = SearchQuery('domain', 'melbourne')
        self.roots = [sydney.key, melbourne.key]
        self.queries = {
            sydney.key: node(sydney, ['S1'], total=4, children=[left.key, right.key]),
            left.key: node(left, ['S1', 'S2']), right.key: node(right, ['S3', 'S4']),
            melbourne.key: node(melbourne, ['M1', 'M2']),
        }
        scope = hashlib.sha256(b'metro-region-fixture').hexdigest()
        self.source = {'version': 1, 'mode': 'full', 'status': 'success', 'exit_code': 0,
                       'complete_inventory': True, 'errors': [], 'queue': [],
                       'options': {'cities': ['sydney', 'melbourne'], 'sources': ['domain'],
                                   'scope_hash': scope, 'planning_version': 1, 'root_queries': self.roots},
                       'queries': self.queries,
                       'seen': {'domain:' + item: ['domain', 'sydney' if item.startswith('S') else 'melbourne']
                                for item in ['S1', 'S2', 'S3', 'S4', 'M1', 'M2']},
                       'listings': 6, 'pages_visited': 4,
                       'traffic': {'proxy_requests': 0, 'proxy_bytes': 0, 'proxy_wire_bytes': 0,
                                   'proxy_connections': 0, 'direct_requests': 80, 'direct_bytes': 9000,
                                   'wire_hosts': []}}
        self.process = {'state': 'finished', 'exit_code': 0, 'timed_out': False, 'finished_at': 123.0}
        rea = SearchQuery('realestate', 'sydney', 'Western Sydney, NSW', 'atlas')
        self.rea_key = rea.key
        self.target = {'version': 1, 'mode': 'full', 'status': 'partial', 'exit_code': 1,
                       'complete_inventory': False, 'errors': [{'source': 'domain', 'error': '403'}],
                       'queue': [sydney.key, rea.key], 'initial': {'domain:old': 'old-signature'},
                       'options': {**self.source['options'], 'sources': ['domain', 'realestate'],
                                   'root_queries': self.roots + [rea.key], 'db': str(self.target_db.resolve()),
                                   'proxy_bytes': 600000000, 'max_requests': 500000},
                       'queries': {sydney.key: node(sydney, ['old']), melbourne.key: node(melbourne, []),
                                   rea.key: {**node(rea, ['R1']), 'status': 'running'}},
                       'seen': {'domain:old': ['domain', 'sydney'], 'realestate:R1': ['realestate', 'sydney']},
                       'traffic': {'proxy_requests': 40, 'proxy_bytes': 1500, 'proxy_wire_bytes': 2000,
                                   'max_proxy_bytes': 600000000, 'requests': 400},
                       'quality': {'stale': True}, 'coverage': ['stale'], 'completed_at': 'old'}
        source_store = SQLiteStore(self.source_db)
        source_store.save([self.prop(item) for item in ['S1', 'S2', 'S3', 'S4', 'M1', 'M2', 'not-seen']])
        source_store.close()
        target_store = SQLiteStore(self.target_db)
        target_store.save([self.prop('R1', PropertySource.REALESTATE), self.prop('old')])
        target_store.close()
        self.flush()

    def prop(self, house_id, source=PropertySource.DOMAIN):
        return PropertyData(house_id=house_id, source=source, price_per_week=600,
                            address_line1='10 Example Street', suburb='Sydney' if not house_id.startswith('M') else 'Melbourne',
                            state='VIC' if house_id.startswith('M') else 'NSW', postcode='3000' if house_id.startswith('M') else '2000',
                            scraped_at=datetime(2026, 10, 28))

    def flush(self):
        write_json(self.source_path, self.source)
        write_json(self.process_path, self.process)
        write_json(self.output / 'state.json', self.target)
        write_json(self.output / 'result.json', {'status': 'partial', 'exit_code': 1})

    def call(self, apply=False):
        from ops.import_domain_capture import import_capture
        return import_capture(source_state=self.source_path, source_db=self.source_db,
                              source_process=self.process_path, target_output=self.output,
                              target_db=self.target_db, apply=apply)

    def rows(self, path):
        with sqlite3.connect(path) as connection:
            return connection.execute('SELECT house_id,source,payload FROM property_enrichment ORDER BY source,house_id').fetchall()

    def test_default_dry_run_validates_without_changing_database_or_checkpoint(self):
        before = self.rows(self.target_db), (self.output / 'state.json').read_bytes()
        receipt = self.call()
        self.assertEqual((receipt['status'], receipt['listings']), ('dry_run', 6))
        self.assertEqual((self.rows(self.target_db), (self.output / 'state.json').read_bytes()), before)
        self.assertFalse((self.target_db.parent / 'backup').exists())
        self.assertFalse((self.output / 'import-receipt.json').exists())

    def test_apply_backs_up_and_preserves_rea_budget_and_pending_validation(self):
        original_rows = self.rows(self.target_db)
        original_state = (self.output / 'state.json').read_bytes()
        receipt = self.call(apply=True)
        state = json.loads((self.output / 'state.json').read_text())
        self.assertEqual((state['status'], state['exit_code'], state['complete_inventory']), ('partial', 1, False))
        for key in ('options', 'initial', 'traffic'):
            self.assertEqual(state[key], self.target[key])
        self.assertEqual(state['queries'][self.rea_key], self.target['queries'][self.rea_key])
        self.assertEqual(state['queue'], [self.rea_key])
        self.assertEqual(state['seen'], {**self.source['seen'], 'realestate:R1': ['realestate', 'sydney']})
        for key in ('quality', 'coverage', 'completed_at'):
            self.assertNotIn(key, state)
        rows = self.rows(self.target_db)
        self.assertEqual([row for row in rows if row[1] == 'realestate'], [row for row in original_rows if row[1] == 'realestate'])
        self.assertNotIn('not-seen', [row[0] for row in rows])
        self.assertEqual(len(rows), 8)
        backup = Path(receipt['backup'])
        self.assertEqual(self.rows(backup / self.target_db.name), original_rows)
        self.assertEqual((backup / 'state.json').read_bytes(), original_state)
        self.assertEqual((backup.stat().st_mode & 0o777), 0o700)
        self.assertEqual(receipt['status'], 'pending_validation')
        self.assertFalse((self.target_db.parent / 'metro-baseline.json').exists())
        report = json.loads((self.output / 'result.json').read_text())
        self.assertEqual((report['status'], report['exit_code'], report['complete_inventory']), ('partial', 1, False))
        self.assertEqual(report['counts'], {'domain:sydney': 4, 'domain:melbourne': 2, 'realestate:sydney': 1})
        self.assertNotIn('quality', report)
        self.assertEqual(state['external_captures'][0]['traffic']['proxy_bytes'], 0)
        self.assertEqual(len(state['external_captures'][0]['source_state_sha256']), 64)

    def test_repeated_import_is_safe_and_does_not_duplicate_external_capture(self):
        self.call(apply=True)
        first_rows = self.rows(self.target_db)
        self.call(apply=True)
        self.assertEqual(self.rows(self.target_db), first_rows)
        state = json.loads((self.output / 'state.json').read_text())
        self.assertEqual(len(state['external_captures']), 1)

    def test_online_backup_includes_committed_target_wal_rows(self):
        writer = SQLiteStore(self.target_db)
        try:
            writer.connection.execute('PRAGMA wal_autocheckpoint=0')
            writer.save([self.prop('R2', PropertySource.REALESTATE)])
            self.assertTrue(Path(str(self.target_db) + '-wal').exists())
            receipt = self.call(apply=True)
            backup_rows = self.rows(Path(receipt['backup']) / self.target_db.name)
            self.assertIn(('R2', 'realestate'), [(row[0], row[1]) for row in backup_rows])
            self.assertIn(('R2', 'realestate'), [(row[0], row[1]) for row in self.rows(self.target_db)])
        finally:
            writer.close()

    def test_cli_defaults_to_dry_run_without_importing_scraper_runtime(self):
        script = Path(__file__).resolve().parents[1] / 'ops' / 'import_domain_capture.py'
        command = [sys.executable, str(script), '--source-state', str(self.source_path),
                   '--source-db', str(self.source_db), '--source-process', str(self.process_path),
                   '--target-output', str(self.output), '--target-db', str(self.target_db)]
        result = subprocess.run(command, cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'dry_run')
        self.assertFalse((self.target_db.parent / 'backup').exists())

    def test_boolean_values_cannot_impersonate_exit_codes_versions_or_byte_counts(self):
        from ops.import_domain_capture import ImportRejected
        source, process = copy.deepcopy(self.source), copy.deepcopy(self.process)
        for kind in ('source_exit', 'process_exit', 'version', 'paid_bytes'):
            with self.subTest(kind=kind):
                self.source, self.process = copy.deepcopy(source), copy.deepcopy(process)
                if kind == 'source_exit': self.source['exit_code'] = False
                elif kind == 'process_exit': self.process['exit_code'] = False
                elif kind == 'version': self.source['version'] = True
                else: self.source['traffic']['proxy_bytes'] = False
                self.flush()
                with self.assertRaises(ImportRejected): self.call()

    def test_failed_database_transaction_leaves_rows_and_checkpoint_unchanged(self):
        before_rows = self.rows(self.target_db)
        before_state = (self.output / 'state.json').read_bytes()
        with sqlite3.connect(self.target_db) as connection:
            connection.execute("CREATE TRIGGER reject_fixture BEFORE INSERT ON properties WHEN NEW.house_id='S2' "
                               "BEGIN SELECT RAISE(ABORT,'fixture refusal'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.call(apply=True)
        self.assertEqual(self.rows(self.target_db), before_rows)
        self.assertEqual((self.output / 'state.json').read_bytes(), before_state)
        self.assertFalse((self.output / 'import-receipt.json').exists())
        self.assertFalse((self.target_db.parent / 'metro-baseline.json').exists())

    def test_checkpoint_write_failure_can_be_reapplied_without_false_completion(self):
        before_state = (self.output / 'state.json').read_bytes()
        with patch('ops.import_domain_capture.atomic_json', side_effect=OSError('fixture disk error')):
            with self.assertRaises(OSError): self.call(apply=True)
        self.assertEqual((self.output / 'state.json').read_bytes(), before_state)
        self.assertFalse((self.output / 'import-receipt.json').exists())
        self.assertFalse((self.target_db.parent / 'metro-baseline.json').exists())
        self.assertEqual(self.call(apply=True)['status'], 'pending_validation')

    def test_rejects_partial_or_paid_source_and_unfinished_or_timed_out_process(self):
        from ops.import_domain_capture import ImportRejected
        cases = [('status', 'partial'), ('complete_inventory', False), ('exit_code', 1),
                 ('errors', ['blocked']), ('queue', [self.roots[0]]), ('mode', 'incremental')]
        original = copy.deepcopy(self.source)
        for key, value in cases:
            with self.subTest(key=key):
                self.source = {**copy.deepcopy(original), key: value}
                self.flush()
                with self.assertRaises(ImportRejected): self.call(apply=True)
        self.source = original
        for changes in ({'state': 'running'}, {'exit_code': 1}, {'timed_out': True}):
            with self.subTest(process=changes):
                self.process = {'state': 'finished', 'exit_code': 0, 'timed_out': False, **changes}
                self.flush()
                with self.assertRaises(ImportRejected): self.call(apply=True)
        self.process = {'state': 'finished', 'exit_code': 0, 'timed_out': False}
        for key in ('proxy_requests', 'proxy_bytes', 'proxy_wire_bytes', 'proxy_connections'):
            with self.subTest(traffic=key):
                self.source = copy.deepcopy(original)
                self.source['traffic'][key] = 1
                self.flush()
                with self.assertRaises(ImportRejected): self.call(apply=True)
        self.assertFalse((self.target_db.parent / 'backup').exists())

    def test_rejects_scope_and_query_identity_cycle_and_coverage_mismatches(self):
        from ops.import_domain_capture import ImportRejected
        original = copy.deepcopy(self.source)
        for kind in ('scope_hash', 'planning_version', 'version', 'cities', 'sources', 'roots', 'query_key',
                     'cycle', 'coverage', 'leaf_coverage', 'leaf_truncated', 'unreachable', 'seen_city', 'paid_host'):
            with self.subTest(kind=kind):
                self.source = copy.deepcopy(original)
                if kind in {'scope_hash', 'planning_version'}: self.source['options'][kind] = 'different'
                elif kind == 'version': self.source['version'] = 2
                elif kind == 'cities': self.source['options']['cities'] = ['sydney']
                elif kind == 'sources': self.source['options']['sources'] = ['domain', 'realestate']
                elif kind == 'roots': self.source['options']['root_queries'] = self.roots[:1]
                elif kind == 'query_key': self.source['queries'][self.roots[0]]['query']['city'] = 'melbourne'
                elif kind == 'cycle': self.source['queries'][self.roots[0]]['children'] = [self.roots[0]]
                elif kind == 'coverage': self.source['queries'][self.roots[0]]['total'] = self.source['queries'][self.roots[0]]['first_total'] = 99
                elif kind == 'leaf_coverage': self.source['queries'][self.roots[1]]['total'] = self.source['queries'][self.roots[1]]['first_total'] = 3
                elif kind == 'leaf_truncated': self.source['queries'][self.roots[1]]['truncated'] = True
                elif kind == 'unreachable': self.source['options']['root_queries'] = [self.roots[1]]
                elif kind == 'paid_host': self.source['traffic']['wire_hosts'] = [{'mode': 'proxy', 'bytes': 1, 'connections': 1}]
                else: self.source['seen']['domain:S1'] = ['domain', 'melbourne']
                self.flush()
                with self.assertRaises(ImportRejected): self.call(apply=True)
        self.assertFalse((self.target_db.parent / 'backup').exists())

    def test_missing_or_misidentified_payload_is_rejected_before_any_write(self):
        from ops.import_domain_capture import ImportRejected
        original_rows = self.rows(self.target_db)
        for kind in ('missing', 'source', 'house_id', 'state'):
            with self.subTest(kind=kind):
                source_store = SQLiteStore(self.source_db)
                source_store.save([self.prop('S1')])
                source_store.close()
                with sqlite3.connect(self.source_db) as connection:
                    if kind == 'missing': connection.execute("DELETE FROM property_enrichment WHERE house_id='S1' AND source='domain'")
                    else:
                        payload = json.loads(connection.execute("SELECT payload FROM property_enrichment WHERE house_id='S1' AND source='domain'").fetchone()[0])
                        payload[kind] = {'source': 'realestate', 'house_id': 'wrong', 'state': 'VIC'}[kind]
                        connection.execute("UPDATE property_enrichment SET payload=? WHERE house_id='S1' AND source='domain'", (json.dumps(payload),))
                with self.assertRaises(ImportRejected): self.call(apply=True)
                self.assertEqual(self.rows(self.target_db), original_rows)
        self.assertFalse((self.target_db.parent / 'backup').exists())

    def test_live_campaign_lock_prevents_apply(self):
        from ops.import_domain_capture import TargetBusy
        with (self.target_db.parent / '.capture.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(TargetBusy): self.call(apply=True)
        self.assertFalse((self.target_db.parent / 'backup').exists())


if __name__ == '__main__':
    unittest.main()

from contextlib import closing
import gzip
import io
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from ops import cold_sqlite_backups as tier


class ColdSqliteBackupTests(unittest.TestCase):
    def setUp(self):
        stage = tempfile.TemporaryDirectory()
        self.addCleanup(stage.cleanup)
        self.stage = Path(stage.name).resolve()
        self.root = self.stage / 'source'
        self.backup = self.root / 'backup'
        self.backup.mkdir(parents=True)
        self.hk = self.stage / 'remote'
        self.hk.mkdir(mode=0o700)
        tier.shared.write_json(self.root / 'cold-archive.json', {'ssh_target': 'root@relay'})
        options = (patch.object(tier, 'DESTINATION', self.hk), patch.object(tier, 'STAGING', self.stage),
                   patch.object(tier, 'available_memory', return_value=1 << 50),
                   patch.object(tier.shared, 'ROOT', self.root), patch.object(tier.shared, 'DESTINATION_RESERVE', 0),
                   patch.object(tier.shared, 'SOURCE_RECEIPT_RESERVE', 0))
        for option in options:
            option.start()
            self.addCleanup(option.stop)
        self.paths = [self.backup / f'properties_202611{day:02d}.db.gz' for day in range(1, 12)]
        self.create_archive(self.paths[0])
        for path in self.paths[1:]:
            shutil.copyfile(self.paths[0], path)
            path.chmod(0o600)
        self.ledger = self.backup / 'enrichment-state' / 'properties_20261101.db.gz'
        self.ledger.parent.mkdir()
        self.ledger.write_bytes(b'ledger snapshot is outside main SQLite scope')

    def create_archive(self, target, *, payload=None):
        database = self.stage / 'original.db'
        database.unlink(missing_ok=True)
        with closing(sqlite3.connect(database)) as db:
            columns = ', '.join(f'{name} TEXT' for name in sorted(tier.PROPERTY_COLUMNS - {'id'}))
            db.execute(f'CREATE TABLE properties (id INTEGER PRIMARY KEY AUTOINCREMENT, {columns})')
            db.execute('CREATE TABLE property_enrichment (house_id TEXT, source TEXT, payload TEXT)')
            db.execute("INSERT INTO properties (house_id, source) VALUES ('123', 'domain')")
            data = {'source': 'domain', 'house_id': '123'} if payload is None else payload
            db.execute('INSERT INTO property_enrichment VALUES (?, ?, ?)', ('123', 'domain', json.dumps(data)))
            db.commit()
        with database.open('rb') as source, gzip.open(target, 'wb') as output:
            shutil.copyfileobj(source, output)
        target.chmod(0o600)

    def transport(self, target, action, name, manifest_sha, *, script, send=None):
        self.assertEqual(target, 'root@relay')
        if action == '--remote-verify':
            return tier.verify_destination(self.hk, name, manifest_sha)
        self.assertEqual(action, '--remote-receive')
        output = io.BytesIO()
        send(output)
        return tier.receive(self.hk, name, manifest_sha, io.BytesIO(output.getvalue()))

    def test_four_old_snapshots_are_recovered_and_only_seven_stay_hot(self):
        before = {path.name: tier.shared.digest(path) for path in self.paths}
        ledger = self.ledger.read_bytes()
        report = {'completed': []}
        with patch.object(tier.shared, 'remote', side_effect=self.transport):
            status = tier.archive_old(self.root, True, 4, report)
        self.assertEqual(status, 'cold_main_sqlite_verified')
        self.assertEqual(tier.local_snapshots(self.root), self.paths[-7:])
        self.assertEqual({path.name: tier.shared.digest(path) for path in self.paths[-7:]},
                         {path.name: before[path.name] for path in self.paths[-7:]})
        self.assertEqual(self.ledger.read_bytes(), ledger)
        self.assertEqual(len(report['completed']), 4)
        for snapshot in report['completed']:
            cold = Path(snapshot['archive'])
            self.assertEqual(tier.shared.digest(cold), before[snapshot['snapshot']])
            self.assertEqual(tier.verify_destination(self.hk, snapshot['snapshot'],
                                                     snapshot['source_manifest_sha256'])['quick_check'], 'ok')
            self.assertTrue(Path(snapshot['source_receipt']).is_file())

    def test_non_public_payload_and_insufficient_remote_capacity_leave_hot(self):
        self.create_archive(self.paths[0], payload={'user_email': 'fixture'})
        with patch.object(tier.shared, 'remote') as transfer:
            with self.assertRaisesRegex(tier.shared.ArchiveError, 'payload fields'):
                tier.archive_old(self.root, True, 1, {'completed': []})
        transfer.assert_not_called()
        self.create_archive(self.paths[0])
        with patch.object(tier.shared, 'DESTINATION_RESERVE', 1 << 60), \
                patch.object(tier.shared, 'remote', side_effect=self.transport):
            with self.assertRaisesRegex(tier.shared.ArchiveError, 'Insufficient Hong Kong capacity'):
                tier.archive_old(self.root, True, 1, {'completed': []})
        self.assertEqual(tier.local_snapshots(self.root), self.paths)
        self.assertEqual(list(self.hk.iterdir()), [])

    def test_corrupt_cold_readback_prevents_local_release(self):
        def corrupt(*args, **kwargs):
            proof = self.transport(*args, **kwargs)
            if args[1] == '--remote-receive':
                with Path(proof['archive']).open('ab') as output:
                    output.write(b'damaged')
            return proof

        with patch.object(tier.shared, 'remote', side_effect=corrupt):
            with self.assertRaisesRegex(tier.shared.ArchiveError, 'receipt or gzip SHA differs'):
                tier.archive_old(self.root, True, 1, {'completed': []})
        self.assertTrue(self.paths[0].is_file())
        self.assertFalse((self.backup / 'main-cold-receipts').exists())

    def test_capture_lock_prevents_release_after_successful_remote_archive(self):
        with tier.shared.lock(self.root / '.capture.lock', create=True):
            with patch.object(tier.shared, 'remote', side_effect=self.transport):
                with self.assertRaises(BlockingIOError):
                    tier.archive_old(self.root, True, 1, {'completed': []})
        self.assertTrue(self.paths[0].is_file())
        with patch.object(tier.shared, 'remote', side_effect=self.transport) as transfer:
            self.assertEqual(tier.archive_old(self.root, True, 1, {'completed': []}), 'cold_main_sqlite_verified')
        self.assertEqual([call.args[1] for call in transfer.call_args_list], ['--remote-verify'])


if __name__ == '__main__':
    unittest.main()

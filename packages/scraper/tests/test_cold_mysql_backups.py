from datetime import timedelta
import gzip
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ops import cold_mysql_backups as archive


class ColdMysqlBackupTests(unittest.TestCase):
    def setUp(self):
        stage = tempfile.TemporaryDirectory()
        self.addCleanup(stage.cleanup)
        stage = Path(stage.name).resolve()
        self.root = stage / 'crawler'
        self.hk = stage / 'hong-kong'
        self.root.mkdir()
        self.hk.mkdir(mode=0o700)
        self.put(self.root / 'cold-archive.json', {'ssh_target': 'archiver@approved'})
        for option, value in (('SOURCE_WATERMARK', 1 << 60), ('SOURCE_RECEIPT_RESERVE', 0),
                              ('DESTINATION', self.hk), ('DESTINATION_RESERVE', 0)):
            change = patch.object(archive, option, value)
            change.start()
            self.addCleanup(change.stop)
        self.old = self.queue('20261031_020000_000000', timedelta(days=7), batches=2)
        self.latest = self.queue('20261106_020000_000000', timedelta(days=3))

    @staticmethod
    def put(path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
        path.chmod(0o600)

    def queue(self, run, age, *, batches=1):
        base = self.root / 'output' / ('school-enrichment-' + run)
        at = (archive.now() - age).isoformat()
        total = {'addresses': batches, 'published_addresses': batches}
        self.put(base / 'job.json', {'output': str(base / 'queue')})
        self.put(base / 'prepare-receipt.json', {'status': 'prepared'})
        self.put(base / 'daily-receipt.json', {'status': 'processed', 'capture_run': run, 'completed_at': at, **total})
        self.put(base / 'queue/progress.json', {'total_addresses': batches, 'processed_addresses': batches,
                                                'published_addresses': batches, 'held_addresses': 0})
        self.put(base / 'queue/result.json', {'status': 'processed', 'completed_at': at, 'batch_count': batches,
                                              'held_addresses': 0, **total})
        (base / 'queue/.queue.lock').touch()
        for number in range(1, batches + 1):
            batch = base / 'queue' / f'batch-{number:04d}'
            self.put(batch / 'receipt.json', {'status': 'processed', 'published_addresses': 1})
            self.put(batch / 'audit/result.json', {'status': 'audit_passed'})
            self.backup(batch, f'MYSQL{number}')
        return base

    def backup(self, batch, name, *, private_column=False):
        target = batch / 'publish' / f'mysql-before-{name}.jsonl.gz'
        target.parent.mkdir(exist_ok=True)
        counts = dict.fromkeys(archive.TABLES, 1)
        records = [{'kind': 'header', 'format': 'qrent-mysql-jsonl-v1'}]
        for table in sorted(counts):
            column = 'user_email' if private_column and table == 'properties' else 'id'
            records.extend(({'kind': 'table', 'name': table, 'columns': [column]},
                            {'kind': 'row', 'table': table, 'values': [1]},
                            {'kind': 'table_end', 'name': table, 'count': 1}))
        records.append({'kind': 'end', 'counts': counts})
        with gzip.open(target, 'wt') as stream:
            for record in records:
                stream.write(json.dumps(record) + '\n')
        target.chmod(0o600)
        self.put(target.parent / 'result.json', {'status': 'published', 'backup': {
            'status': 'read_only_archive_verified', 'path': str(target), 'sha256': archive.digest(target),
            'bytes': target.stat().st_size, 'counts': counts}})
        return target

    def transport(self, target, action, run, manifest_sha, *, base=None, manifest=None):
        self.assertEqual(target, 'archiver@approved')
        if action == '--remote-verify':
            return archive.verify_destination(self.hk, run, manifest_sha)
        self.assertEqual(action, '--remote-receive')
        buffer = io.BytesIO()
        archive.send_tar(base, manifest, buffer)
        return archive.receive(self.hk, run, manifest_sha, io.BytesIO(buffer.getvalue()))

    def archive_old(self):
        with patch.object(archive, 'remote', side_effect=self.transport):
            return archive.archive_once(self.root, True, {})

    def test_completed_old_queue_archives_with_independent_restore_and_preserves_latest(self):
        old_paths = sorted(self.old.glob('queue/batch-*/publish/*.gz'))
        original = {str(path.relative_to(self.old)): archive.digest(path) for path in old_paths}
        audit = self.old / 'queue/batch-0001/audit/result.json'
        audit_hash = archive.digest(audit)
        self.assertEqual(archive.eligible(self.root, archive.now()), [self.old])
        receipt = self.archive_old()
        self.assertEqual(receipt['hot_files_removed'], len(original))
        self.assertEqual(receipt['protected_receipts_unchanged'], 9)
        self.assertTrue(all(not path.exists() for path in old_paths))
        self.assertEqual(archive.digest(audit), audit_hash)
        self.assertEqual(len(list(self.latest.glob('queue/batch-*/publish/*.gz'))), 1)
        marker = archive.read_json(self.old / archive.MARKER)
        self.assertEqual({item['path']: item['sha256'] for item in marker['verified_files']}, original)
        self.assertEqual(archive.digest(Path(receipt['archive'])), receipt['archive_sha256'])
        archive.unpack_and_verify(Path(receipt['archive']), self.hk, marker['verified_files'])
        self.assertEqual(archive.eligible(self.root, archive.now()), [])

    def test_invalid_private_column_prevents_any_transfer_or_release(self):
        self.backup(self.old / 'queue/batch-0001', 'MYSQL1', private_column=True)
        with patch.object(archive, 'remote') as transfer:
            with self.assertRaisesRegex(archive.ArchiveError, 'table or column'):
                archive.archive_once(self.root, True, {})
        transfer.assert_not_called()
        self.assertEqual(len(list(self.old.glob('queue/batch-*/publish/*.gz'))), 2)
        self.assertFalse((self.old / archive.MARKER).exists())

    def test_truncated_stream_never_commits_archive(self):
        manifest = archive.snapshot(self.old, full_scan=True)
        manifest_sha = hashlib.sha256(archive.canonical(manifest) + b'\n').hexdigest()
        stream = io.BytesIO()
        archive.send_tar(self.old, manifest, stream)
        with self.assertRaisesRegex(archive.ArchiveError, 'truncated'):
            archive.receive(self.hk, manifest['source_run'], manifest_sha, io.BytesIO(stream.getvalue()[:-1]))
        self.assertEqual(list(self.hk.iterdir()), [])
        self.assertEqual(len(list(self.old.glob('queue/batch-*/publish/*.gz'))), 2)

    def test_low_destination_reserve_and_capture_lock_leave_hot_files(self):
        with patch.object(archive, 'DESTINATION_RESERVE', 1 << 60):
            with self.assertRaisesRegex(archive.ArchiveError, 'insufficient reserve'):
                self.archive_old()
        self.assertFalse((self.old / archive.MARKER).exists())
        self.assertEqual(len(list(self.old.glob('queue/batch-*/publish/*.gz'))), 2)
        with archive.lock(self.root / '.capture.lock', create=True):
            with self.assertRaises(BlockingIOError):
                self.archive_old()
        self.assertFalse((self.old / archive.MARKER).exists())
        self.assertEqual(len(list(self.old.glob('queue/batch-*/publish/*.gz'))), 2)
        self.assertEqual(self.archive_old()['hot_files_removed'], 2)

    def test_partial_release_resumes_with_fresh_cold_sha(self):
        original = Path.unlink
        last = self.old / 'queue/batch-0002/publish/mysql-before-MYSQL2.jsonl.gz'

        def fail_second(path, *args, **kwargs):
            if path == last:
                raise PermissionError('interrupted release')
            return original(path, *args, **kwargs)

        with patch.object(Path, 'unlink', fail_second):
            with self.assertRaises(PermissionError):
                self.archive_old()
        marker = archive.read_json(self.old / archive.MARKER)
        remote = Path(marker['archive'])
        with remote.open('r+b') as output:
            first_byte = output.read(1)
            output.seek(0)
            output.write(bytes([first_byte[0] ^ 1]))
        with self.assertRaisesRegex(archive.ArchiveError, 'SHA differs'):
            self.archive_old()
        self.assertTrue(last.is_file())
        with remote.open('r+b') as output:
            output.write(first_byte)
        result = self.archive_old()
        self.assertEqual((result['hot_files_removed'], result['already_cold_files']), (1, 1))
        self.assertFalse(last.exists())


if __name__ == '__main__':
    unittest.main()

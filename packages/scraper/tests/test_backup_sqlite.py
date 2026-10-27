import gzip
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / 'ops' / 'backup_sqlite.py'
sys.path.insert(0, str(SCRIPT.parent.parent))
from ops import backup_sqlite


class BackupSqliteTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.database = self.root / 'live.sqlite'
        self.directory = self.root / 'backups'

    def database_with_wal(self):
        writer = sqlite3.connect(self.database)
        self.addCleanup(writer.close)
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute('PRAGMA wal_autocheckpoint=0')
        writer.execute('CREATE TABLE records (id INTEGER PRIMARY KEY, payload TEXT CHECK(length(payload)>0))')
        writer.execute("INSERT INTO records(payload) VALUES ('checkpointed')")
        writer.commit()
        writer.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        writer.execute("INSERT INTO records(payload) VALUES ('committed in WAL')")
        writer.commit()
        self.assertGreater(Path(str(self.database) + '-wal').stat().st_size, 32)
        return writer

    def run_cli(self, *options):
        return subprocess.run([sys.executable, str(SCRIPT), str(self.database), str(self.directory), *options],
                              text=True, capture_output=True, timeout=20)

    def assert_database(self, path, expected_rows=2):
        connection = sqlite3.connect(f'{path.resolve().as_uri()}?mode=ro&immutable=1', uri=True)
        try:
            self.assertEqual(connection.execute('PRAGMA quick_check').fetchall(), [('ok',)])
            self.assertEqual(connection.execute('SELECT count(*) FROM records').fetchone()[0], expected_rows)
        finally:
            connection.close()

    def test_gzip_cli_roundtrip_includes_committed_wal_and_private_permissions(self):
        self.database_with_wal()
        self.assert_database(self.database, expected_rows=1)
        result = self.run_cli('--gzip')
        self.assertEqual(result.returncode, 0, result.stderr)
        target = Path(result.stdout.strip())
        self.assertEqual(target.parent, self.directory)
        self.assertTrue(target.name.startswith('properties_'))
        self.assertTrue(target.name.endswith('.db.gz'))
        restored = self.root / 'restored.db'
        with gzip.open(target, 'rb') as source, restored.open('wb') as destination:
            while block := source.read(65536):
                destination.write(block)
        self.assert_database(restored)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        self.assertEqual(list(self.directory.iterdir()), [target])

    def test_default_cli_still_creates_a_plain_db_including_wal(self):
        self.database_with_wal()
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        target = Path(result.stdout.strip())
        self.assertTrue(target.name.endswith('.db'))
        self.assert_database(target)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        self.assertEqual(list(self.directory.iterdir()), [target])

    def test_failed_quick_check_preserves_the_previous_backup_and_cleans_temporaries(self):
        writer = self.database_with_wal()
        for options in ((), ('--gzip',)):
            with self.subTest(options=options):
                good = self.run_cli(*options)
                self.assertEqual(good.returncode, 0, good.stderr)
                target = Path(good.stdout.strip())
                original = target.read_bytes()
                writer.execute('PRAGMA ignore_check_constraints=ON')
                writer.execute("INSERT INTO records(payload) VALUES ('')")
                writer.commit()
                failed = self.run_cli(*options)
                self.assertNotEqual(failed.returncode, 0)
                self.assertIn('quick_check', failed.stderr)
                self.assertEqual(target.read_bytes(), original)
                self.assertFalse(any(path.name.startswith('.') for path in self.directory.iterdir()))
                writer.execute("DELETE FROM records WHERE payload='' ")
                writer.commit()

    def test_stream_failure_preserves_old_gzip_and_cleans_partial_files(self):
        writer = self.database_with_wal()
        target = backup_sqlite.backup_database(self.database, self.directory, compress=True)
        original = target.read_bytes()
        writer.execute("INSERT INTO records(payload) VALUES ('new transaction')")
        writer.commit()

        def interrupted_copy(source, destination, **options):
            destination.write(source.read(100))
            raise OSError('simulated compression write failure')

        with patch.object(backup_sqlite.shutil, 'copyfileobj', side_effect=interrupted_copy):
            with self.assertRaisesRegex(OSError, 'simulated compression'):
                backup_sqlite.backup_database(self.database, self.directory, compress=True)
        self.assertEqual(target.read_bytes(), original)
        self.assertEqual(list(self.directory.iterdir()), [target])

    def test_atomic_publish_failure_preserves_old_backup_and_cleans_temporaries(self):
        writer = self.database_with_wal()
        for compress in (False, True):
            directory = self.directory / str(compress)
            target = backup_sqlite.backup_database(self.database, directory, compress=compress)
            original = target.read_bytes()

            def cannot_publish(source, destination):
                self.assertEqual(Path(source).parent, target.parent)
                self.assertEqual(Path(destination), target)
                self.assertEqual(target.read_bytes(), original)
                self.assertEqual(stat.S_IMODE(Path(source).stat().st_mode), 0o600)
                raise OSError('simulated atomic replace failure')

            with self.subTest(compress=compress), patch.object(backup_sqlite.os, 'replace', side_effect=cannot_publish):
                with self.assertRaisesRegex(OSError, 'simulated atomic replace'):
                    backup_sqlite.backup_database(self.database, directory, compress=compress)
            self.assertEqual(target.read_bytes(), original)
            self.assertEqual(list(directory.iterdir()), [target])

    def test_missing_source_is_not_created_and_leaves_no_temporary_files(self):
        result = self.run_cli('--gzip')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.database.exists())
        self.assertEqual(list(self.directory.iterdir()), [])


if __name__ == '__main__':
    unittest.main()

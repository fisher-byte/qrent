import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ops.backup_mysql import config_from_env


class MysqlConfigTests(unittest.TestCase):
    def test_explicit_writer_file_overrides_inherited_database_credentials(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'writer.env'
            path.write_text('DATABASE_URL=\nDB_HOST=writer-host\nDB_USER=qrent_writer\n'
                            'DB_PASSWORD=writer-secret\nDB_DATABASE=qrent\n')
            with patch.dict(os.environ, {'DATABASE_URL':'mysql://root:old@old-host/old-db',
                                        'DB_HOST':'old-host','DB_USER':'root'}, clear=True):
                config = config_from_env(path)
            self.assertEqual(config['host'], 'writer-host')
            self.assertEqual(config['user'], 'qrent_writer')
            self.assertEqual(config['password'], 'writer-secret')
            self.assertEqual(config['database'], 'qrent')

    def test_process_environment_remains_fallback_for_missing_file_fields(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'database.env'
            path.write_text('DB_USER=file-user\n')
            with patch.dict(os.environ, {'DB_HOST':'env-host','DB_USER':'env-user',
                                        'DB_PASSWORD':'env-secret'}, clear=True):
                config = config_from_env(path)
            self.assertEqual(config['host'], 'env-host')
            self.assertEqual(config['user'], 'file-user')


if __name__ == '__main__':
    unittest.main()

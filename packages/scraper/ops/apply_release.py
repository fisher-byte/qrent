#!/usr/bin/env python3
"""Apply a hash-checked source bundle under the crawler lock; roll back on test failure."""
import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tarfile
from datetime import datetime
from pathlib import Path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def offline_test_environment(path):
    # Legacy modules also load .env directly. Explicit defaults prevent those
    # imports from selecting a different SDK branch or restoring a live key.
    values={name:'' for name in ('GOOGLE_MAPS_API_KEY','NSW_API_KEY','DASHSCOPE_API_KEY',
        'PROPERTY_RATING_API_KEY','QWEN_API_KEY','DISTANCE_MATRIX_API_KEY')}
    values.update(COMMUTE_PROVIDER='google',QWEN_MODEL='qwen-plus',
                  DASHSCOPE_HTTP_BASE_URL='https://dashscope.aliyuncs.com/api/v1',
                  CAMOUFOX_ASSET_PREFLIGHT='false',DOMAIN_BROWSER_PROXY='',
                  DOMAIN_PROXY_IP='',DOMAIN_DETAIL_TRANSPORT='')
    path.write_text('\n'.join(k+'='+v for k,v in values.items())+'\n')
    return dict(os.environ,SCRAPER_ENV_FILE=str(path),**values)


def apply(archive, expected_sha, root):
    os.umask(0o077)
    release = root / 'releases/recovery-20261027'
    assert digest(archive) == expected_sha, 'Bundle hash mismatch'
    with (root / '.capture.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with tarfile.open(archive) as tar:
            manifest = json.load(tar.extractfile('release-manifest.json'))
            files = {}
            for name, metadata in manifest['files'].items():
                path = Path(name)
                assert not path.is_absolute() and '..' not in path.parts and '.env' not in path.parts
                data = tar.extractfile(name).read()
                assert hashlib.sha256(data).hexdigest() == metadata['sha256'], name
                assert digest(release / name) == metadata['previous_sha256'], (name, 'source changed since packaging')
                files[name] = data
        backup = root / 'backup' / ('pre-source-' + datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
        backup.mkdir(parents=True)
        for name in [*files, 'DEPLOYMENT.json']:
            source = release / name
            if source.exists():
                target = backup / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
        with sqlite3.connect(root / 'properties.db') as source, sqlite3.connect(backup / 'properties.db') as target:
            source.backup(target)
            assert target.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
        try:
            for name, data in files.items():
                dest = release / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                temp = dest.with_suffix(dest.suffix + '.update')
                temp.write_bytes(data)
                temp.replace(dest)
            # Acceptance tests must not inherit live credentials or consume
            # model/map allowance. The active private environment is untouched.
            offline = backup / 'offline-test.env'
            environment = offline_test_environment(offline)
            with (backup / 'tests.log').open('w') as log:
                tests = subprocess.run([str(release / '.venv/bin/python'), '-m', 'unittest', 'discover', '-s', 'tests', '-v'],
                    cwd=release, env=environment, stdout=log, stderr=subprocess.STDOUT, timeout=240)
            text = (backup / 'tests.log').read_text()
            count = re.search(r'Ran (\d+) tests?', text)
            assert tests.returncode == 0 and count and int(count.group(1)) > 0, 'Server tests failed'
            for script in (release / 'ops').glob('*.sh'):
                subprocess.run(['bash', '-n', str(script)], check=True, timeout=10)
            deployment_path = release / 'DEPLOYMENT.json'
            deployment = json.loads(deployment_path.read_text())
            hashes = {**deployment.get('files_sha256', {}), **deployment.get('file_hashes', {})}
            hashes.update({name: value['sha256'] for name, value in manifest['files'].items()})
            deployment.update(repository='fisher-byte/qrent', source_commit=manifest['source_commit'],
                working_tree=manifest['working_tree'], files_sha256=hashes, tests_passed=int(count.group(1)),
                source_deployed_at=datetime.now().isoformat(), latest_acceptance='pending')
            deployment.pop('file_hashes', None)
            deployment_path.write_text(json.dumps(deployment, indent=2))
            receipt = {'status': 'deployed_tests_passed', 'tests': int(count.group(1)), 'backup': str(backup),
                       'source_commit': manifest['source_commit'], 'changed_files': len(files),
                       'bundle_sha256': expected_sha}
            (backup / 'receipt.json').write_text(json.dumps(receipt, indent=2))
            print(json.dumps(receipt))
            return receipt
        except BaseException:
            for name, metadata in manifest['files'].items():
                if metadata['previous_sha256'] is None:
                    (release / name).unlink(missing_ok=True)
                else:
                    shutil.copy2(backup / name, release / name)
            if (backup / 'DEPLOYMENT.json').exists():
                shutil.copy2(backup / 'DEPLOYMENT.json', release / 'DEPLOYMENT.json')
            print('Source rolled back; database and private configuration preserved. Backup:', backup)
            raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--root', type=Path, default=Path('/root/qrent-scraper'))
    args = parser.parse_args()
    apply(args.archive, args.sha256, args.root)

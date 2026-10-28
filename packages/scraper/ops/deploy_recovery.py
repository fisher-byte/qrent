#!/usr/bin/env python3
"""Run local checks, upload only source changes over SSH, and verify server tests."""
import argparse
import hashlib
import io
import json
import shlex
import subprocess
import sys
import tarfile
from datetime import datetime
from pathlib import Path


def remote(host, arguments, text=None):
    return subprocess.run(['ssh', host, shlex.join(arguments)], input=text, text=True,
                          capture_output=True, check=True)


def deploy(host, root):
    scraper = Path(__file__).resolve().parents[1]
    repository = scraper.parents[1]
    python = repository / '.venv/bin/python'
    subprocess.run([str(python) if python.exists() else sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'],
                   cwd=scraper, check=True)
    for script in (scraper / 'ops').glob('*.sh'):
        subprocess.run(['bash', '-n', str(script)], check=True)
    paths = [scraper / 'recovery_runner.py', scraper / 'metro_runner.py', scraper / 'enrichment_runner.py',
             scraper / 'src/config/metro_regions.json']
    paths.extend(scraper.glob('requirements*.txt'))
    for folder in ('src', 'tests', 'ops'):
        paths.extend((scraper / folder).rglob('*.py'))
        paths.extend((scraper / folder).rglob('*.sh'))
    sources = {str(path.relative_to(scraper)): path.read_bytes() for path in paths if path.is_file()}
    read_script = '''from pathlib import Path
import json,sys,hashlib
root=Path(sys.argv[1]); release=root/'releases/recovery-20261027'
deployment=json.loads((release/'DEPLOYMENT.json').read_text())
names=json.load(sys.stdin)
actual={name:hashlib.sha256((release/name).read_bytes()).hexdigest() if (release/name).exists() else None for name in names}
print(json.dumps({'actual':actual,'recorded':{**deployment.get('files_sha256',{}),**deployment.get('file_hashes',{})}}))
'''
    previous = json.loads(remote(host, ['python3', '-c', read_script, root], json.dumps(list(sources))).stdout)
    changed = {}
    for name, data in sources.items():
        new = hashlib.sha256(data).hexdigest()
        actual = previous['actual'][name]
        recorded = previous['recorded'].get(name)
        if actual != recorded and actual != new:
            raise RuntimeError('Unrecorded server change: ' + name)
        if new != actual:
            changed[name] = {'sha256': new, 'previous_sha256': actual}
    if not changed:
        print(json.dumps({'status': 'already_current'}))
        return
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repository, text=True).strip()
    dirty = bool(subprocess.check_output(['git', 'status', '--porcelain', '--', 'packages/scraper'], cwd=repository))
    manifest = {'source_commit': commit, 'working_tree': dirty, 'files': changed}
    output = scraper / 'output/deployments' / datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    output.mkdir(parents=True)
    archive = output / 'source-update.tgz'
    with tarfile.open(archive, 'w:gz') as tar:
        for name in [*changed, 'release-manifest.json']:
            data = json.dumps(manifest, indent=2).encode() if name == 'release-manifest.json' else sources[name]
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o600
            tar.addfile(info, io.BytesIO(data))
    sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    target = root + '/tmp/' + output.name + '.tgz'
    remote(host, ['mkdir', '-p', root + '/tmp'])
    subprocess.run(['scp', '-q', str(archive), host + ':' + target], check=True)
    # The applier is sent via encrypted stdin; no CI credentials or public upload endpoint.
    result = remote(host, ['python3', '-', target, '--sha256', sha, '--root', root],
                    (scraper / 'ops/apply_release.py').read_text())
    (output / 'server-receipt.json').write_text(result.stdout)
    (output / 'release-manifest.json').write_text(json.dumps(manifest, indent=2))
    remote(host, ['python3', '-c', 'from pathlib import Path; import sys; Path(sys.argv[1]).unlink()', target])
    print(result.stdout.strip())
    print('Receipt:', output / 'server-receipt.json')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='qrent-prod', help='Existing SSH config alias')
    parser.add_argument('--root', default='/root/qrent-scraper')
    args = parser.parse_args()
    deploy(args.host, args.root)

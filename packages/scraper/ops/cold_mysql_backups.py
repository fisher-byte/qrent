#!/usr/bin/env python3
"""Archive completed school queues' verified public MySQL rollback files."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile


MIB = 1024 ** 2
SOURCE_WATERMARK = 2048 * MIB
SOURCE_RECEIPT_RESERVE = 32 * MIB
DESTINATION_RESERVE = 2048 * MIB
MAX_QUEUE_BYTES = 512 * MIB
MAX_MANIFEST_BYTES = MIB
MIN_AGE = timedelta(hours=48)
ROOT = Path('/root/qrent-scraper')
DESTINATION = Path('/root/qrent-cold-archive')
RUN = re.compile(r'\d{8}_\d{6}_\d{6}')
BACKUP = re.compile(r'queue/batch-\d{4}/publish/mysql-before-[A-Z0-9]+\.jsonl\.gz')
SHA256 = re.compile(r'[a-f0-9]{64}')
TABLES = {'properties', 'property_images', 'property_school', 'regions', 'schools'}
PRIVATE_COLUMN = re.compile(r'(?:^|_)(?:user|account|password|secret|token|credential|email|phone|favorite)(?:_|$)', re.I)
PRIVATE_CONTENT = re.compile(r'-----BEGIN [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----|\bsk-(?:proj-)?[A-Za-z0-9_-]{40,}|'
                             r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b|\bgh[pousr]_[A-Za-z0-9]{30,}')
TASK_FILES = ('job.json', 'prepare-receipt.json', 'daily-receipt.json', 'queue/progress.json', 'queue/result.json')
MARKER = 'mysql-backups-cold-manifest.json'
RELEASE = 'mysql-backups-hot-release-receipt.json'
ARCHIVE = 'mysql-prewrite-backups.tar'
MANIFEST = 'source-manifest.json'
RECOVERY = 'archive-recovery-receipt.json'


class ArchiveError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise ArchiveError(message)


def now():
    return datetime.now(timezone.utc)


def canonical(record):
    return json.dumps(record, sort_keys=True, separators=(',', ':')).encode()


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_json(path, record):
    fd, name = tempfile.mkstemp(prefix='.archive-receipt-', dir=path.parent)
    candidate = Path(name)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(canonical(record) + b'\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(candidate, path)
        sync_directory(path.parent)
    finally:
        candidate.unlink(missing_ok=True)


def private_path(path, mode):
    return (path.resolve() == path and path.stat().st_uid == os.geteuid()
            and stat.S_IMODE(path.stat().st_mode) == mode)


@contextmanager
def lock(path, *, create=False):
    flags = os.O_RDWR | os.O_NOFOLLOW | (os.O_CREAT if create else 0)
    with os.fdopen(os.open(path, flags, 0o600), 'r+b') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def read_json(path):
    require(path.resolve() == path and path.is_file(), f'Missing or linked receipt {path.name}')
    result = json.loads(path.read_bytes())
    require(isinstance(result, dict), f'Invalid receipt {path.name}')
    return result


def completed(base, run):
    daily, progress, queue = (read_json(base / name) for name in TASK_FILES[2:])
    require(daily['capture_run'] == run and daily['status'] in {'processed', 'processed_with_held_records'}
            and queue['status'] == 'processed' and 1 <= queue['batch_count'] <= 200
            and daily['addresses'] == queue['addresses'] == progress['total_addresses'] == progress['processed_addresses']
            and daily['published_addresses'] == queue['published_addresses'] == progress['published_addresses']
            and queue['held_addresses'] == progress['held_addresses']
            and queue['addresses'] == queue['published_addresses'] + queue['held_addresses'],
            'Queue completion receipts disagree')
    finished = datetime.fromisoformat(daily['completed_at'])
    queue_finished = datetime.fromisoformat(queue['completed_at'])
    require(finished.tzinfo is not None and queue_finished.tzinfo is not None and finished >= queue_finished,
            'Queue completion time is invalid')
    return finished, queue


def eligible(root, at):
    directories = sorted(path for path in (root / 'output').glob('school-enrichment-*')
                         if RUN.fullmatch(path.name.removeprefix('school-enrichment-'))
                         and path.is_dir() and path.resolve() == path)
    finished = []
    for base in directories:
        run = base.name.removeprefix('school-enrichment-')
        try:
            ended, queue = completed(base, run)
        except (ArchiveError, OSError, KeyError, TypeError, ValueError):
            continue
        finished.append((base, ended, queue))
    candidates = []
    for base, ended, queue in finished[:-1]:  # Keep the latest completed run, even if the newest run failed.
        if base == directories[-1] or at - ended < MIN_AGE or not queue['published_addresses']:
            continue
        if (base / RELEASE).exists() or (base / RELEASE).is_symlink():
            receipt, marker = read_json(base / RELEASE), read_json(base / MARKER)
            require(receipt['status'] == 'verified_hot_copies_released'
                    and marker['status'] == 'verified_cold_archive'
                    and receipt['source_run'] == marker['source_run'] == base.name.removeprefix('school-enrichment-')
                    and receipt['archive_sha256'] == marker['archive_sha256']
                    and receipt['source_manifest_sha256'] == marker['source_manifest_sha256'],
                    'Existing cold release receipts disagree')
            continue
        candidates.append(base)
    return sorted(candidates, key=lambda base: (not (base / MARKER).exists(), base.name))


def validate_payload(path, expected):
    counts, columns, closed, footer = {}, {}, set(), None
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        header = json.loads(next(stream, ''))
        require(header.get('kind') == 'header' and header.get('format') == 'qrent-mysql-jsonl-v1'
                and set(header) <= {'kind', 'format', 'created_at'},
                'Unexpected MySQL backup format')
        for line in stream:
            require(footer is None and not PRIVATE_CONTENT.search(line), 'Out-of-scope MySQL archive content')
            item = json.loads(line)
            kind = item['kind']
            if kind == 'table':
                name = item['name']
                require(name in TABLES and name not in columns
                        and not any(PRIVATE_COLUMN.search(column) for column in item['columns']),
                        'Out-of-scope MySQL backup table or column')
                columns[name], counts[name] = len(item['columns']), 0
            elif kind == 'row':
                name = item['table']
                require(name in columns and name not in closed and len(item['values']) == columns[name],
                        'Malformed MySQL backup row')
                counts[name] += 1
            elif kind == 'table_end':
                name = item['name']
                require(name in columns and name not in closed and item['count'] == counts[name],
                        'Incomplete MySQL backup table')
                closed.add(name)
            elif kind == 'end':
                footer = item['counts']
            else:
                raise ArchiveError('Unexpected MySQL backup record')
    require(closed == TABLES and counts == footer == expected, 'MySQL backup footer counts differ')


def snapshot(base, *, full_scan, prior=None):
    run = base.name.removeprefix('school-enrichment-')
    ended, queue = completed(base, run)
    require(read_json(base / 'job.json')['output'] == str(base / 'queue'),
            'Frozen job does not reference this completed queue')
    protected = {}
    for name in TASK_FILES:
        read_json(base / name)
        protected[name] = digest(base / name)
    files, published = [], 0
    for number in range(1, queue['batch_count'] + 1):
        batch = base / 'queue' / f'batch-{number:04d}'
        batch_receipt = batch / 'receipt.json'
        batch_result = read_json(batch_receipt)
        require(batch_result['status'] == 'processed', 'Unfinished publication batch')
        protected[batch_receipt.relative_to(base).as_posix()] = digest(batch_receipt)
        published += batch_result['published_addresses']
        if not batch_result['published_addresses']:
            continue
        proof_path = batch / 'publish/result.json'
        proof = read_json(proof_path)
        backup = proof['backup']
        path = Path(backup['path'])
        name = path.relative_to(base).as_posix()
        require(proof['status'] == 'published' and backup['status'] == 'read_only_archive_verified'
                and path.parent == proof_path.parent and BACKUP.fullmatch(name)
                and set(backup['counts']) == TABLES and SHA256.fullmatch(backup['sha256']),
                'Publication backup is outside the verified path or table scope')
        protected[proof_path.relative_to(base).as_posix()] = digest(proof_path)
        files.append({'path': name, 'sha256': backup['sha256'], 'bytes': backup['bytes'], 'counts': backup['counts']})
        if path.is_file():
            require(private_path(path, 0o600) and path.stat().st_nlink == 1
                    and path.stat().st_size == backup['bytes'] and digest(path) == backup['sha256'],
                    'A hot backup differs from its original publication receipt')
            if full_scan:
                validate_payload(path, backup['counts'])
        else:
            require(prior is not None and not path.is_symlink(), 'A verified hot backup is missing without a cold marker')
    found = {path.relative_to(base).as_posix() for path in base.glob('queue/batch-*/publish/mysql-before-*.jsonl.gz')}
    require(files and published == queue['published_addresses'] and found <= {item['path'] for item in files},
            'Publication backup inventory does not match the completed queue')
    return {'version': 1, 'source_run': run, 'source_root': str(base), 'completed_at': ended.isoformat(),
            'files': files, 'protected': protected}


def validate_manifest(manifest, run):
    files = manifest['files']
    require(RUN.fullmatch(run) and manifest['source_run'] == run and manifest['version'] == 1
            and isinstance(files, list) and 1 <= len(files) <= 200
            and len({item['path'] for item in files}) == len(files),
            'Invalid cold archive manifest')
    for item in files:
        require(BACKUP.fullmatch(item['path']) and len(item['path']) <= 100 and SHA256.fullmatch(item['sha256'])
                and set(item['counts']) == TABLES
                and all(isinstance(count, int) and count >= 0 for count in item['counts'].values())
                and isinstance(item['bytes'], int) and item['bytes'] > 0,
                'Invalid archive member')
    total = sum(item['bytes'] for item in files)
    require(total <= MAX_QUEUE_BYTES, 'Queue exceeds the cold archive transfer cap')
    return total


def tar_size(files):
    block, record = tarfile.BLOCKSIZE, tarfile.RECORDSIZE
    blocks = 2 + sum(1 + (item['bytes'] + block - 1) // block for item in files)
    return (blocks * block + record - 1) // record * record


def unpack_and_verify(archive, stage, files):
    expected = {item['path']: item for item in files}
    with tempfile.TemporaryDirectory(prefix='restore-check-', dir=stage) as scratch, tarfile.open(archive, 'r:') as tar:
        members = tar.getmembers()
        require(len(members) == len(expected) and {entry.name for entry in members} == set(expected),
                'Archive entries differ from the source manifest')
        for entry in members:
            proof = expected[entry.name]
            require(entry.isfile() and entry.size == proof['bytes'], 'Unsafe or truncated archive member')
            target = Path(scratch) / entry.name
            target.parent.mkdir(parents=True, exist_ok=True)
            with tar.extractfile(entry) as source, target.open('xb') as output:
                shutil.copyfileobj(source, output)
            require(digest(target) == proof['sha256'], 'Unpacked backup SHA differs')
            validate_payload(target, proof['counts'])


def receive(root, run, manifest_sha, stream):
    data = stream.readline(MAX_MANIFEST_BYTES + 1)
    require(len(data) <= MAX_MANIFEST_BYTES and data.endswith(b'\n')
            and hashlib.sha256(data).hexdigest() == manifest_sha, 'Transferred manifest SHA differs')
    manifest = json.loads(data)
    total = validate_manifest(manifest, run)
    final = root / run
    require(not final.exists() and not final.is_symlink(), 'A cold archive already exists')
    expected_size = tar_size(manifest['files'])
    require(shutil.disk_usage(root).free >= DESTINATION_RESERVE + expected_size + total,
            'Cold archive destination has insufficient reserve for transfer and verification')
    stage = Path(tempfile.mkdtemp(prefix=f'.{run}-', dir=root))
    try:
        archive = stage / ARCHIVE
        size = 0
        with os.fdopen(os.open(archive, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as output:
            while block := stream.read(MIB):
                size += len(block)
                require(size <= expected_size, 'Cold archive stream exceeds manifest bounds')
                output.write(block)
            output.flush()
            os.fsync(output.fileno())
        require(size == expected_size, 'Cold archive stream is truncated')
        unpack_and_verify(archive, stage, manifest['files'])
        require(shutil.disk_usage(root).free >= DESTINATION_RESERVE, 'Cold archive destination reserve fell during transfer')
        write_json(stage / MANIFEST, manifest)
        require(digest(stage / MANIFEST) == manifest_sha, 'Stored manifest SHA differs')
        receipt = {'status': 'cold_copy_restored_and_sha_verified', 'verified_at': now().isoformat(),
                   'source_run': run, 'source_root': manifest['source_root'],
                   'source_manifest_sha256': manifest_sha, 'archive_sha256': digest(archive),
                   'archive_bytes': size, 'verified_files': len(manifest['files']),
                   'archive': str(final / ARCHIVE), 'recovery_receipt': str(final / RECOVERY)}
        write_json(stage / RECOVERY, receipt)
        os.rename(stage, final)
        sync_directory(root)
        return receipt
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def verify_destination(root, run, manifest_sha):
    folder = root / run
    if not folder.exists() and not folder.is_symlink():
        return {'status': 'missing'}
    require(folder.is_dir() and private_path(folder, 0o700), 'Cold archive directory is not private')
    for name in (MANIFEST, ARCHIVE, RECOVERY):
        require((folder / name).is_file() and private_path(folder / name, 0o600), 'Cold archive file is not private')
    receipt = read_json(folder / RECOVERY)
    require(receipt['status'] == 'cold_copy_restored_and_sha_verified' and receipt['source_run'] == run
            and receipt['source_manifest_sha256'] == digest(folder / MANIFEST) == manifest_sha
            and receipt['archive'] == str(folder / ARCHIVE)
            and receipt['archive_bytes'] == (folder / ARCHIVE).stat().st_size
            and receipt['archive_sha256'] == digest(folder / ARCHIVE),
            'Persisted cold archive or receipt SHA differs')
    return receipt


def ssh_command(target, action, run, manifest_sha):
    arguments = ('/usr/bin/python3.11', '-c', Path(__file__).read_text(), action, run, manifest_sha)
    return ['/usr/bin/ssh', '-T', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'UpdateHostKeys=no',
            '-o', 'ClearAllForwardings=yes', '-o', 'IdentityAgent=none', '-o', 'ControlPath=none',
            '-o', 'ConnectTimeout=10', '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=2',
            target, shlex.join(arguments)]


def send_tar(base, manifest, stream):
    stream.write(canonical(manifest) + b'\n')
    with tarfile.open(fileobj=stream, mode='w|', format=tarfile.USTAR_FORMAT) as archive:
        for item in manifest['files']:
            entry = tarfile.TarInfo(item['path'])
            entry.size, entry.mode = item['bytes'], 0o600
            with (base / item['path']).open('rb') as source:
                archive.addfile(entry, source)


def remote(target, action, run, manifest_sha, *, base=None, manifest=None):
    with tempfile.TemporaryFile() as output:
        command = ssh_command(target, action, run, manifest_sha)
        with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=output, stderr=subprocess.DEVNULL) as process:
            try:
                try:
                    with process.stdin as stream:
                        if manifest is not None:
                            send_tar(base, manifest, stream)
                except BrokenPipeError:
                    pass  # Read the receiver's error receipt.
                process.wait(timeout=180)
            except BaseException:
                process.kill()
                raise
            output.seek(0)
            data = output.read(32 * 1024)
            require(data, 'Cold archive SSH failed before returning a receipt')
            result = json.loads(data)
            require(process.returncode == 0 and result.get('status') != 'failed',
                    result.get('error', 'Cold archive SSH failed'))
    return result


def release_hot(root, base, manifest, remote_receipt, manifest_sha):
    marker_path = base / MARKER
    with lock(root / '.capture.lock', create=True), lock(base / 'queue/.queue.lock'):
        require(shutil.disk_usage(root).free >= SOURCE_RECEIPT_RESERVE, 'Insufficient space for cold release receipts')
        require(base in eligible(root, now()), 'Queue is no longer eligible for cold release')
        prior = read_json(marker_path) if marker_path.exists() else None
        require(snapshot(base, full_scan=False, prior=prior) == manifest, 'Hot queue changed after cold verification')
        run = manifest['source_run']
        require(remote_receipt['status'] == 'cold_copy_restored_and_sha_verified'
                and remote_receipt['source_run'] == run and remote_receipt['source_root'] == str(base)
                and remote_receipt['source_manifest_sha256'] == manifest_sha
                and remote_receipt['verified_files'] == len(manifest['files'])
                and remote_receipt['archive'] == str(DESTINATION / run / ARCHIVE)
                and SHA256.fullmatch(remote_receipt['archive_sha256']),
                'Cold recovery proof does not match this queue')
        marker = {**remote_receipt, 'status': 'verified_cold_archive', 'verified_files': manifest['files'],
                  'marked_at': now().isoformat(), 'protected_receipt_digests': manifest['protected'],
                  'restore_stream_command': f'ssh -o BatchMode=yes hk-relay cat {remote_receipt["archive"]} '
                                            '| tar -xf - -C /path/to/new/private-restore-directory'}
        if prior is not None:
            require(prior['source_manifest_sha256'] == manifest_sha
                    and prior['archive_sha256'] == marker['archive_sha256']
                    and prior['verified_files'] == manifest['files'], 'Previously marked archive proof differs')
        else:
            write_json(marker_path, marker)
        require(read_json(marker_path)['archive_sha256'] == marker['archive_sha256'], 'Cold marker readback failed')
        before = shutil.disk_usage(root).free
        changed = set()
        removed = allocated_bytes = 0
        for item in manifest['files']:
            path = base / item['path']
            if path.exists():
                allocated_bytes += path.stat().st_blocks * 512
                path.unlink()
                changed.add(path.parent)
                removed += 1
        for folder in changed:
            sync_directory(folder)
        require(all(digest(base / name) == sha for name, sha in manifest['protected'].items()),
                'Protected publication receipts changed')
        receipt = {**remote_receipt, 'status': 'verified_hot_copies_released', 'completed_at': now().isoformat(),
                   'cold_marker': str(marker_path), 'hot_files_removed': removed,
                   'already_cold_files': len(manifest['files']) - removed,
                   'protected_receipts_unchanged': len(manifest['protected']),
                   'hot_allocated_bytes_removed': allocated_bytes,
                   'free_bytes_delta_at_unlink': shutil.disk_usage(root).free - before}
        write_json(base / RELEASE, receipt)
        return receipt


def archive_once(root, apply, report):
    with lock(root / '.cold-archive.lock', create=True):
        report['available_bytes_before'] = free = shutil.disk_usage(root).free
        require(free >= SOURCE_RECEIPT_RESERVE, 'Insufficient local space for durable cold receipts')
        candidates = eligible(root, now())
        if free >= SOURCE_WATERMARK and not (candidates and (candidates[0] / MARKER).exists()):
            return {'status': 'above_archive_watermark'}
        if not candidates:
            return {'status': 'no_eligible_completed_queue'}
        base = candidates[0]
        report['source_run'] = run = base.name.removeprefix('school-enrichment-')
        with lock(base / 'queue/.queue.lock'):
            prior = read_json(base / MARKER) if (base / MARKER).exists() else None
            manifest = snapshot(base, full_scan=True, prior=prior)
        total = validate_manifest(manifest, run)
        if not apply:
            return {'status': 'candidate_verified', 'verified_files': len(manifest['files']), 'candidate_bytes': total}
        configuration = root / 'cold-archive.json'
        require(private_path(configuration, 0o600), 'Cold archive SSH configuration is not private')
        target = read_json(configuration)['ssh_target']
        require(re.fullmatch(r'[A-Za-z0-9_.-]+@[A-Za-z0-9_.:-]+', target), 'Invalid configured cold archive SSH target')
        data = canonical(manifest) + b'\n'
        require(len(data) <= MAX_MANIFEST_BYTES, 'Cold archive manifest exceeds the transfer cap')
        manifest_sha = hashlib.sha256(data).hexdigest()
        proof = remote(target, '--remote-verify', run, manifest_sha)
        if proof['status'] == 'missing':
            require(prior is None, 'Previously marked cold archive is missing; hot files preserved')
            remote(target, '--remote-receive', run, manifest_sha, base=base, manifest=manifest)
            proof = remote(target, '--remote-verify', run, manifest_sha)
        return release_hot(root, base, manifest, proof, manifest_sha)


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--check', action='store_true')
    modes.add_argument('--apply', action='store_true')
    modes.add_argument('--remote-verify', nargs=2, metavar=('RUN', 'SHA'))
    modes.add_argument('--remote-receive', nargs=2, metavar=('RUN', 'SHA'))
    parser.add_argument('--root', type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        if args.remote_receive or args.remote_verify:
            run, manifest_sha = args.remote_receive or args.remote_verify
            require(RUN.fullmatch(run) and SHA256.fullmatch(manifest_sha), 'Invalid cold archive identifier')
            DESTINATION.mkdir(mode=0o700, exist_ok=True)
            require(private_path(DESTINATION, 0o700), 'Cold archive destination is not private')
            with lock(DESTINATION / '.cold-archive.lock', create=True):
                result = (receive(DESTINATION, run, manifest_sha, sys.stdin.buffer) if args.remote_receive
                          else verify_destination(DESTINATION, run, manifest_sha))
            print(json.dumps(result))
            return 0
        root = args.root.resolve()
        started = now()
        report = {'started_at': started.isoformat(), 'apply': args.apply}
        def interrupted(_signal, _frame):
            raise ArchiveError('Cold archive interrupted')
        signal.signal(signal.SIGTERM, interrupted)
        try:
            report.update(archive_once(root, args.apply, report))
        except (ArchiveError, OSError, ValueError, KeyError, TypeError, EOFError, tarfile.TarError, subprocess.SubprocessError) as error:
            report.update(status='failed', error=str(error) if isinstance(error, ArchiveError) else type(error).__name__)
        report.update(completed_at=now().isoformat(), available_bytes_after=shutil.disk_usage(root).free,
                      source_watermark_bytes=SOURCE_WATERMARK)
        receipts = root / 'logs/cold-archive-receipts'
        receipts.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = receipts / f'{started:%Y%m%d_%H%M%S_%f}.json'
        write_json(path, report)
        print(json.dumps({**report, 'receipt': str(path)}))
        return int(report['status'] == 'failed')
    except (ArchiveError, OSError, ValueError, KeyError, TypeError, EOFError, tarfile.TarError) as error:
        print(json.dumps({'status': 'failed', 'error': str(error) if isinstance(error, ArchiveError) else type(error).__name__}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

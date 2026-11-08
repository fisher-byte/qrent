#!/usr/bin/env python3
"""Keep seven local main SQLite snapshots and verify every older cold copy."""
import argparse
from contextlib import closing
from datetime import datetime
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import sqlite3
import struct
import subprocess
import sys
import tempfile

try:
    from . import cold_mysql_backups as shared
except ImportError:
    import cold_mysql_backups as shared


DESTINATION = shared.DESTINATION / 'sqlite-main'
STAGING = Path('/dev/shm')
HOT_COUNT = 7
MAX_RAW_BYTES = 1024 * shared.MIB
MAX_GZIP_BYTES = 256 * shared.MIB
NAME = re.compile(r'properties_(\d{8})\.db\.gz')
PROPERTY_COLUMNS = set(('id house_id source price_per_week address_line1 address_line2 suburb state postcode '
                        'bedroom_count bathroom_count parking_count property_type property_type_raw '
                        'description_en description_cn keywords url thumbnail_url available_date published_at '
                        'scraped_at updated_at is_active').split())
ENRICHMENT_COLUMNS = {'house_id', 'source', 'payload'}
PAYLOAD_FIELDS = ((PROPERTY_COLUMNS - {'id', 'updated_at', 'is_active'})
                  | {'average_score', 'scores', 'commute_times', 'price_text'})
PRIVATE_BYTES = re.compile(shared.PRIVATE_CONTENT.pattern.encode())
RECOVERY = 'archive-recovery-receipt.json'
MANIFEST = 'source-manifest.json'
ERRORS = (shared.ArchiveError, OSError, ValueError, KeyError, TypeError, EOFError, sqlite3.Error, subprocess.SubprocessError)


def valid_name(name):
    match = NAME.fullmatch(name)
    shared.require(match, 'Snapshot is outside the main SQLite filename whitelist')
    datetime.strptime(match[1], '%Y%m%d')


def local_snapshots(root):
    paths = sorted(path for path in (root / 'backup').glob('properties_*.db.gz') if NAME.fullmatch(path.name))
    for path in paths:
        valid_name(path.name)
        shared.require(path.is_file() and shared.private_path(path, 0o600), 'Main SQLite hot inventory contains a linked or unprivate file')
    return paths


def copy_bounded(source, destination, expected):
    size = 0
    digest = hashlib.sha256()
    tail = b''
    with destination.open('xb') as output:
        os.fchmod(output.fileno(), 0o600)
        while block := source.read(shared.MIB):
            size += len(block)
            shared.require(size <= expected, 'Snapshot stream exceeds its declared length')
            if destination.suffix == '.db':
                shared.require(not PRIVATE_BYTES.search(tail + block), 'Snapshot contains a private key signature')
                tail = block[-512:]
            output.write(block)
            digest.update(block)
        shared.require(size == expected, 'Snapshot stream is truncated')
        output.flush()
        os.fsync(output.fileno())
    return digest.hexdigest()


def inspect_snapshot(archive, expected_raw_bytes, staging):
    with tempfile.TemporaryDirectory(prefix='sqlite-restore-', dir=staging) as scratch:
        restored = Path(scratch) / 'restored.db'
        with gzip.open(archive, 'rb') as stream:
            raw_sha = copy_bounded(stream, restored, expected_raw_bytes)
        with closing(sqlite3.connect(restored.as_uri() + '?mode=ro&immutable=1', uri=True)) as db:
            db.execute('PRAGMA trusted_schema=OFF')
            schema = set(db.execute("SELECT name, type FROM sqlite_schema WHERE type <> 'index'"))
            shared.require(schema == {(table, 'table') for table in ('properties', 'property_enrichment', 'sqlite_sequence')},
                           'Snapshot contains tables or other objects outside the main SQLite scope')
            columns = {row[1] for row in db.execute('PRAGMA table_xinfo(properties)')}
            enrichment = {row[1] for row in db.execute('PRAGMA table_xinfo(property_enrichment)')}
            shared.require(columns == PROPERTY_COLUMNS and enrichment == ENRICHMENT_COLUMNS,
                           'Snapshot contains unexpected property columns')
            shared.require(db.execute('PRAGMA quick_check').fetchall() == [('ok',)], 'Restored SQLite quick_check failed')
            for (payload,) in db.execute('SELECT payload FROM property_enrichment'):
                value = json.loads(payload)
                shared.require(isinstance(value, dict) and set(value) <= PAYLOAD_FIELDS,
                               'Snapshot contains property payload fields outside the allowed schema')
            rows = {table: db.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
                    for table in ('properties', 'property_enrichment')}
    return {'raw_sha256': raw_sha, 'raw_bytes': expected_raw_bytes, 'row_counts': rows, 'quick_check': 'ok'}


def available_memory():
    return next((int(row.split()[1]) * 1024 for row in Path('/proc/meminfo').read_text().splitlines()
                 if row.startswith('MemAvailable:')), 0)


def inspect_source(path):
    valid_name(path.name)
    shared.require(path.is_file() and shared.private_path(path, 0o600), 'Main SQLite backup is missing or is not private')
    info = path.stat()
    shared.require(info.st_nlink == 1 and 32 <= info.st_size <= MAX_GZIP_BYTES, 'Main SQLite gzip size or link count is invalid')
    with path.open('rb') as stream:
        stream.seek(-4, os.SEEK_END)
        raw_size, = struct.unpack('<I', stream.read(4))
    needed = raw_size + 16 * shared.MIB
    shared.require(0 < raw_size <= MAX_RAW_BYTES and shutil.disk_usage(STAGING).free >= needed
                   and available_memory() >= needed + 1024 * shared.MIB, 'Insufficient or invalid main SQLite restore staging capacity')
    details = inspect_snapshot(path, raw_size, STAGING)
    return {'version': 1, 'snapshot': path.name, 'original_path': str(path), 'mtime_ns': info.st_mtime_ns,
            'gzip_bytes': info.st_size, 'gzip_sha256': shared.digest(path), **details}


def destination_folder(root, name):
    valid_name(name)
    return root / name.removesuffix('.db.gz')


def receive(root, name, manifest_sha, stream):
    payload = stream.readline(shared.MAX_MANIFEST_BYTES + 1)
    shared.require(len(payload) <= shared.MAX_MANIFEST_BYTES and payload.endswith(b'\n')
                   and hashlib.sha256(payload).hexdigest() == manifest_sha, 'Transferred SQLite manifest SHA differs')
    manifest = json.loads(payload)
    shared.require(manifest['version'] == 1 and manifest['snapshot'] == name
                   and Path(manifest['original_path']) == shared.ROOT / 'backup' / name
                   and 0 < manifest['raw_bytes'] <= MAX_RAW_BYTES and 32 <= manifest['gzip_bytes'] <= MAX_GZIP_BYTES
                   and shared.SHA256.fullmatch(manifest['gzip_sha256']), 'Invalid main SQLite cold archive manifest')
    final = destination_folder(root, name)
    shared.require(not final.exists() and not final.is_symlink(), 'A cold SQLite snapshot already exists')
    needed = shared.DESTINATION_RESERVE + manifest['gzip_bytes'] + manifest['raw_bytes']
    shared.require(shutil.disk_usage(root).free >= needed, 'Insufficient Hong Kong capacity for the SQLite restore proof')
    stage = Path(tempfile.mkdtemp(prefix=f'.{final.name}-', dir=root))
    try:
        archive = stage / name
        shared.require(copy_bounded(stream, archive, manifest['gzip_bytes']) == manifest['gzip_sha256'],
                       'Transferred main SQLite gzip SHA differs')
        restored = inspect_snapshot(archive, manifest['raw_bytes'], stage)
        shared.require(all(manifest[key] == value for key, value in restored.items()), 'Restored SQLite proof differs from Guangzhou')
        shared.require(shutil.disk_usage(root).free >= shared.DESTINATION_RESERVE, 'Hong Kong disk reserve fell during transfer')
        shared.write_json(stage / MANIFEST, manifest)
        shared.require(shared.digest(stage / MANIFEST) == manifest_sha, 'Persisted main SQLite manifest SHA differs')
        receipt = {'status': 'main_sqlite_cold_restored', 'verified_at': shared.now().isoformat(),
                   'archive': str(final / name), 'recovery_receipt': str(final / RECOVERY),
                   'source_manifest_sha256': manifest_sha, **manifest}
        shared.write_json(stage / RECOVERY, receipt)
        os.rename(stage, final)
        shared.sync_directory(root)
        return receipt
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def verify_destination(root, name, manifest_sha):
    folder = destination_folder(root, name)
    if not folder.exists() and not folder.is_symlink():
        return {'status': 'missing'}
    shared.require(folder.is_dir() and shared.private_path(folder, 0o700), 'Cold SQLite directory is not private')
    archive = folder / name
    for path in (archive, folder / MANIFEST, folder / RECOVERY):
        shared.require(path.is_file() and shared.private_path(path, 0o600), 'Cold SQLite file is not private')
    manifest = shared.read_json(folder / MANIFEST)
    receipt = shared.read_json(folder / RECOVERY)
    shared.require(shared.digest(folder / MANIFEST) == manifest_sha == receipt['source_manifest_sha256']
                   and receipt['status'] == 'main_sqlite_cold_restored' and receipt['archive'] == str(archive)
                   and all(receipt[key] == value for key, value in manifest.items())
                   and archive.stat().st_size == manifest['gzip_bytes']
                   and shared.digest(archive) == manifest['gzip_sha256'], 'Persisted main SQLite receipt or gzip SHA differs')
    shared.require(shutil.disk_usage(root).free >= shared.DESTINATION_RESERVE + manifest['raw_bytes'],
                   'Insufficient Hong Kong capacity for independent SQLite readback')
    restored = inspect_snapshot(archive, manifest['raw_bytes'], root)
    shared.require(all(receipt[key] == value for key, value in restored.items()), 'Cold SQLite restoration differs')
    return receipt


def remote_script():
    # The relay runs the deployed receiver from the pinned SSH connection.
    common = Path(shared.__file__).read_text()
    return ("import sys, types\n"
            "_common = types.ModuleType('cold_mysql_backups')\n"
            f"exec({common!r}, _common.__dict__)\n"
            "sys.modules[_common.__name__] = _common\n" + Path(__file__).read_text())


def send_gzip(path, manifest_data, stream):
    stream.write(manifest_data)
    with path.open('rb') as source:
        shutil.copyfileobj(source, stream, shared.MIB)


def release_local(root, path, manifest, receipt, manifest_sha):
    with shared.lock(root / '.capture.lock', create=True):
        shared.require(shutil.disk_usage(root).free >= shared.SOURCE_RECEIPT_RESERVE
                       and path in local_snapshots(root)[:-HOT_COUNT], 'Snapshot is now protected or local receipt reserve is low')
        shared.require(path.is_file() and shared.private_path(path, 0o600)
                       and path.stat().st_mtime_ns == manifest['mtime_ns']
                       and path.stat().st_nlink == 1 and shared.digest(path) == manifest['gzip_sha256'],
                       'Original main SQLite gzip changed after verification')
        expected_remote = destination_folder(DESTINATION, path.name) / path.name
        shared.require(receipt['status'] == 'main_sqlite_cold_restored' and receipt['archive'] == str(expected_remote)
                       and receipt['source_manifest_sha256'] == manifest_sha
                       and all(receipt[key] == value for key, value in manifest.items()), 'Cold SQLite recovery proof differs')
        receipts = root / 'backup/main-cold-receipts'
        receipts.mkdir(mode=0o700, exist_ok=True)
        shared.require(shared.private_path(receipts, 0o700), 'Main SQLite source receipt directory is not private')
        marker = receipts / (path.name + '.json')
        record = {**receipt, 'status': 'main_sqlite_cold_verified', 'marked_at': shared.now().isoformat(),
                  'restore_stream_command': f'ssh -o BatchMode=yes hk-relay cat {expected_remote} '
                                            '> /path/to/new/private-main-snapshot.db.gz'}
        if marker.exists() or marker.is_symlink():
            old = shared.read_json(marker)
            shared.require(old['source_manifest_sha256'] == manifest_sha, 'An existing main SQLite cold marker differs')
        else:
            shared.write_json(marker, record)
        shared.require(shared.read_json(marker)['gzip_sha256'] == manifest['gzip_sha256'], 'Main SQLite cold marker readback failed')
        allocated = path.stat().st_blocks * 512
        path.unlink()
        shared.sync_directory(path.parent)
        record.update(status='main_sqlite_hot_copy_released', hot_allocated_bytes_removed=allocated,
                      released_at=shared.now().isoformat(), source_receipt=str(marker))
        shared.write_json(marker, record)
        return record


def archive_old(root, apply, limit, report):
    with shared.lock(root / '.cold-archive.lock', create=True):
        snapshots = local_snapshots(root)
        report['kept_local'] = [path.name for path in snapshots[-HOT_COUNT:]]
        report['available_bytes_before'] = free = shutil.disk_usage(root).free
        shared.require(free >= shared.SOURCE_RECEIPT_RESERVE, 'Insufficient local space for cold SQLite receipts')
        older = snapshots[:-HOT_COUNT][:limit]
        if not older:
            return 'no_snapshot_older_than_seven'
        target = shared.read_target(root) if apply else None
        script = remote_script() if apply else None
        for path in older:
            report['current_snapshot'] = path.name
            manifest = inspect_source(path)
            if not apply:
                report['completed'].append(manifest)
                continue
            data = shared.canonical(manifest) + b'\n'
            manifest_sha = hashlib.sha256(data).hexdigest()
            proof = shared.remote(target, '--remote-verify', path.name, manifest_sha, script=script)
            if proof['status'] == 'missing':
                shared.remote(target, '--remote-receive', path.name, manifest_sha, script=script,
                              send=lambda stream: send_gzip(path, data, stream))
                proof = shared.remote(target, '--remote-verify', path.name, manifest_sha, script=script)
            report['completed'].append(release_local(root, path, manifest, proof, manifest_sha))
        return 'cold_main_sqlite_verified' if apply else 'local_main_sqlite_candidates_verified'


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--check', action='store_true')
    modes.add_argument('--apply', action='store_true')
    modes.add_argument('--remote-verify', nargs=2, metavar=('NAME', 'SHA'))
    modes.add_argument('--remote-receive', nargs=2, metavar=('NAME', 'SHA'))
    parser.add_argument('--root', type=Path, default=shared.ROOT)
    parser.add_argument('--limit', type=int, choices=range(1, 5), default=1)
    args = parser.parse_args()
    try:
        if args.remote_receive or args.remote_verify:
            name, manifest_sha = args.remote_receive or args.remote_verify
            valid_name(name)
            shared.require(shared.SHA256.fullmatch(manifest_sha), 'Invalid SQLite manifest SHA')
            shared.DESTINATION.mkdir(mode=0o700, exist_ok=True)
            shared.require(shared.private_path(shared.DESTINATION, 0o700), 'Cold archive root is not private')
            with shared.lock(shared.DESTINATION / '.cold-archive.lock', create=True):
                DESTINATION.mkdir(mode=0o700, exist_ok=True)
                shared.require(shared.private_path(DESTINATION, 0o700), 'Cold SQLite root is not private')
                result = (receive(DESTINATION, name, manifest_sha, sys.stdin.buffer) if args.remote_receive
                          else verify_destination(DESTINATION, name, manifest_sha))
            print(json.dumps(result))
            return 0
        root = args.root.resolve()
        started = shared.now()
        report = {'started_at': started.isoformat(), 'apply': args.apply, 'completed': []}
        signal.signal(signal.SIGTERM, shared.interrupted)
        try:
            report['status'] = archive_old(root, args.apply, args.limit, report)
        except ERRORS as error:
            report.update(status='failed', error=str(error) if isinstance(error, shared.ArchiveError) else type(error).__name__)
        report.update(completed_at=shared.now().isoformat(), available_bytes_after=shutil.disk_usage(root).free)
        receipts = root / 'logs/cold-archive-receipts'
        receipts.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = receipts / f'sqlite-{started:%Y%m%d_%H%M%S_%f}.json'
        shared.write_json(path, report)
        print(json.dumps({**report, 'receipt': str(path)}))
        return int(report['status'] == 'failed')
    except ERRORS as error:
        print(json.dumps({'status': 'failed', 'error': str(error) if isinstance(error, shared.ArchiveError) else type(error).__name__}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

#!/usr/bin/env python3
"""Make a consistent SQLite backup, including committed WAL transactions."""
import argparse
from contextlib import closing
import gzip
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path


MIB = 1024 ** 2
BACKUP_RESERVE = 300 * MIB


def available_memory_bytes():
    for line in Path('/proc/meminfo').read_text().splitlines():
        if line.startswith('MemAvailable:'):
            return int(line.split()[1]) * 1024
    raise OSError('Cannot measure staging memory')


def backup_database(database, directory, *, compress=False, staging_directory=None,
                    reserve_bytes=BACKUP_RESERVE):
    """Publish a checked, standalone snapshot without replacing a good backup early."""
    database, directory = Path(database), Path(directory)
    if staging_directory is not None and not compress:
        raise ValueError('A separate staging directory requires gzip')
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = directory / f"properties_{datetime.now():%Y%m%d}.db{'.gz' if compress else ''}"
    temporary_paths = []
    private_stage = None

    def temporary(parent, suffix):
        descriptor, name = tempfile.mkstemp(prefix=f'.{target.name}.', suffix=suffix, dir=parent)
        os.close(descriptor)
        path = Path(name)
        temporary_paths.append(path)
        return path

    try:
        stage = directory
        with closing(sqlite3.connect(f'{database.resolve().as_uri()}?mode=ro', uri=True)) as source:
            if staging_directory is not None:
                pages = source.execute('PRAGMA page_count').fetchone()[0]
                page_size = source.execute('PRAGMA page_size').fetchone()[0]
                staging_bytes = 2 * pages * page_size + 16 * MIB
                stage_free = shutil.disk_usage(staging_directory).free
                if stage_free < staging_bytes:
                    raise OSError(f'Insufficient backup staging space: {stage_free} bytes free, {staging_bytes} required')
                memory_free = available_memory_bytes()
                if memory_free < staging_bytes + 1024 * MIB:
                    raise OSError(f'Insufficient backup staging memory: {memory_free} bytes free, '
                                  f'{staging_bytes + 1024 * MIB} required including reserve')
                private_stage = tempfile.TemporaryDirectory(prefix='qrent-backup-', dir=staging_directory)
                stage = Path(private_stage.name)
            snapshot = temporary(stage, '.db.tmp')
            with closing(sqlite3.connect(snapshot)) as destination:
                source.backup(destination, pages=256, sleep=0.05)
                # A backup of a WAL database must stand on its own when compressed.
                destination.execute('PRAGMA journal_mode=DELETE')
                checks = destination.execute('PRAGMA quick_check').fetchall()
                if checks != [('ok',)]:
                    raise RuntimeError(f'SQLite backup quick_check failed: {checks!r}')
        artifact = snapshot
        if compress:
            artifact = temporary(stage, '.gz.tmp')
            with snapshot.open('rb') as source, artifact.open('wb') as output:
                with gzip.GzipFile(filename='', mode='wb', fileobj=output, mtime=0) as compressed:
                    shutil.copyfileobj(source, compressed, length=1024 * 1024)
                output.flush()
                os.fsync(output.fileno())
            with snapshot.open('rb') as original, gzip.open(artifact, 'rb') as restored:
                if hashlib.file_digest(original, 'sha256').digest() != hashlib.file_digest(restored, 'sha256').digest():
                    raise OSError('Gzip payload differs from the checked SQLite snapshot')
        else:
            with artifact.open('rb') as output:
                os.fsync(output.fileno())
        if private_stage is not None:
            needed = artifact.stat().st_size + reserve_bytes
            destination_free = shutil.disk_usage(directory).free
            if destination_free < needed:
                raise OSError(f'Insufficient backup destination space: {destination_free} bytes free, '
                              f'{needed} required including reserve')
            candidate = temporary(directory, '.gz.publish.tmp')
            with artifact.open('rb') as source, candidate.open('wb') as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
                output.flush()
                os.fsync(output.fileno())
            with artifact.open('rb') as source, candidate.open('rb') as output:
                if hashlib.file_digest(source, 'sha256').digest() != hashlib.file_digest(output, 'sha256').digest():
                    raise OSError('Backup destination copy differs from the verified archive')
            if shutil.disk_usage(directory).free < reserve_bytes:
                raise OSError('Backup destination fell below its free space reserve')
            artifact = candidate
        artifact.chmod(0o600)
        os.replace(artifact, target)
        return target
    finally:
        for path in temporary_paths:
            for suffix in ('', '-wal', '-shm', '-journal'):
                Path(str(path) + suffix).unlink(missing_ok=True)
        if private_stage is not None:
            private_stage.cleanup()


def write_receipt(path, receipt):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix='.backup-receipt-')
    candidate = Path(name)
    try:
        with os.fdopen(descriptor, 'w') as stream:
            json.dump(receipt, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(candidate, path)
    finally:
        candidate.unlink(missing_ok=True)


def rotate_backups(target, retain_days):
    cutoff = time.time() - (retain_days + 1) * 86400  # Preserve the existing find -mtime +N boundary.
    removed = []
    for path in sorted(target.parent.iterdir()):
        if (path != target and re.fullmatch(r'properties_\d{8}\.db(?:\.gz)?', path.name)
                and not path.is_symlink() and path.is_file() and path.stat().st_mtime < cutoff):
            path.unlink()
            removed.append(path.name)
    return removed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--gzip", action="store_true", help="Stream the checked snapshot into a .db.gz backup")
    parser.add_argument('--staging-directory', type=Path, help='Staging filesystem with measured RAM and space')
    parser.add_argument('--receipt-directory', type=Path, help='Write a checked receipt for every attempt')
    parser.add_argument('--retain-days', type=int, help='Rotate named backups only after a verified receipt')
    args = parser.parse_args()
    if args.retain_days is not None and (args.retain_days < 1 or args.receipt_directory is None):
        parser.error('--retain-days requires a positive value and --receipt-directory')
    now = datetime.now().astimezone()
    receipt_path = (args.receipt_directory / f'{args.database.name}-{now:%Y%m%d_%H%M%S_%f}.json'
                    if args.receipt_directory is not None else None)
    receipt = {'status': 'backup_failed', 'started_at': now.isoformat(), 'database': str(args.database),
               'directory': str(args.directory), 'staging_directory': str(args.staging_directory or args.directory)}
    try:
        target = backup_database(args.database, args.directory, compress=args.gzip,
                                 staging_directory=args.staging_directory)
        if receipt_path is not None:
            free_bytes = shutil.disk_usage(target.parent).free
            with target.open('rb') as stream:
                receipt.update(status='backup_verified', completed_at=datetime.now(timezone.utc).isoformat(),
                               archive=str(target), sha256=hashlib.file_digest(stream, 'sha256').hexdigest(),
                               archive_bytes=target.stat().st_size, quick_check='ok',
                               gzip_payload_verified=args.gzip, available_bytes=free_bytes,
                               low_space_warning=free_bytes < 1024 * MIB)
            write_receipt(receipt_path, receipt)
            if args.retain_days is not None:
                receipt['rotated'] = rotate_backups(target, args.retain_days)
                write_receipt(receipt_path, receipt)
        print(target)
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as error:
        if receipt['status'] == 'backup_verified':
            receipt['status'] = 'backup_verified_followup_failed'
        receipt.update(error=f'{type(error).__name__}: {error}', completed_at=datetime.now(timezone.utc).isoformat())
        if receipt_path is not None:
            try:
                write_receipt(receipt_path, receipt)
            except OSError as receipt_error:
                receipt['receipt_error'] = str(receipt_error)
        print(json.dumps(receipt), file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()

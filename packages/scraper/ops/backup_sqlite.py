#!/usr/bin/env python3
"""Make a consistent SQLite backup, including committed WAL transactions."""
import argparse
from contextlib import closing
import gzip
import os
import shutil
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path


def backup_database(database, directory, *, compress=False):
    """Publish a checked, standalone snapshot without replacing a good backup early."""
    database, directory = Path(database), Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = directory / f"properties_{datetime.now():%Y%m%d}.db{'.gz' if compress else ''}"
    temporary_paths = []

    def temporary(suffix):
        descriptor, name = tempfile.mkstemp(prefix=f'.{target.name}.', suffix=suffix, dir=directory)
        os.close(descriptor)
        path = Path(name)
        temporary_paths.append(path)
        return path

    try:
        snapshot = temporary('.db.tmp')
        with closing(sqlite3.connect(f'{database.resolve().as_uri()}?mode=ro', uri=True)) as source:
            with closing(sqlite3.connect(snapshot)) as destination:
                source.backup(destination, pages=256, sleep=0.05)
                # A backup of a WAL database must stand on its own when compressed.
                destination.execute('PRAGMA journal_mode=DELETE')
                checks = destination.execute('PRAGMA quick_check').fetchall()
                if checks != [('ok',)]:
                    raise RuntimeError(f'SQLite backup quick_check failed: {checks!r}')
        artifact = snapshot
        if compress:
            artifact = temporary('.gz.tmp')
            with snapshot.open('rb') as source, artifact.open('wb') as output:
                with gzip.GzipFile(filename='', mode='wb', fileobj=output, mtime=0) as compressed:
                    shutil.copyfileobj(source, compressed, length=1024 * 1024)
                output.flush()
                os.fsync(output.fileno())
        else:
            with artifact.open('rb') as output:
                os.fsync(output.fileno())
        artifact.chmod(0o600)
        os.replace(artifact, target)
        return target
    finally:
        for path in temporary_paths:
            for suffix in ('', '-wal', '-shm', '-journal'):
                Path(str(path) + suffix).unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--gzip", action="store_true", help="Stream the checked snapshot into a .db.gz backup")
    args = parser.parse_args()
    print(backup_database(args.database, args.directory, compress=args.gzip))


if __name__ == "__main__":
    main()

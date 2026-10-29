#!/usr/bin/env python3
"""Read-only, consistent full MySQL archive; never creates or changes server tables."""
import argparse
import base64
import gzip
import hashlib
import json
import os
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit, unquote

import mysql.connector
from dotenv import dotenv_values


def encode(value):
    if isinstance(value, (datetime, date)):
        return {'type': type(value).__name__, 'value': value.isoformat()}
    if isinstance(value, Decimal):
        return {'type': 'decimal', 'value': str(value)}
    if isinstance(value, bytes):
        return {'type': 'bytes', 'value': base64.b64encode(value).decode()}
    if isinstance(value, timedelta):
        return {'type': 'timedelta', 'value': value.total_seconds()}
    raise TypeError(type(value).__name__)


def decode(value):
    if set(value) != {'type', 'value'}:
        return value
    kind, data = value['type'], value['value']
    return {'datetime': datetime.fromisoformat, 'date': date.fromisoformat,
            'decimal': Decimal, 'bytes': base64.b64decode,
            'timedelta': lambda x: timedelta(seconds=x)}.get(kind, lambda _: value)(data)


def config_from_env(path):
    # An explicit --mysql-env must select its own restricted identity even when
    # the scraper process inherited a different DB configuration. Empty values
    # intentionally clear an inherited DATABASE_URL.
    env = {**{k: v for k, v in os.environ.items() if k.startswith('DB_') or k == 'DATABASE_URL'},
           **dotenv_values(path)}
    if env.get('DATABASE_URL'):
        url = urlsplit(env['DATABASE_URL'])
        if url.scheme != 'mysql':
            raise ValueError('DATABASE_URL must be a MySQL URL')
        return dict(host=url.hostname, port=url.port or 3306, user=unquote(url.username or ''),
                    password=unquote(url.password or ''), database=url.path.lstrip('/'))
    return dict(host=env['DB_HOST'], port=int(env.get('DB_PORT', 3306)), user=env['DB_USER'],
                password=env['DB_PASSWORD'], database=env.get('DB_DATABASE', 'qrent'))


def quoted(name):
    return '`' + name.replace('`', '``') + '`'


def verify_archive(path):
    counts = {}
    footer = None
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        for line in stream:
            item = json.loads(line, object_hook=decode)
            if item['kind'] == 'table':
                if item['name'] in counts:
                    raise ValueError('Duplicate table in archive')
                counts[item['name']] = 0
            elif item['kind'] == 'row':
                counts[item['table']] += 1
            elif item['kind'] == 'table_end':
                if counts[item['name']] != item['count']:
                    raise ValueError('Incomplete table in archive')
            elif item['kind'] == 'end':
                footer = item['counts']
    if not counts or footer != counts:
        raise ValueError('Archive footer or table counts missing')
    return counts


def archive(connection, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents replacing the only pre-publication backup.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    cursor = connection.cursor()
    counts = {}
    try:
        connection.start_transaction(consistent_snapshot=True, readonly=True)
        cursor.execute('SHOW FULL TABLES')
        tables = sorted(row[0] for row in cursor.fetchall() if row[1] == 'BASE TABLE')
        with os.fdopen(fd, 'wb') as raw, gzip.open(raw, 'wt', encoding='utf-8') as stream:
            def write(item):
                stream.write(json.dumps(item, default=encode, ensure_ascii=False) + '\n')
            write({'kind': 'header', 'format': 'qrent-mysql-jsonl-v1',
                   'created_at': datetime.now(timezone.utc).isoformat()})
            for table in tables:
                cursor.execute('SHOW CREATE TABLE ' + quoted(table))
                ddl = cursor.fetchone()[1]
                cursor.execute('SELECT * FROM ' + quoted(table))
                columns = [item[0] for item in cursor.description]
                write({'kind': 'table', 'name': table, 'columns': columns, 'ddl': ddl})
                count = 0
                while True:
                    rows = cursor.fetchmany(500)
                    if not rows:
                        break
                    for row in rows:
                        write({'kind': 'row', 'table': table, 'values': row})
                        count += 1
                counts[table] = count
                write({'kind': 'table_end', 'name': table, 'count': count})
            write({'kind': 'end', 'counts': counts})
        verified = verify_archive(path)
        if verified != counts:
            raise ValueError('Archive verification failed')
        with path.open('rb') as saved:
            sha256 = hashlib.file_digest(saved, 'sha256').hexdigest()
        return {'status': 'read_only_archive_verified', 'path': str(path.resolve()),
                'counts': counts, 'bytes': path.stat().st_size,
                'sha256': sha256,
                'verification': 'gzip CRC, typed JSON, table counts and complete footer; no restore executed'}
    finally:
        connection.rollback()
        cursor.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env', type=Path, default=Path(__file__).resolve().parents[3] / '.env')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    connection = mysql.connector.connect(**config_from_env(args.env), connection_timeout=12,
                                         autocommit=False, allow_local_infile=False)
    try:
        receipt = archive(connection, args.output)
        receipt_path = args.output.with_suffix(args.output.suffix + '.receipt.json')
        receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
        receipt_path.chmod(0o600)
        print(json.dumps(receipt, ensure_ascii=False))
    finally:
        connection.close()


if __name__ == '__main__':
    main()

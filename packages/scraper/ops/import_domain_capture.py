#!/usr/bin/env python3
"""Validate and import a finished direct Domain capture; main-runner validation remains mandatory.

Only standard-library modules are used, so this tool never loads scraper env files.
The SQLite fields/upsert behavior follow src/services/sqlite_store.py.
"""
import argparse
import copy
import fcntl
import hashlib
import json
import os
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


CITY_STATES = {'sydney': 'NSW', 'melbourne': 'VIC'}
QUERY_FIELDS = {'source', 'city', 'region', 'atlas_id', 'low', 'high', 'beds'}
FIELDS = ('house_id', 'source', 'price_per_week', 'address_line1', 'address_line2', 'suburb', 'state',
          'postcode', 'bedroom_count', 'bathroom_count', 'parking_count', 'property_type',
          'property_type_raw', 'description_en', 'description_cn', 'keywords', 'url', 'thumbnail_url',
          'available_date', 'published_at', 'scraped_at')
KEEP_FIELDS = ('description_en', 'description_cn', 'keywords', 'thumbnail_url', 'available_date')
PROPERTY_UPSERT = (
    f"INSERT INTO properties ({','.join(FIELDS)},updated_at,is_active) "
    f"VALUES ({','.join('?' for _ in range(len(FIELDS) + 1))},1) "
    'ON CONFLICT(house_id,source) DO UPDATE SET ' + ','.join(
        f"{name}=" + (f"COALESCE(NULLIF(excluded.{name}, ''),properties.{name})" if name in KEEP_FIELDS else f'excluded.{name}')
        for name in FIELDS[2:]) + ',updated_at=excluded.updated_at,is_active=1')
ENRICHMENT_UPSERT = ('INSERT INTO property_enrichment (house_id,source,payload) VALUES (?,?,?) '
                     'ON CONFLICT(house_id,source) DO UPDATE SET payload=excluded.payload')


class ImportRejected(ValueError):
    pass


class TargetBusy(ImportRejected):
    pass


def require(condition, message):
    if not condition:
        raise ImportRejected(message)


def read_json(path):
    raw = Path(path).read_bytes()
    data = json.loads(raw)
    require(isinstance(data, dict), 'Capture files must contain JSON objects')
    return data, raw


def digest_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def read_database(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)


def query_key(query):
    require(isinstance(query, dict) and set(query) == QUERY_FIELDS, 'Unsupported query schema')
    require(query['source'] in {'domain', 'realestate'} and query['city'] in CITY_STATES, 'Unsupported query scope')
    return hashlib.sha256(json.dumps(query, sort_keys=True).encode()).hexdigest()[:20]


def validate_source(state, process):
    require(type(state.get('version')) is int and state['version'] == 1 and state.get('mode') == 'full',
            'Source must be a supported full capture')
    require(state.get('status') == 'success' and type(state.get('exit_code')) is int and state['exit_code'] == 0
            and state.get('complete_inventory') is True and state.get('errors') == []
            and state.get('queue') == [], 'Source full capture has not succeeded')
    require(process.get('state') == 'finished' and type(process.get('exit_code')) is int and process['exit_code'] == 0
            and process.get('timed_out', False) is False and not process.get('error'), 'Source process did not finish successfully')
    options = state['options']
    require(options.get('sources') == ['domain'], 'Only a Domain-only capture may be imported')
    require(isinstance(options.get('cities'), list) and set(options['cities']) <= CITY_STATES.keys(), 'Invalid source cities')
    traffic = state['traffic']
    require(all(type(traffic.get(key)) is int and traffic[key] == 0
                for key in ('proxy_requests', 'proxy_bytes', 'proxy_wire_bytes', 'proxy_connections')),
            'Source must have zero paid proxy requests, connections and bytes')
    require(all(item.get('mode') != 'proxy' or (item.get('bytes') == 0 and item.get('connections') == 0)
                for item in traffic.get('wire_hosts', [])), 'Source contains paid wire traffic')
    queries, seen, roots = state['queries'], state['seen'], options['root_queries']
    require(isinstance(queries, dict) and isinstance(seen, dict) and isinstance(roots, list)
            and roots and len(set(roots)) == len(roots), 'Invalid source query graph')
    reached, active = set(), set()
    coverage = {}

    def walk(key, city=None):
        require(key in queries and key not in active, 'Missing query or cycle in source graph')
        item = queries[key]
        query = item['query']
        require(query_key(query) == key and query['source'] == 'domain'
                and query['city'] in options['cities'] and (city is None or query['city'] == city),
                'Query identity or city does not match its graph')
        if key in coverage:
            return coverage[key]
        active.add(key)
        reached.add(key)
        ids = item['ids']
        require(isinstance(ids, list) and all(isinstance(value, str) and value and ':' not in value for value in ids)
                and len(set(ids)) == len(ids), 'Invalid query listing IDs')
        for house_id in ids:
            require(seen.get('domain:' + house_id) == ['domain', query['city']], 'Query ID and seen city disagree')
        total, first = item['total'], item['first_total']
        require(type(total) is int and type(first) is int and min(total, first) >= 0, 'Invalid query totals')
        children = item.get('children', [])
        require(isinstance(children, list) and len(children) == len(set(children)), 'Invalid query children')
        result = set(ids)
        if children:
            require(item.get('status') == 'split', 'Nonterminal split query')
            for child in children:
                result.update(walk(child, query['city']))
        else:
            require(item.get('status') == 'done' and item.get('truncated') is False, 'Incomplete or truncated source leaf')
        require(len(result) >= min(total, first), 'Source query has a unique-listing coverage shortfall')
        active.remove(key)
        coverage[key] = result
        return result

    all_ids = set()
    for root in roots:
        all_ids.update(walk(root))
    require(reached == set(queries), 'Source has unreachable query nodes')
    require(set(seen) == {'domain:' + value for value in all_ids}
            and type(state.get('listings')) is int and state['listings'] == len(seen),
            'Source seen IDs do not match covered queries')
    return state


def source_payloads(database, state):
    payloads = {}
    with closing(read_database(database)) as connection:
        connection.execute('BEGIN')
        for identity, (source, city) in state['seen'].items():
            house_id = identity.removeprefix('domain:')
            row = connection.execute(
                'SELECT p.state,e.payload,' + ','.join('p.' + name for name in KEEP_FIELDS) +
                ' FROM properties p JOIN property_enrichment e '
                'ON p.house_id=e.house_id AND p.source=e.source WHERE p.house_id=? AND p.source=?',
                (house_id, 'domain')).fetchone()
            require(row is not None, 'Source seen listing is missing from SQLite')
            data = json.loads(row[1])
            require(isinstance(data, dict) and set(FIELDS) <= data.keys(), 'Incomplete enrichment payload schema')
            require(set(data) <= set(FIELDS) | {'average_score', 'scores', 'commute_times', 'price_text'}, 'Unsupported enrichment fields')
            require(isinstance(data.get('price_text', ''), str), 'Unsupported source price quote')
            require(source == 'domain' and data['source'] == 'domain' and data['house_id'] == house_id
                    and data['state'] == row[0] == CITY_STATES[city]
                    and data.get('city', city) == city, 'Source payload identity or city/state mismatch')
            for name, value in zip(KEEP_FIELDS, row[2:]):
                if not data.get(name) and value:
                    data[name] = value
            require(all(data[name] is None or isinstance(data[name], (str, int, float)) for name in FIELDS),
                    'Unsupported SQLite payload value')
            for name in ('scraped_at', 'published_at', 'available_date'):
                if data[name]:
                    datetime.fromisoformat(data[name])
            payloads[house_id] = data
    return payloads


def prepare_target(target, source, payloads, database, connection, capture):
    require(type(target.get('version')) is int and target['version'] == source['version'] and target.get('mode') == 'full',
            'Target must be the same full-capture version')
    options, source_options = target['options'], source['options']
    require(options.get('scope_hash') and options.get('scope_hash') == source_options.get('scope_hash')
            and type(options.get('planning_version')) is int
            and options.get('planning_version') == source_options.get('planning_version'), 'Capture scope or planning version differs')
    require('domain' in options['sources'] and set(options['cities']) <= set(source_options['cities']), 'Source does not cover target cities')
    require(Path(options['db']).resolve() == Path(database).resolve(), 'Target DB does not match its checkpoint')
    require(target.get('status') not in {'success', 'pilot_success'} and target.get('complete_inventory') is not True,
            'Target is already complete; refusing to invalidate its completed capture')
    target_queries = target['queries']
    for key, item in target_queries.items():
        require(query_key(item['query']) == key, 'Target query identity is invalid')
    roots = options['root_queries']
    require(len(roots) == len(set(roots)) and all(key in target_queries for key in roots), 'Invalid target roots')
    domain_roots = {key for key in roots if target_queries[key]['query']['source'] == 'domain'}
    require(domain_roots == set(source_options['root_queries']), 'Domain root query scopes differ')
    require(all(key in target_queries for key in target['queue']), 'Target queue contains missing queries')
    old_domain = {key for key, item in target_queries.items() if item['query']['source'] == 'domain'}
    require(not (set(source['queries']) & (set(target_queries) - old_domain)), 'Imported queries collide with another source')
    # Compile both statements against the real target schema before backup or writes.
    connection.execute('EXPLAIN ' + PROPERTY_UPSERT, [None] * (len(FIELDS) + 1)).fetchone()
    connection.execute('EXPLAIN ' + ENRICHMENT_UPSERT, [None] * 3).fetchone()
    rows = []
    now = datetime.now().isoformat()
    for house_id, payload in payloads.items():
        data = copy.deepcopy(payload)
        previous_sql = connection.execute('SELECT ' + ','.join(KEEP_FIELDS) +
            ' FROM properties WHERE house_id=? AND source=?', (house_id, 'domain')).fetchone()
        sql_details = dict(zip(KEEP_FIELDS, previous_sql)) if previous_sql else {}
        previous = connection.execute('SELECT payload FROM property_enrichment WHERE house_id=? AND source=?',
                                      (house_id, 'domain')).fetchone()
        old = {}
        if previous:
            old = json.loads(previous[0])
            require(isinstance(old, dict), 'Invalid existing Domain enrichment payload')
        for name in KEEP_FIELDS:
            if not data.get(name):
                data[name] = old.get(name) or sql_details.get(name) or data.get(name)
        for name in ('average_score', 'scores'):
            if not data.get(name) and old.get(name):
                data[name] = old[name]
        data['commute_times'] = {**old.get('commute_times', {}), **data.get('commute_times', {})}
        require(all(data[name] is None or isinstance(data[name], (str, int, float)) for name in FIELDS),
                'Existing enrichment cannot be safely merged into SQLite')
        rows.append(([data[name] for name in FIELDS] + [now],
                     (house_id, 'domain', json.dumps(data, ensure_ascii=False))))
    updated = copy.deepcopy(target)
    updated['queries'] = {key: item for key, item in updated['queries'].items() if key not in old_domain}
    updated['queries'].update(copy.deepcopy(source['queries']))
    updated['queue'] = [key for key in updated['queue'] if key not in old_domain]
    updated['seen'] = {key: value for key, value in updated['seen'].items() if not key.startswith('domain:')}
    updated['seen'].update(copy.deepcopy(source['seen']))
    updated.update(status='partial', exit_code=1, complete_inventory=False, listings=len(updated['seen']))
    for name in ('quality', 'coverage', 'completed_at', 'new_listings', 'reverse_reused'):
        updated.pop(name, None)
    updated['errors'] = [error for error in updated.get('errors', []) if not isinstance(error, dict)
                         or (error.get('source') != 'domain' and error.get('query') not in old_domain)]
    updated['errors'].append({'source': 'domain', 'stage': 'external_import', 'error': 'Pending main runner validation'})
    captures = updated.setdefault('external_captures', [])
    require(isinstance(captures, list), 'Invalid external capture metadata')
    if not any(item.get('id') == capture['id'] for item in captures):
        captures.append(capture)
    updated['updated_at'] = now
    return updated, rows


def atomic_json(path, value):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix=path.name + '.', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def pending_report(state):
    """Use the main runner's public projection, with the pending import status."""
    report = {key: value for key, value in state.items() if key not in {'initial', 'seen', 'queue', 'queries'}}
    report['queries'] = [{key: value for key, value in item.items() if key not in {'ids', 'hashes'}}
                         for item in state['queries'].values()]
    report['counts'] = {}
    for source, city in state['seen'].values():
        name = source + ':' + city
        report['counts'][name] = report['counts'].get(name, 0) + 1
    return report


def backup_target(connection, database, output, state_bytes):
    tag = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
    folder = database.parent / 'backup' / ('pre-domain-import-' + tag)
    folder.mkdir(mode=0o700, parents=True)
    with closing(sqlite3.connect(folder / database.name)) as destination:
        connection.backup(destination)
    (folder / database.name).chmod(0o600)
    (folder / 'state.json').write_bytes(state_bytes)
    if (output / 'result.json').exists():
        (folder / 'result.json').write_bytes((output / 'result.json').read_bytes())
    return folder


def import_capture(*, source_state, source_db, source_process, target_output, target_db, apply=False):
    source_state, source_db, source_process, target_output, target_db = map(
        Path, (source_state, source_db, source_process, target_output, target_db))
    require(source_db.resolve() != target_db.resolve() and source_state.resolve() != (target_output / 'state.json').resolve(),
            'Source and target must be separate captures')
    try:
        source, source_bytes = read_json(source_state)
        process, process_bytes = read_json(source_process)
        validate_source(source, process)
        payloads = source_payloads(source_db, source)
        fingerprints = {'source_state_sha256': hashlib.sha256(source_bytes).hexdigest(),
                        'source_process_sha256': hashlib.sha256(process_bytes).hexdigest(),
                        'source_db_sha256': digest_file(source_db),
                        'source_payload_sha256': hashlib.sha256(json.dumps(payloads, sort_keys=True).encode()).hexdigest()}
        capture = {'id': hashlib.sha256(json.dumps(fingerprints, sort_keys=True).encode()).hexdigest(),
                   'source': 'domain', 'imported_at': datetime.now(timezone.utc).isoformat(),
                   'listings': len(payloads), 'cities': source['options']['cities'],
                   'pages_visited': source.get('pages_visited'), 'traffic': source['traffic'],
                   'source_files': {'state': str(source_state.resolve()), 'database': str(source_db.resolve()),
                                    'process': str(source_process.resolve())}, **fingerprints}

        def plan():
            target, target_bytes = read_json(target_output / 'state.json')
            if (target_output / 'result.json').exists():
                result, _ = read_json(target_output / 'result.json')
                require(result.get('status') not in {'success', 'pilot_success'} and result.get('complete_inventory') is not True,
                        'Target report is already complete')
            connection = read_database(target_db)
            try:
                connection.execute('BEGIN')
                updated, rows = prepare_target(target, source, payloads, target_db, connection, capture)
            except BaseException:
                connection.close()
                raise
            return connection, updated, rows, target_bytes

        connection, updated, rows, target_bytes = plan()
        connection.close()
        receipt = {'status': 'dry_run', 'listings': len(payloads), 'requires_main_runner_validation': True,
                   'capture_id': capture['id'], **fingerprints}
        if not apply:
            return receipt
        previous_umask = os.umask(0o077)
        try:
            with (target_db.parent / '.capture.lock').open('a') as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as error:
                    raise TargetBusy('Main capture is running; stop it before applying an import') from error
                connection, updated, rows, target_bytes = plan()
                with closing(connection):
                    folder = backup_target(connection, target_db, target_output, target_bytes)
                with closing(sqlite3.connect(target_db)) as destination, destination:
                    for properties, enrichment in rows:
                        destination.execute(PROPERTY_UPSERT, properties)
                        destination.execute(ENRICHMENT_UPSERT, enrichment)
                # Commit rows before publishing their checkpoint. A failed JSON
                # write leaves the previous partial checkpoint safe to reapply.
                atomic_json(target_output / 'state.json', updated)
                atomic_json(target_output / 'result.json', pending_report(updated))
                receipt.update(status='pending_validation', backup=str(folder))
                atomic_json(target_output / 'import-receipt.json', receipt)
                return receipt
        finally:
            os.umask(previous_umask)
    except ImportRejected:
        raise
    except (KeyError, TypeError, ValueError, AttributeError, RecursionError) as error:
        raise ImportRejected('Malformed capture data: ' + type(error).__name__) from error


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source-state', 'source-db', 'source-process', 'target-output', 'target-db'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--apply', action='store_true', help='Back up and apply; default validates without writing')
    return parser


def main():
    try:
        receipt = import_capture(**vars(build_parser().parse_args()))
    except (ImportRejected, sqlite3.Error, OSError) as error:
        print(json.dumps({'status': 'rejected', 'error': str(error)}))
        return 75 if isinstance(error, TargetBusy) else 1
    print(json.dumps(receipt, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

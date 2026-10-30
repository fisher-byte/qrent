#!/usr/bin/env python3
"""Recover only failed Domain queries; preserve and prove the original daily window."""
import argparse
import copy
from contextlib import contextmanager
from datetime import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.services.school_scope import capture_areas, schools_for_area


class IncompleteCapture(ValueError):
    """The original capture still has failed or uncertain work; do not publish."""


def queries():
    result = [(source, area) for source in ('domain', 'realestate')
              for area in capture_areas(['sydney'], source)]
    if len(result) != 41 or len(set(result)) != 41:
        raise ValueError('Original Sydney query configuration changed; review required')
    return result


def read_json(path):
    path = Path(path)
    if path.is_symlink() or path.resolve() != path.absolute():
        raise ValueError('Capture evidence must not use symlinks')
    return json.loads(path.read_text())


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def freeze(path, value):
    path = Path(path)
    if path.exists():
        if read_json(path) != value:
            raise ValueError('Immutable capture recovery evidence changed')
        return
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with path.open('x') as stream:
        os.chmod(path, 0o600)
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())


def campaign_root(original):
    original = Path(original).absolute()
    if not re.fullmatch(r'\d{8}_\d{6}_\d{6}_result\.json', original.name):
        raise ValueError('Recovery requires the original full-run result filename')
    run = original.name.removesuffix('_result.json')
    if original.parent.name != 'school-daily-' + run[:8] or original.parent.parent.name != 'output':
        raise ValueError('Original capture must remain in its original daily directory')
    return original.parent.parent.parent


def validate_data(data, expected, run, allow_empty=False):
    if (data.get('run') != run or not re.fullmatch(r'\d{8}_\d{6}_\d{6}', run)
            or data.get('school_scope') is not True or data.get('requested_cities') != ['sydney']
            or data.get('max_pages_per_area') != 7):
        raise ValueError('Capture run, Sydney scope or seven-page window changed')
    areas = data.get('areas', [])
    actual = [(item['source'], item['area']) for item in areas]
    if len(actual) != len(expected) or len(set(actual)) != len(actual) or set(actual) != set(expected):
        raise ValueError('Capture does not cover exactly its original query manifest')
    for item in areas:
        if type(item.get('count')) is not int or item['count'] < 0:
            raise ValueError('Invalid captured query count')
        if not item.get('error') and item.get('query_schools') != schools_for_area(item['area'], item['source']):
            raise ValueError('Query school provenance changed')
    identities = data.get('captured_source_ids', [])
    normalized = [(source, str(identity)) for source, identity in identities]
    expected_sources = {source for source, _ in expected}
    if ((not normalized and not allow_empty) or len(set(normalized)) != len(normalized)
            or any(source not in expected_sources or not identity.isdigit() for source, identity in normalized)
            or data.get('listings') != len(normalized)):
        raise ValueError('Capture identity set does not match its recorded listings')
    for source in expected_sources:
        counts = [item['count'] for item in areas if item['source'] == source]
        distinct = len({identity for item_source, identity in normalized if item_source == source})
        # Areas may overlap. Their observations need not sum to distinct homes.
        if not max(counts) <= distinct <= sum(counts):
            raise ValueError('Source identity count disagrees with its query counts')
    cohorts = data.get('school_cohorts')
    if not isinstance(cohorts, dict):
        raise ValueError('Capture school provenance is missing')
    cohort_ids = set()
    for school, sources in cohorts.items():
        if school not in {'UNSW', 'USYD', 'UTS'} or not isinstance(sources, dict):
            raise ValueError('Unknown Sydney school in capture evidence')
        for source, ids in sources.items():
            if (source not in expected_sources or not isinstance(ids, list)
                    or any(not str(value).isdigit() for value in ids)):
                raise ValueError('Invalid captured school identity')
            cohort_ids.update((source, str(value)) for value in ids)
    if cohort_ids != set(normalized):
        raise ValueError('School-cohort identities must exactly cover captured source identities')
    errors = data.get('errors')
    if not isinstance(errors, list):
        raise ValueError('Capture errors were not recorded')
    complete = not errors and all(not item.get('error') for item in areas)
    if complete != (data.get('status') == 'success' and data.get('exit_code') == 0):
        raise ValueError('Capture completion disagrees with its query/error evidence')
    partial_status = 'partial' if normalized else 'failed'
    if not complete and (data.get('status') != partial_status or data.get('exit_code') != 1):
        raise ValueError('Incomplete capture must retain partial/1')
    return complete


def failed_domain_areas(data):
    failures = [item for item in data['areas'] if item.get('error')]
    expected_errors = [{'source': item['source'], 'area': item['area'], 'error': item['error']}
                       for item in failures]
    if (any(item['source'] != 'domain' or item['count'] != 0 for item in failures)
            or data['errors'] != expected_errors):
        raise IncompleteCapture('Non-Domain or non-listing failure requires review; no recovery requests')
    return [item['area'] for item in failures]


def ordinary_environment(environment, release):
    result = dict(environment, SCRAPER_ENV_FILE=str(release / '.env'),
                  DOMAIN_BROWSER_PROXY='socks5://127.0.0.1:18082', DOMAIN_NATIVE_NETWORK='true')
    for name in ('PROXY_SERVER', 'PROXY_USERNAME', 'PROXY_PASSWORD', 'PROXY_SOURCES',
                 'PROPERTY_RATING_API_KEY', 'DASHSCOPE_API_KEY', 'QWEN_API_KEY', 'OPENAI_API_KEY',
                 'GOOGLE_MAPS_API_KEY', 'NSW_API_KEY', 'DISTANCE_MATRIX_API_KEY'):
        result[name] = ''
    for name in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'):
        result.pop(name, None)
    return result


def retry_command(release, root, original, areas, folder):
    return ['timeout', '--kill-after=30s', '20m', 'xvfb-run', '-a',
            str(release / '.venv/bin/python'), '-u', str(release / 'recovery_runner.py'),
            '--run-tag', original['run'], '--school-scope', '--cities', 'sydney',
            '--scrapers', 'domain', '--areas', *areas, '--pages', '7', '--limit', '2000',
            '--no-details', '--retries', '0', '--max-requests', '10000', '--proxy-bytes', '1',
            '--db', str(root / 'properties.db'), '--output', str(folder)]


def run_round(root, original, areas, folder, lock_fd):
    release = root / 'releases/recovery-20261027'
    command = retry_command(release, root, original, areas, folder)
    with (folder / 'capture.log').open('ab') as log:
        done = subprocess.run(command, cwd=release, env=ordinary_environment(os.environ, release),
                              stdout=log, stderr=subprocess.STDOUT, pass_fds=(lock_fd,), timeout=1250)
    path = folder / (original['run'] + '_result.json')
    if done.returncode not in (0, 1) or not path.is_file():
        raise IncompleteCapture('Recovery child has no trustworthy completed result; preserve its started request')
    return path


@contextmanager
def capture_lock(root, supplied=None):
    path = root / '.capture.lock'
    owned = supplied is None
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600) if owned else supplied
    try:
        current, expected = os.fstat(fd), path.stat()
        if (current.st_dev, current.st_ino) != (expected.st_dev, expected.st_ino):
            raise ValueError('Inherited capture lock is not the original lock file')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield fd
    finally:
        if owned:
            os.close(fd)


def source_fingerprint(root):
    release = root / 'releases/recovery-20261027'
    paths = {release / 'recovery_runner.py', Path(__file__).resolve()}
    excluded = {'__pycache__', '.venv', 'venv', '.pytest_cache', '.mypy_cache'}
    paths.update(path for path in (release / 'src').rglob('*.py')
                 if not excluded.intersection(path.relative_to(release).parts))
    paths.update((release / 'src/config').glob('*.json'))
    paths.update(path for path in (release / 'ops/recover_school_capture.py',
                                  release / 'ops/run_school_capture.sh') if path.exists())
    for path in paths:
        if path.is_symlink() or path.resolve() != path.absolute():
            raise ValueError('Capture source fingerprint must not follow symlinks')
    # Only public capture sources/configuration: no .env, venv or cache files.
    return {str(path): digest(path) for path in sorted(paths)}


def require_inventory(path, identities=None):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise IncompleteCapture('Original properties.db is missing or linked; do not create a replacement')
    if identities is None:
        return
    expected = {(source, str(identity)) for source, identity in identities}
    try:
        with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as db:
            present = {(source, str(identity)) for source, identity in
                       db.execute('SELECT source,house_id FROM properties')}
    except sqlite3.Error as error:
        raise IncompleteCapture('Original captured inventory cannot be read; no enrichment allowed') from error
    if expected - present:
        raise IncompleteCapture('Original inventory is missing captured identities; no enrichment allowed')


def recovery_paths(original):
    run = original.name.removesuffix('_result.json')
    return original.parent / ('recovery-' + run), original.parent / (run + '_recovered_result.json')


def request_value(original, base, number, areas):
    return {'version': 1, 'original_sha256': digest(original), 'run': base['run'], 'round': number,
            'areas': areas, 'mode': 'domain-ordinary-no-details-v1'}


def merge_round(current, data):
    result = copy.deepcopy(current)
    recovered = {(item['source'], item['area']): item for item in data['areas'] if not item.get('error')}
    result['areas'] = [copy.deepcopy(recovered.get((item['source'], item['area']), item))
                       for item in result['areas']]
    result['errors'] = [item for item in result['errors'] if (item['source'], item['area']) not in recovered]
    seen = {(source, str(identity)) for source, identity in result['captured_source_ids']}
    for source, identity in data['captured_source_ids']:
        if (source, str(identity)) not in seen:
            result['captured_source_ids'].append([source, identity])
            seen.add((source, str(identity)))
    for school, sources in data['school_cohorts'].items():
        for source, ids in sources.items():
            dest = result['school_cohorts'].setdefault(school, {}).setdefault(source, [])
            existing = {str(identity) for identity in dest}
            for identity in ids:
                if str(identity) not in existing:
                    dest.append(identity)
                    existing.add(str(identity))
    result['listings'] = len(result['captured_source_ids'])
    complete = not result['errors']
    result.update(status='success' if complete else 'partial', exit_code=0 if complete else 1,
                  capture_window_complete=complete, complete_inventory=False)
    return result


def validate_round(path, base, areas):
    data = read_json(path)
    validate_data(data, [('domain', area) for area in areas], base['run'], allow_empty=True)
    failed_domain_areas(data)
    if (data.get('scored') != 0 or data.get('detail_requests') != 0
            or any(data.get('traffic', {}).get(name) != 0
                   for name in ('proxy_requests', 'proxy_bytes', 'proxy_wire_bytes'))):
        raise ValueError('Recovery evidence does not prove zero model/detail/residential use')
    if any(source != 'domain' for source, _ in data['captured_source_ids']):
        raise ValueError('Recovery introduced a non-Domain identity')
    return data


def replay(root, original, base, folder, record_receipts=False):
    job = read_json(folder / 'job.json')
    expected = {'version': 1, 'run': base['run'], 'original_report': str(original),
                'original_sha256': digest(original), 'queries': [list(item) for item in queries()],
                'source_files': source_fingerprint(root)}
    if job != expected:
        raise ValueError('Original capture, recovery source or query manifest changed')
    result, evidence = copy.deepcopy(base), []
    for number in (1, 2):
        path = folder / ('round-' + str(number))
        request = path / 'request.json'
        if not request.exists():
            if path.exists() or any((folder / ('round-' + str(later))).exists()
                                    for later in range(number + 1, 3)):
                raise ValueError('Recovery round is missing its original request or earlier round')
            break
        areas = failed_domain_areas(result)
        if not areas or read_json(request) != request_value(original, base, number, areas):
            raise ValueError('Recovery request does not match the remaining failed queries')
        raw = path / (base['run'] + '_result.json')
        if not raw.is_file():
            raise IncompleteCapture('Started recovery round is uncertain; automatic repetition is forbidden')
        child = validate_round(raw, base, areas)
        proof = {'round': number, 'areas': areas, 'report': str(raw), 'sha256': digest(raw),
                 'request_sha256': digest(request)}
        receipt = path / 'receipt.json'
        if record_receipts:
            freeze(receipt, proof)
        elif read_json(receipt) != proof:
            raise ValueError('Recovery result differs from its durable receipt')
        result = merge_round(result, child)
        evidence.append(proof)
    return result, evidence


def aggregate_value(original, base, result, evidence, output):
    value = copy.deepcopy(result)
    value['report'] = str(output)
    value['recovery'] = {'version': 1, 'original_report': str(original),
                         'original_sha256': digest(original), 'run': base['run'],
                         'expected_queries': [list(item) for item in queries()], 'rounds': evidence}
    return value


def validate_complete_capture(path):
    path = Path(path).absolute()
    if path.is_symlink():
        raise ValueError('Capture evidence must not use symlinks')
    path = path.resolve()
    data = read_json(path)
    if path.name == str(data.get('run')) + '_result.json':
        campaign_root(path)
        if not validate_data(data, queries(), data['run']):
            raise IncompleteCapture('The latest original capture remains partial')
        return data
    run = data.get('run', '')
    if path.name != run + '_recovered_result.json':
        raise ValueError('Unrecognized daily capture evidence')
    original = path.parent / (run + '_result.json')
    root = campaign_root(original)
    base = read_json(original)
    validate_data(base, queries(), run)
    folder, expected_output = recovery_paths(original)
    result, evidence = replay(root, original, base, folder)
    if not evidence or data != aggregate_value(original, base, result, evidence, expected_output):
        raise ValueError('Aggregate does not match its immutable original and recovery evidence')
    if not validate_data(data, queries(), run):
        raise IncompleteCapture('Recovery has not completed all 41 original queries')
    return data


def select_complete_capture(folder):
    originals = [path for path in Path(folder).glob('*_result.json')
                 if re.fullmatch(r'\d{8}_\d{6}_\d{6}_result\.json', path.name)]
    if not originals:
        raise IncompleteCapture('No original Sydney capture result; no API requests')
    original = max(originals, key=lambda path: path.name)
    _, output = recovery_paths(original)
    chosen = output if output.exists() else original
    validate_complete_capture(chosen)
    return chosen


def recover(root, original, lock_fd=None, run_round=None):
    root, original = Path(root).resolve(), Path(original).absolute()
    if original.is_symlink():
        raise ValueError('Original capture evidence must not use symlinks')
    original = original.resolve()
    with capture_lock(root, lock_fd) as fd:
        require_inventory(root / 'properties.db')
        if campaign_root(original) != root:
            raise ValueError('Recovery must use the original crawler root and SQLite inventory')
        base = read_json(original)
        complete = validate_data(base, queries(), base['run'])
        if complete:
            return original
        failed_domain_areas(base)
        folder, output = recovery_paths(original)
        if output.exists():
            validate_complete_capture(output)
            return output
        freeze(folder / 'job.json', {'version': 1, 'run': base['run'], 'original_report': str(original),
               'original_sha256': digest(original), 'queries': [list(item) for item in queries()],
               'source_files': source_fingerprint(root)})
        result, evidence = replay(root, original, base, folder, record_receipts=True)
        execute = run_round or globals()['run_round']
        while failed_domain_areas(result) and len(evidence) < 2:
            number = len(evidence) + 1
            areas = failed_domain_areas(result)
            round_folder = folder / ('round-' + str(number))
            freeze(round_folder / 'request.json', request_value(original, base, number, areas))
            raw = execute(root, base, areas, round_folder, fd)
            if Path(raw).absolute() != round_folder / (base['run'] + '_result.json'):
                raise ValueError('Recovery child returned an unrelated result path')
            result, evidence = replay(root, original, base, folder, record_receipts=True)
        if failed_domain_areas(result):
            raise IncompleteCapture('Two recovery rounds exhausted; original inventory and partial report kept')
        freeze(output, aggregate_value(original, base, result, evidence, output))
        validate_complete_capture(output)
        return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/root/qrent-scraper'))
    parser.add_argument('--capture-result', type=Path, required=True)
    parser.add_argument('--capture-lock-fd', type=int)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        today = datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d')
        if args.capture_result.parent.name != 'school-daily-' + today:
            raise ValueError('Only the current daily window may trigger daily enrichment')
        output = recover(args.root, args.capture_result, args.capture_lock_fd)
    except BlockingIOError:
        print(json.dumps({'status': 'capture_still_running', 'enrichment_allowed': False}))
        return 75
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(json.dumps({'status': 'incomplete', 'enrichment_allowed': False, 'reason': str(error)}))
        return 1
    print(json.dumps({'status': 'complete_window_verified', 'capture_result': str(output),
                      'enrichment_allowed': True}))
    return 0


if __name__ == '__main__':
    sys.exit(main())

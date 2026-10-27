#!/usr/bin/env python3
"""Checkpointed metro inventory; Domain direct, Real Estate critical requests via AU."""
import argparse
import csv
import fcntl
import gzip
import hashlib
import json
import logging
import os
import shutil
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

from recovery_runner import verify_au_proxy
from src.scrapers import DomainScraper, RealEstateScraper
from src.services.incremental import complete_postcodes, reuse_details
from src.services.search_plan import SearchQuery, SearchMetadataError, metro_queries, read_snapshot
from src.services.sqlite_store import SQLiteStore
from src.utils.browser import BrowserManager, BrowserType
from src.utils.network import TrafficBudget, set_run_budget

logger = logging.getLogger(__name__)
FACTORIES = {'domain': DomainScraper, 'realestate': RealEstateScraper}
PLANNING_VERSION = 1


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    temp.replace(path)


def signature(value):
    fields = ('price_per_week', 'address_line1', 'suburb', 'state', 'postcode',
              'bedroom_count', 'bathroom_count', 'parking_count', 'property_type')
    return hashlib.sha256(json.dumps([value.get(k) for k in fields], default=str).encode()).hexdigest()


def restore_budget(saved, max_requests, max_bytes):
    budget = TrafficBudget(max_requests=max_requests, max_proxy_bytes=max_bytes)
    for key, value in saved.items():
        if key in budget.__dataclass_fields__ and not key.startswith('_') and key not in {'max_requests', 'max_proxy_bytes'}:
            setattr(budget, key, value)
    budget._wire_hosts = {(item['mode'], item['host']): dict(item) for item in saved.get('wire_hosts', [])}
    budget.exhausted = (max(budget.proxy_bytes, budget.proxy_wire_bytes) >= max_bytes
                        or budget.requests >= max_requests
                        or budget.proxy_connections + budget.direct_connections >= max_requests)
    return budget


def campaign_options(args, queries):
    scope_hash = hashlib.sha256((Path(__file__).parent / 'src/config/metro_regions.json').read_bytes()).hexdigest()
    return {'cities': args.cities, 'sources': args.scrapers, 'proxy_bytes': args.proxy_bytes,
            'max_requests': args.max_requests, 'db': str(args.db.resolve()),
            'pilot_pages': args.pilot_pages, 'pilot_regions': args.pilot_regions,
            'scope_hash': scope_hash, 'planning_version': PLANNING_VERSION,
            'root_queries': sorted(query.key for query in queries)}


def unfinished_children(state, key):
    for child in state['queries'][key].get('children', []):
        if state['queries'][child]['status'] not in {'done', 'split'} or unfinished_children(state, child):
            return True
    return False


def initial_state(args, mode, queries, store):
    columns = ('house_id', 'source', 'price_per_week', 'address_line1', 'suburb', 'state', 'postcode',
               'bedroom_count', 'bathroom_count', 'parking_count', 'property_type')
    initial = {}
    for row in store.connection.execute('SELECT ' + ','.join(columns) + ' FROM properties'):
        data = dict(zip(columns, row))
        initial[f"{data['source']}:{data['house_id']}"] = signature(data)
    return {
        'version': 1, 'mode': mode, 'status': 'running', 'exit_code': None,
        'started_at': datetime.now().isoformat(),
        'options': campaign_options(args, queries),
        'queue': [query.key for query in queries],
        'queries': {query.key: {'query': query.to_dict(), 'next_page': 1, 'status': 'pending',
                               'ids': [], 'hashes': [], 'pages_visited': 0, 'unchanged_pages': 0}
                    for query in queries},
        'initial': initial,
        'seen': {}, 'pages_visited': 0, 'detail_requests': 0, 'details_failed': 0,
        'reused': 0, 'postcodes_completed': 0, 'traffic': {}, 'errors': [],
    }


def checkpoint(args, state, budget):
    state['updated_at'] = datetime.now().isoformat()
    state['traffic'] = budget.snapshot()
    state['listings'] = len(state['seen'])
    atomic_json(args.output / 'state.json', state)
    # Keep the public-sized report free of checkpoint identifiers and fingerprints.
    report = {k: v for k, v in state.items() if k not in {'initial', 'seen', 'queue', 'queries'}}
    report['queries'] = [{k: v for k, v in item.items() if k not in {'ids', 'hashes'}}
                         for item in state['queries'].values()]
    report['counts'] = {}
    for source_city in state['seen'].values():
        label = source_city[0] + ':' + source_city[1]
        report['counts'][label] = report['counts'].get(label, 0) + 1
    atomic_json(args.output / 'result.json', report)


def export_rows(args, state, store):
    streams, writers = {}, {}
    quality = {}
    try:
        for house_id, source, payload in store.connection.execute('SELECT house_id,source,payload FROM property_enrichment'):
            info = state['seen'].get(source + ':' + house_id)
            if not info:
                continue
            name = source + '_' + info[1]
            data = json.loads(payload)
            stats = quality.setdefault(name, {'listings': 0, 'with_description': 0, 'with_availability': 0,
                                             'with_thumbnail': 0, 'missing_postcodes': 0, 'unknown_price': 0})
            stats['listings'] += 1
            stats['with_description'] += bool(data.get('description_en'))
            stats['with_availability'] += bool(data.get('available_date'))
            stats['with_thumbnail'] += bool(data.get('thumbnail_url'))
            stats['missing_postcodes'] += not bool(data.get('postcode'))
            stats['unknown_price'] += not bool(data.get('price_per_week'))
            if name not in streams:
                streams[name] = gzip.open(args.output / (name + '.csv.gz'), 'wt', encoding='utf-8-sig', newline='')
                writers[name] = csv.DictWriter(streams[name], fieldnames=list(data))
                writers[name].writeheader()
            writers[name].writerow(data)
    finally:
        for stream in streams.values():
            stream.close()
    state['quality'] = quality


def capture(args):
    os.umask(0o077)
    args.output.mkdir(parents=True, exist_ok=True)
    args.db.parent.mkdir(parents=True, exist_ok=True)
    lock = (args.db.parent / '.capture.lock').open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        return {'status': 'already_running', 'exit_code': 75}
    store = SQLiteStore(args.db)
    budget, state = None, None
    browser = None
    domain_browser = None
    domain_profile = args.output / 'profiles' / 'domain-session'
    baseline_path = args.db.parent / 'metro-baseline.json'
    try:
        baseline = json.loads(baseline_path.read_text()) if baseline_path.exists() else {}
        scope_hash = hashlib.sha256((Path(__file__).parent / 'src/config/metro_regions.json').read_bytes()).hexdigest()
        queries = metro_queries(args.cities, args.scrapers)
        baseline_matches = (baseline.get('scope_hash') == scope_hash
                            and baseline.get('planning_version') == PLANNING_VERSION
                            and set(baseline.get('root_queries', [])) >= {query.key for query in queries}
                            and set(baseline.get('cities', [])) >= set(args.cities)
                            and set(baseline.get('sources', [])) >= set(args.scrapers))
        state_path = args.output / 'state.json'
        saved_state = json.loads(state_path.read_text()) if state_path.exists() else None
        mode = args.mode
        if mode == 'auto':
            age = time.time() - baseline.get('completed_timestamp', 0)
            mode = saved_state['mode'] if saved_state else ('incremental' if baseline_matches and age < 7 * 86400 else 'full')
        if mode == 'incremental' and not baseline_matches:
            raise RuntimeError('A completed full metro scan is required before incremental capture')
        if mode == 'pilot':
            queries = [query for query in queries if query.source == 'domain' or
                       query in [q for q in queries if q.source == 'realestate' and q.city == query.city][:args.pilot_regions]]
        state = saved_state or initial_state(args, mode, queries, store)
        expected_options = campaign_options(args, queries)
        fixed_options = set(expected_options) - {'proxy_bytes', 'max_requests'}
        if state.get('version') != 1 or any(state['options'].get(key) != expected_options[key] for key in fixed_options) or state['mode'] != mode:
            raise RuntimeError('Checkpoint options differ; use a new output directory for a new campaign')
        if any(expected_options[key] < state['options'][key] for key in ('proxy_bytes', 'max_requests')):
            raise RuntimeError('A resumed campaign cannot lower its recorded budget ceilings')
        state['options'].update({key: expected_options[key] for key in ('proxy_bytes', 'max_requests')})
        if state['status'] in {'success', 'pilot_success'}:
            return {k: state[k] for k in ('status', 'exit_code', 'listings')}
        state['status'], state['exit_code'] = 'running', None
        # Failed queries are retried only on an explicit resume; successful pages are retained.
        state['errors'] = []
        # Repair older checkpoints that rewound a parent because a child failed.
        # Its successful partitions must survive the explicit resume.
        for key, item in state['queries'].items():
            if item['status'] == 'failed' and item.get('children') and unfinished_children(state, key):
                item['status'] = 'split'
                state['queue'] = [value for value in state['queue'] if value != key]
        for key, item in state['queries'].items():
            if item['status'] == 'failed' and key not in state['queue']:
                state['queue'].append(key)
        # Retry unfinished direct collection before continuing paid inventory work.
        state['queue'].sort(key=lambda key: state['queries'][key]['query']['source'] != 'domain')
        budget = restore_budget(state['traffic'], args.max_requests, args.proxy_bytes)
        set_run_budget(budget)
        references = {(ref.source.value, ref.house_id): ref for ref in store.recent()
                      if ref.description_en and ref.thumbnail_url and ref.available_date}
        checkpoint(args, state, budget)
        while state['queue'] and not budget.exhausted:
            key = state['queue'][0]
            item = state['queries'][key]
            query = SearchQuery.from_dict(item['query'])
            item.pop('error', None)
            reuse_domain = query.source == 'domain' and args.detail_limit == 0
            if query.source != 'domain' and domain_browser:
                domain_browser.close()
                domain_browser = None
                shutil.rmtree(domain_profile, ignore_errors=True)
            os.environ['PROXY_SESSION'] = uuid.uuid4().hex[:12]
            geoip_address = None
            if query.source == 'realestate':
                try:
                    verified_exit = verify_au_proxy('realestate', budget, include_ip=True)
                    geoip_address = verified_exit.pop('ip', None)
                    state['exit'] = verified_exit
                    state.setdefault('verified_sessions', {})[key] = {
                        **state['exit'], 'session_hash': hashlib.sha256(os.environ['PROXY_SESSION'].encode()).hexdigest()[:16]}
                except Exception as error:
                    state['errors'].append({'source': 'realestate', 'stage': 'proxy_check', 'error': type(error).__name__})
                    break
            item['status'] = 'running'
            profile = domain_profile if reuse_domain else args.output / 'profiles' / (query.source + '-' + uuid.uuid4().hex)
            try:
                if reuse_domain and domain_browser:
                    browser = domain_browser
                else:
                    browser = BrowserManager(browser_type=BrowserType.PLAYWRIGHT,
                        source=query.source, profile_dir=str(profile), geoip_address=geoip_address)
                    browser.create_driver()
                scraper = FACTORIES[query.source]()
                while not budget.exhausted:
                    page = item['next_page']
                    for attempt in range(args.retries + 1):
                        if browser.navigate(query.url(page), wait_time=5 if page == 1 else 3):
                            html = browser.get_page_source()
                            try:
                                snapshot = read_snapshot(query.source, html, query, page)
                            except SearchMetadataError:
                                if attempt == args.retries:
                                    raise
                                logger.warning('%s page %s has unverified search metadata; retrying same URL', query.source, page)
                            else:
                                break
                        elif attempt == args.retries:
                            raise RuntimeError('Navigation failed or blocked')
                        elif query.source == 'domain' and getattr(browser, '_navigation_status', None) in {403, 429}:
                            logger.warning('Domain rejected the session; retrying this page in a fresh direct browser')
                            browser.close()
                            shutil.rmtree(profile, ignore_errors=True)
                            browser = BrowserManager(browser_type=BrowserType.PLAYWRIGHT,
                                source=query.source, profile_dir=str(profile))
                            browser.create_driver()
                            state['browser_restarts'] = state.get('browser_restarts', 0) + 1
                        time.sleep(3)
                    item.update(total=snapshot.total, visible_pages=snapshot.pages,
                                scope=snapshot.scope, truncated=snapshot.truncated,
                                sort=snapshot.request.get('sort') or snapshot.request.get('sortType'))
                    item.setdefault('first_total', snapshot.total)
                    props = scraper.parse_listing_page(html)
                    if snapshot.result_ids is not None:
                        props = [prop for prop in props if prop.house_id in snapshot.result_ids]
                    if snapshot.total and not props:
                        raise SearchMetadataError('Search reports listings but no cards could be parsed')
                    expected_state = 'NSW' if query.city == 'sydney' else 'VIC'
                    if any(prop.state != expected_state for prop in props):
                        raise SearchMetadataError('Listing state does not match requested city')
                    ids = sorted({prop.house_id for prop in props})
                    digest = hashlib.sha256(json.dumps(ids).encode()).hexdigest()
                    if ids and digest in item['hashes']:
                        raise SearchMetadataError('Repeated page detected; refusing to call the scan complete')
                    if any(not prop.postcode for prop in props):
                        state['postcodes_completed'] += complete_postcodes(props, references.values())
                    if any(not prop.description_en for prop in props):
                        state['reused'] += reuse_details(props, references.values(), allow_same_source=True)
                    unchanged = bool(props) and all(state['initial'].get(query.source + ':' + prop.house_id)
                                                   == signature(prop.to_dict()) for prop in props)
                    item['unchanged_pages'] = item['unchanged_pages'] + 1 if unchanged else 0
                    store.save(props)
                    references.update({(prop.source.value, prop.house_id): prop for prop in props
                                       if prop.description_en and prop.thumbnail_url and prop.available_date})
                    for prop in props:
                        state['seen'][query.source + ':' + prop.house_id] = [query.source, query.city]
                    item['ids'] = list(set(item['ids']) | set(ids))
                    item['hashes'].append(digest)
                    item['pages_visited'] += 1
                    state['pages_visited'] += 1
                    item['next_page'] = page + 1
                    if snapshot.result_ids is not None and set(ids) != snapshot.result_ids:
                        item['next_page'] = page
                        item['hashes'].pop()
                        raise SearchMetadataError('Structured search IDs are missing from parsed cards')
                    expected_cards = min(snapshot.page_size, max(0, snapshot.total - (page - 1) * snapshot.page_size))
                    if len(ids) < expected_cards:
                        item['next_page'] = page
                        item['hashes'].pop()
                        raise SearchMetadataError(f'Card parsing shortfall: {len(ids)} of {expected_cards}')
                    logger.info('%s %s %s page %s: %s cards; paid wire %s',
                                query.source, query.city, query.region or 'metro', page,
                                len(props), budget.proxy_wire_bytes)
                    split = snapshot.truncated and (mode == 'full' or
                        (mode == 'incremental' and page >= snapshot.pages and item['unchanged_pages'] < args.known_pages))
                    if split:
                        children = query.split()
                        if len(state['queries']) + len(children) > args.max_partitions:
                            raise SearchMetadataError('Partition limit reached before complete coverage')
                        item['status'] = 'split'
                        item['children'] = [child.key for child in children]
                        state['queue'].pop(0)
                        for child in reversed(children):
                            if child.key not in state['queries']:
                                state['queries'][child.key] = {'query': child.to_dict(), 'status': 'pending',
                                    'next_page': 1, 'ids': [], 'hashes': [], 'pages_visited': 0, 'unchanged_pages': 0}
                            else:
                                state['queries'][child.key].update(status='pending', next_page=1, hashes=[])
                            if child.key not in state['queue']:
                                state['queue'].insert(0, child.key)
                        checkpoint(args, state, budget)
                        break
                    stop = None
                    if mode == 'pilot' and page >= args.pilot_pages:
                        stop = 'pilot_page_limit'
                    elif mode == 'incremental' and item['unchanged_pages'] >= args.known_pages:
                        stop = 'consecutive_known_unchanged_pages'
                    elif page >= snapshot.pages:
                        stop = 'last_available_page'
                    expected_total = min(item['first_total'], snapshot.total)
                    if mode == 'full' and item.get('coverage_retries', 0) and len(item['ids']) >= expected_total:
                        stop = 'coverage_repaired'
                    if stop:
                        if mode == 'full' and snapshot.truncated:
                            raise SearchMetadataError('Unresolved result window cap')
                        if mode == 'full' and len(item['ids']) < expected_total:
                            item['next_page'], item['hashes'] = 1, []
                            if not item.get('coverage_retries', 0):
                                item['coverage_retries'] = 1
                                checkpoint(args, state, budget)
                                continue
                            raise SearchMetadataError(f'Unique listing shortfall: {len(item["ids"])} of {expected_total}')
                        # Reuse the list browser and its fingerprint for Domain details.
                        if query.source == 'domain' and state['detail_requests'] < args.detail_limit:
                            details = [prop for prop in props if not prop.description_en][:min(
                                args.details_per_query, args.detail_limit - state['detail_requests'])]
                            if details:
                                scraper.browser = browser
                                state['detail_requests'] += len(details)
                                scraper.scrape_property_details(details, skip_existing=True)
                                state['details_failed'] += sum(not prop.description_en for prop in details)
                                store.save(details)
                                item['failed_detail_ids'] = list(set(item.get('failed_detail_ids', [])) |
                                    {prop.house_id for prop in details if not prop.description_en})
                        if mode == 'pilot' and item.get('failed_detail_ids'):
                            unresolved = []
                            for failed_id in item['failed_detail_ids']:
                                row = store.connection.execute('SELECT description_en FROM properties WHERE house_id=? AND source=?',
                                                               (failed_id, query.source)).fetchone()
                                if not row or not row[0] or not row[0].strip():
                                    unresolved.append(failed_id)
                            item['failed_detail_ids'] = unresolved
                            if unresolved:
                                item['next_page'] = page
                                item['hashes'].pop()
                                raise RuntimeError('Unresolved pilot detail failures; increase explicit retry limit after diagnosis')
                        item.update(status='done', stop_reason=stop, unique_listings=len(item['ids']))
                        state['queue'].pop(0)
                        checkpoint(args, state, budget)
                        break
                    checkpoint(args, state, budget)
                    time.sleep(args.delay)
            except Exception as error:
                logger.error('%s %s stopped: %s', query.source, query.region or query.city, type(error).__name__)
                item.update(status='failed', error=str(error)[:200])
                state['errors'].append({'source': query.source, 'city': query.city,
                                        'query': key, 'error': str(error)[:200]})
                state['queue'].pop(0)
            finally:
                if reuse_domain and item['status'] in {'done', 'split'} and not budget.exhausted:
                    domain_browser = browser
                    browser = None
                else:
                    if browser:
                        browser.close()
                        browser = None
                    if reuse_domain:
                        domain_browser = None
                    shutil.rmtree(profile, ignore_errors=True)
                checkpoint(args, state, budget)
        if domain_browser:
            domain_browser.close()
            domain_browser = None
            shutil.rmtree(domain_profile, ignore_errors=True)
        if budget.exhausted:
            state['errors'].append({'stage': 'budget', 'error': 'Budget reached; checkpoint retained'})
        observed = set(tuple(value) for value in state['seen'].values())
        for source in args.scrapers:
            for city in args.cities:
                if (source, city) not in observed:
                    state['errors'].append({'source': source, 'city': city, 'error': 'No listings captured for required source/city'})
        if mode == 'full':
            def covered_ids(key):
                current = state['queries'][key]
                ids = set(current['ids'])
                for child in current.get('children', []):
                    ids.update(covered_ids(child))
                return ids
            state['coverage'] = []
            for key in state['options']['root_queries']:
                item = state['queries'][key]
                expected = min(item.get('first_total', 0), item.get('total', 0))
                count = len(covered_ids(key))
                state['coverage'].append({'query': key, 'expected': expected, 'unique': count})
                if count < expected and item['status'] in {'done', 'split'}:
                    if not unfinished_children(state, key):
                        item.update(status='failed', next_page=1, hashes=[])
                    state['errors'].append({'query': key, 'stage': 'coverage',
                                            'error': f'Root listing shortfall: {count} of {expected}'})
        # Real Estate list responses can supplement matching Domain records without another request.
        references = store.recent()
        targets = [prop for prop in references if prop.source.value == 'domain' and not prop.description_en
                   and 'domain:' + prop.house_id in state['seen']]
        filled = reuse_details(targets, references, allow_same_source=True)
        if filled:
            store.save([prop for prop in targets if prop.description_en])
        state['reverse_reused'] = filled
        state['new_listings'] = sum(key not in state['initial'] for key in state['seen'])
        state['complete_inventory'] = mode == 'full' and not state['errors'] and not state['queue']
        status = 'pilot_success' if mode == 'pilot' else 'success'
        state['status'] = status if not state['errors'] and state['seen'] else 'partial'
        state['exit_code'] = 0 if state['status'] == status else 1
        state['completed_at'] = datetime.now().isoformat()
        checkpoint(args, state, budget)
        export_rows(args, state, store)
        checkpoint(args, state, budget)
        if state['complete_inventory']:
            atomic_json(baseline_path, {'completed_timestamp': time.time(),
                'cities': args.cities, 'sources': args.scrapers, 'scope_hash': scope_hash,
                'planning_version': PLANNING_VERSION, 'root_queries': state['options']['root_queries'],
                'report': str(args.output / 'result.json')})
        return json.loads((args.output / 'result.json').read_text())
    except Exception as error:
        logger.error('Metro capture failed: %s', type(error).__name__)
        if state is not None and budget is not None:
            state.update(status='failed', exit_code=1, complete_inventory=False)
            state['errors'].append({'stage': 'fatal', 'error': str(error)[:200]})
            checkpoint(args, state, budget)
            return json.loads((args.output / 'result.json').read_text())
        return {'status': 'failed', 'exit_code': 1, 'error': str(error)[:200]}
    finally:
        if browser:
            browser.close()
        if domain_browser:
            domain_browser.close()
            shutil.rmtree(domain_profile, ignore_errors=True)
        if state is not None and budget is not None:
            checkpoint(args, state, budget)
        store.close()
        lock.close()


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['pilot', 'full', 'incremental', 'auto'], default='pilot')
    parser.add_argument('--cities', nargs='+', choices=['sydney', 'melbourne'], default=['sydney', 'melbourne'])
    parser.add_argument('--scrapers', nargs='+', choices=['domain', 'realestate'], default=['domain', 'realestate'])
    parser.add_argument('--db', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--proxy-bytes', type=int, default=300000000)
    parser.add_argument('--max-requests', type=int, default=500000)
    parser.add_argument('--max-partitions', type=int, default=500)
    parser.add_argument('--pilot-pages', type=int, default=3)
    parser.add_argument('--pilot-regions', type=int, default=3)
    parser.add_argument('--known-pages', type=int, default=3)
    parser.add_argument('--retries', type=int, choices=[0, 1], default=1)
    parser.add_argument('--delay', type=float, default=2)
    parser.add_argument('--detail-limit', type=int, default=8)
    parser.add_argument('--details-per-query', type=int, default=1)
    return parser


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    args = build_parser().parse_args()
    if any(getattr(args, key) <= 0 for key in ('proxy_bytes', 'max_requests', 'max_partitions',
                                              'pilot_pages', 'pilot_regions', 'known_pages')) or args.detail_limit < 0:
        raise SystemExit('Budgets and page limits must be positive; detail-limit can be zero')
    result = capture(args)
    print(json.dumps(result, ensure_ascii=False))
    return result['exit_code']


if __name__ == '__main__':
    sys.exit(main())

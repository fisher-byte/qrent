import json
import os
import re
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import metro_runner as runner
from src.models import PropertySource
from src.services.search_plan import SearchQuery, SearchSnapshot, SearchMetadataError
from test_recovery import listing


class Harness:
    def __init__(self, spec, fail_pages=(), metadata_failures=0, navigation_failures=0):
        self.spec, self.fail_pages = spec, set(fail_pages)
        self.visits, self.sessions, self.verified = [], [], []
        self.browser_geoips = []
        self.detail_calls = 0
        self.current = None
        self.metadata_failures = metadata_failures
        self.navigation_failures = navigation_failures

    def browser_factory(self, source, **kwargs):
        harness = self
        self.browser_geoips.append(kwargs.get('geoip_address'))
        class Browser:
            def create_driver(self): harness.sessions.append(os.environ['PROXY_SESSION'])
            def navigate(self, url, **kwargs):
                match = re.search(r'/list-(\d+)', url)
                page = int(match.group(1)) if match else int(parse_qs(urlsplit(url).query)['page'][0])
                harness.visits.append(page)
                if harness.navigation_failures:
                    harness.navigation_failures -= 1
                    self._navigation_status = 403
                    return False
                return page not in harness.fail_pages
            def get_page_source(self): return 'fixture'
            def close(self): pass
        return Browser()

    def snapshot(self, source, html, query, page):
        if self.metadata_failures:
            self.metadata_failures -= 1
            raise SearchMetadataError('First rendered template has no verified search data')
        self.current = query, page
        data = self.spec(query, page)
        return SearchSnapshot(data['total'], data['pages'], page, data.get('size', 2), query.city, {},
                              frozenset(data['result_ids']) if 'result_ids' in data else None)

    def scraper_factory(self):
        harness = self
        class Scraper:
            def parse_listing_page(self, html):
                query, page = harness.current
                data = harness.spec(query, page)
                return [listing(PropertySource(query.source), house_id=house_id, state='NSW', postcode='2000',
                                description_en='Complete' if data.get('described', True) else None,
                                thumbnail_url='https://cdn.example/a.jpg',
                                available_date=datetime(2026, 11, 18))
                        for house_id in data['ids']]
            def scrape_property_details(self, props, **kwargs):
                harness.detail_calls += len(props)
        return Scraper()

    def verify(self, source, budget, **kwargs):
        self.verified.append(os.environ['PROXY_SESSION'])
        return {'country': 'AU', **({'ip': '203.0.113.7'} if kwargs.get('include_ip') else {})}

    def run(self, args, queries):
        with patch.object(runner, 'metro_queries', return_value=queries), \
             patch.object(runner, 'BrowserManager', side_effect=self.browser_factory), \
             patch.object(runner, 'read_snapshot', side_effect=self.snapshot), \
             patch.dict(runner.FACTORIES, {'domain': self.scraper_factory, 'realestate': self.scraper_factory}), \
             patch.object(runner, 'verify_au_proxy', side_effect=self.verify), \
             patch.object(runner.time, 'sleep'):
            return runner.capture(args)


def arguments(folder, source='domain', mode='full'):
    return runner.build_parser().parse_args(['--mode', mode, '--cities', 'sydney', '--scrapers', source,
        '--db', str(Path(folder) / 'p.db'), '--output', str(Path(folder) / 'capture'),
        '--detail-limit', '0', '--delay', '0', '--retries', '0'])


class MetroRunnerTests(unittest.TestCase):
    def test_fresh_parent_total_can_validate_completed_partitions_without_rescanning_them(self):
        root_total = 5
        def spec(query, page):
            if query.low is None:
                return {'total': root_total, 'pages': 1, 'ids': ['A', 'B']}
            return {'total': 2, 'pages': 1, 'ids': ['A', 'C'] if query.high is not None else ['B', 'D']}
        with tempfile.TemporaryDirectory() as folder:
            args, root = arguments(folder), SearchQuery('domain', 'sydney')
            self.assertEqual(Harness(spec).run(args, [root])['exit_code'], 1)
            state = json.loads((args.output / 'state.json').read_text())
            self.assertEqual(state['queries'][root.key]['status'], 'failed')
            self.assertTrue(all(state['queries'][k]['status'] == 'done' for k in state['queries'][root.key]['children']))
            root_total = 4  # A fresh real response now agrees with the covered union.
            resumed = Harness(spec)
            result = resumed.run(args, [root])
            self.assertEqual(result['exit_code'], 0)
            self.assertEqual(len(resumed.visits), 1)
            self.assertTrue(result['complete_inventory'])

    def test_parent_refresh_does_not_hide_a_remaining_real_coverage_gap(self):
        repair = False
        def spec(query, page):
            if query.low is None:
                return {'total': 5, 'pages': 1, 'ids': ['A', 'B']}
            if query.high is not None:
                return {'total': 3, 'pages': 2, 'ids': ['A', 'C'] if page == 1 else ['E']} if repair else {
                    'total': 2, 'pages': 1, 'ids': ['A', 'C']}
            return {'total': 2, 'pages': 1, 'ids': ['B', 'D']}
        with tempfile.TemporaryDirectory() as folder:
            args, root = arguments(folder), SearchQuery('domain', 'sydney')
            self.assertEqual(Harness(spec).run(args, [root])['exit_code'], 1)
            repair = True
            resumed = Harness(spec)
            result = resumed.run(args, [root])
            self.assertEqual(result['exit_code'], 0)
            self.assertGreater(len(resumed.visits), 1)
            self.assertEqual(result['listings'], 5)

    def test_failed_child_resume_preserves_completed_partitions_even_with_legacy_failed_root(self):
        failing = True
        def spec(query, page):
            if query.low is None:
                return {'total': 4, 'pages': 1, 'ids': ['A', 'B']}
            if query.high is not None:
                return {'total': 2, 'pages': 1, 'ids': ['A', 'C']}
            if failing:
                raise SearchMetadataError('Temporarily blocked leaf')
            return {'total': 2, 'pages': 1, 'ids': ['B', 'D']}
        with tempfile.TemporaryDirectory() as folder:
            args, root = arguments(folder), SearchQuery('domain', 'sydney')
            first = Harness(spec).run(args, [root])
            self.assertEqual(first['exit_code'], 1)
            state_path = args.output / 'state.json'
            state = json.loads(state_path.read_text())
            self.assertEqual(state['queries'][root.key]['status'], 'split')
            # Older checkpoints incorrectly marked a split root failed as well.
            state['queries'][root.key].update(status='failed', next_page=1, hashes=[])
            state_path.write_text(json.dumps(state))
            failing = False
            resumed = Harness(spec)
            result = resumed.run(args, [root])
            self.assertEqual((result['exit_code'], result['listings']), (0, 4))
            self.assertEqual(resumed.visits, [1])
            self.assertTrue(result['complete_inventory'])

    def test_blocked_domain_session_retries_current_page_in_a_fresh_direct_browser(self):
        harness = Harness(lambda query, page: {'total': 2, 'pages': 1, 'ids': ['A', 'B']}, navigation_failures=1)
        with tempfile.TemporaryDirectory() as folder:
            args = arguments(folder)
            args.retries = 1
            result = harness.run(args, [SearchQuery('domain', 'sydney')])
            self.assertEqual((result['exit_code'], result['listings']), (0, 2))
            self.assertEqual(harness.visits, [1, 1])
            self.assertEqual(len(harness.sessions), 2)
            self.assertEqual(result['browser_restarts'], 1)
            self.assertEqual(harness.verified, [])

    def test_first_unverified_render_retries_same_page_without_losing_rows(self):
        harness = Harness(lambda query, page: {'total': 2, 'pages': 1, 'ids': ['A', 'B']}, metadata_failures=1)
        with tempfile.TemporaryDirectory() as folder:
            args = arguments(folder)
            args.retries = 1
            result = harness.run(args, [SearchQuery('domain', 'sydney')])
            self.assertEqual((result['exit_code'], result['listings'], result['pages_visited']), (0, 2, 1))
            self.assertEqual(harness.visits, [1, 1])
            self.assertTrue(result['complete_inventory'])

    def test_incremental_includes_new_page_then_three_known_unchanged_pages(self):
        queries = [SearchQuery('domain', 'sydney')]
        with tempfile.TemporaryDirectory() as folder:
            args = arguments(folder)
            full = Harness(lambda query, page: {'total': 10, 'pages': 5,
                            'ids': [str(2*page-1), str(2*page)]})
            self.assertEqual(full.run(args, queries)['exit_code'], 0)
            args.mode, args.output = 'incremental', Path(folder) / 'incremental'
            incremental = Harness(lambda query, page: {'total': 11, 'pages': 6,
                'ids': ['new', '1'] if page == 1 else [str(2*page-2), str(2*page-1)]})
            result = incremental.run(args, queries)
            self.assertEqual(result['exit_code'], 0)
            self.assertEqual(incremental.visits, [1, 2, 3, 4])
            self.assertEqual(result['new_listings'], 1)
            self.assertEqual(result['queries'][0]['stop_reason'], 'consecutive_known_unchanged_pages')
            self.assertFalse(result['complete_inventory'])

    def test_extra_cards_cannot_hide_missing_structured_search_ids(self):
        harness = Harness(lambda query, page: {'total': 2, 'pages': 1,
                                             'ids': ['A', 'X'], 'result_ids': ['A', 'B']})
        with tempfile.TemporaryDirectory() as folder:
            result = harness.run(arguments(folder), [SearchQuery('domain', 'sydney')])
            self.assertEqual(result['exit_code'], 1)
            self.assertEqual(result['listings'], 1)
            self.assertFalse(result['complete_inventory'])

    def test_failed_pilot_details_stay_failed_when_retry_limit_is_exhausted(self):
        harness = Harness(lambda query, page: {'total': 2, 'pages': 1, 'ids': ['A', 'B'], 'described': False})
        with tempfile.TemporaryDirectory() as folder:
            args = arguments(folder, mode='pilot')
            args.detail_limit = 1
            queries = [SearchQuery('domain', 'sydney')]
            self.assertEqual(harness.run(args, queries)['exit_code'], 1)
            self.assertEqual(harness.run(args, queries)['exit_code'], 1)
            self.assertEqual(harness.detail_calls, 1)
            self.assertFalse((Path(folder) / 'metro-baseline.json').exists())

    def test_overlap_across_pages_cannot_be_called_complete(self):
        harness = Harness(lambda query, page: {'total': 4, 'pages': 2,
                                             'ids': ['A', 'B'] if page == 1 else ['B', 'C']})
        with tempfile.TemporaryDirectory() as folder:
            result = harness.run(arguments(folder), [SearchQuery('domain', 'sydney')])
            self.assertEqual(result['exit_code'], 1)
            self.assertFalse(result['complete_inventory'])
            self.assertFalse((Path(folder) / 'metro-baseline.json').exists())
            self.assertEqual(result['listings'], 3)

    def test_parse_shortfall_keeps_valid_rows_and_rewinds_failed_page(self):
        harness = Harness(lambda query, page: {'total': 2, 'pages': 1, 'ids': ['A']})
        with tempfile.TemporaryDirectory() as folder:
            args = arguments(folder)
            result = harness.run(args, [SearchQuery('domain', 'sydney')])
            self.assertEqual((result['exit_code'], result['listings']), (1, 1))
            state = json.loads((args.output / 'state.json').read_text())
            self.assertEqual(next(iter(state['queries'].values()))['next_page'], 1)

    def test_resume_does_not_skip_failed_page_or_redownload_successful_page(self):
        spec = lambda query, page: {'total': 4, 'pages': 2,
                                   'ids': ['A', 'B'] if page == 1 else ['C', 'D']}
        with tempfile.TemporaryDirectory() as folder:
            args, queries = arguments(folder), [SearchQuery('domain', 'sydney')]
            failed = Harness(spec, fail_pages=[2])
            self.assertEqual(failed.run(args, queries)['exit_code'], 1)
            resumed = Harness(spec)
            result = resumed.run(args, queries)
            self.assertEqual((result['status'], result['listings']), ('success', 4))
            self.assertEqual(resumed.visits, [2])
            self.assertTrue((Path(folder) / 'metro-baseline.json').exists())

    def test_completed_checkpoint_rejects_new_root_query_scope(self):
        harness = Harness(lambda query, page: {'total': 2, 'pages': 1, 'ids': ['A', 'B']})
        with tempfile.TemporaryDirectory() as folder:
            args = arguments(folder)
            first = SearchQuery('domain', 'sydney')
            self.assertEqual(harness.run(args, [first])['exit_code'], 0)
            visits = len(harness.visits)
            changed = harness.run(args, [first, SearchQuery('domain', 'sydney', low=100)])
            self.assertEqual(changed['exit_code'], 1)
            self.assertEqual(len(harness.visits), visits)
            baseline = json.loads((Path(folder) / 'metro-baseline.json').read_text())
            self.assertEqual(baseline['root_queries'], [first.key])

    def test_actual_realestate_browser_uses_the_session_just_verified(self):
        harness = Harness(lambda query, page: {'total': 2, 'pages': 1, 'ids': ['A', 'B']})
        with tempfile.TemporaryDirectory() as folder:
            args = arguments(folder, source='realestate')
            result = harness.run(args, [SearchQuery('realestate', 'sydney', 'Western Sydney, NSW', 'atlas')])
            self.assertEqual(result['exit_code'], 0)
            self.assertEqual(harness.verified, harness.sessions)
            self.assertEqual(harness.browser_geoips, ['203.0.113.7'])
            self.assertNotIn('ip', result['exit'])

    def test_split_queries_deduplicate_and_check_parent_inventory(self):
        def spec(query, page):
            if query.low is None: return {'total': 4, 'pages': 1, 'ids': ['A', 'B']}
            if query.high is not None: return {'total': 2, 'pages': 1, 'ids': ['A', 'C']}
            return {'total': 2, 'pages': 1, 'ids': ['B', 'D']}
        with tempfile.TemporaryDirectory() as folder:
            harness = Harness(spec)
            result = harness.run(arguments(folder), [SearchQuery('domain', 'sydney')])
            self.assertEqual((result['exit_code'], result['listings']), (0, 4))
            self.assertTrue(result['complete_inventory'])
            self.assertEqual((result['coverage'][0]['expected'], result['coverage'][0]['unique']), (4, 4))
            self.assertEqual(len(harness.sessions), 1)

    def test_increased_resume_budget_keeps_previous_traffic(self):
        budget = runner.restore_budget({'proxy_wire_bytes': 1100, 'requests': 10, 'exhausted': True},
                                       max_requests=100, max_bytes=2000)
        self.assertFalse(budget.exhausted)
        self.assertEqual((budget.proxy_wire_bytes, budget.requests), (1100, 10))


if __name__ == '__main__':
    unittest.main()

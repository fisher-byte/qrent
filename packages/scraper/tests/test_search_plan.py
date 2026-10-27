import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.services.search_plan import SearchQuery, SearchMetadataError, read_snapshot


def domain_html(total=13182, pages=50, **request):
    query = {'page': 1, 'pageSize': 20, 'locations': [{'region': 'Sydney Region', 'state': 'NSW'}],
             'sort': {'sortKey': 'DateUpdated', 'direction': 'Descending'}}
    query.update(request)
    data = {'props': {'pageProps': {'componentProps': {
        'currentPage': 1, 'totalPages': pages, 'totalListings': total,
        'listingSearchResultIds': list(range(min(total, 20))),
        'pageViewMetadata': {'searchRequest': query}}}}}
    return '<script id="__NEXT_DATA__" type="application/json">' + json.dumps(data) + '</script>'


class SearchPlanTests(unittest.TestCase):
    def test_domain_public_json_preserves_scope_ordering_and_ids_validation(self):
        html = domain_html(total=2, pages=1)
        component = json.loads(html.split('>', 1)[1].split('</script>')[0])['props']['pageProps']['componentProps']
        document = {'props': component}
        snapshot = read_snapshot('domain', document, SearchQuery('domain', 'sydney'))
        self.assertEqual((snapshot.total, snapshot.result_ids), (2, frozenset({'0', '1'})))
        with self.assertRaises(SearchMetadataError):
            read_snapshot('domain', document, SearchQuery('domain', 'melbourne'))
        with self.assertRaises(SearchMetadataError):
            read_snapshot('domain', document, SearchQuery('domain', 'sydney', low=0, high=750))
        component['pageViewMetadata']['searchRequest']['sort']['sortKey'] = 'Default'
        with self.assertRaises(SearchMetadataError):
            read_snapshot('domain', document, SearchQuery('domain', 'sydney'))

    def test_domain_rejects_ignored_newest_sort_before_incremental_early_stop(self):
        for sort in (None, {'sortKey': 'Default', 'direction': 'Descending'},
                     {'sortKey': 'DateUpdated', 'direction': 'Ascending'}):
            with self.subTest(sort=sort), self.assertRaises(SearchMetadataError):
                read_snapshot('domain', domain_html(sort=sort), SearchQuery('domain', 'sydney'))

    def test_realestate_rejects_ignored_newest_sort(self):
        query = SearchQuery('realestate', 'melbourne', 'Melbourne - Northern Region, VIC', 'northern')
        for sort, valid in (('new-desc', True), (None, False), ('relevance', False)):
            request = {'pageSize': 25, 'sortType': sort}
            search = {'resolvedQuery': {'localities': [{'atlasId': 'northern', 'display': query.region}],
                'metadata': {'savedSearchQuery': json.dumps(request)}},
                'results': {'totalResultsCount': 0, 'pagination': {'maxPageNumberAvailable': 0, 'page': 1},
                            'exact': {'items': []}}}
            cache = {'1': {'data': json.dumps({'rentSearch': search})}}
            exchange = {'resi-property_listing-experience-web': {'urqlClientCache': json.dumps(cache)}}
            html = '<script>window.ArgonautExchange=' + json.dumps(exchange) + ';</script>'
            if valid:
                self.assertEqual(read_snapshot('realestate', html, query).total, 0)
            else:
                with self.assertRaises(SearchMetadataError):
                    read_snapshot('realestate', html, query)

    def test_domain_exposes_search_window_cap_and_validates_price_filter(self):
        query = SearchQuery('domain', 'sydney')
        page = read_snapshot('domain', domain_html(), query)
        self.assertTrue(page.truncated)
        self.assertEqual((page.total, page.pages, page.page_size), (13182, 50, 20))
        low = SearchQuery('domain', 'sydney', low=0, high=500)
        self.assertIn('price=0-500', low.url())
        self.assertFalse(read_snapshot('domain', domain_html(total=600, pages=30, minPrice=0, maxPrice=500), low).truncated)
        with self.assertRaises(SearchMetadataError):
            read_snapshot('domain', domain_html(), low)

    def test_partition_ranges_overlap_at_boundary_and_keep_unbounded_tail(self):
        root = SearchQuery('domain', 'sydney')
        left, right = root.split()
        self.assertEqual((left.low, left.high, right.low, right.high), (0, 750, 750, None))
        self.assertNotEqual(left.key, right.key)
        self.assertIn('price=750-any', right.url())
        self.assertEqual(right, SearchQuery.from_dict(right.to_dict()))

    def test_one_dollar_tie_splits_by_bedrooms_without_dropping_studios_or_large_homes(self):
        children = SearchQuery('domain', 'melbourne', low=600, high=601).split()
        self.assertEqual([child.beds for child in children], [0, 1, 2, 3, 4, 5])
        self.assertIn('bedrooms=0-0', children[0].url())
        self.assertIn('bedrooms=5-any', children[-1].url())
        with self.assertRaises(SearchMetadataError):
            children[2].split()

    def test_unknown_or_wrong_city_metadata_is_rejected(self):
        with self.assertRaises(SearchMetadataError):
            read_snapshot('domain', '<h1>Access Denied</h1>', SearchQuery('domain', 'sydney'))
        with self.assertRaises(SearchMetadataError):
            read_snapshot('domain', domain_html(), SearchQuery('domain', 'melbourne'))


if __name__ == '__main__':
    unittest.main()

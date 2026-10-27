import json
import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.scrapers.realestate import RealEstateScraper
from src.scrapers.domain import DomainScraper


def listing_html(items):
    data = {'rentSearch': {'results': {'totalResultsCount': len(items),
                                      'exact': {'items': [{'listing': item} for item in items]}}}}
    cache = {'1': {'data': json.dumps(data)}}
    exchange = {'resi-property_listing-experience-web': {'urqlClientCache': json.dumps(cache)}}
    return '<script>window.ArgonautExchange=' + json.dumps(exchange) + ';</script>'


def embedded_listing(house_id='456'):
    return {'__typename': 'RentResidentialListing', 'id': house_id,
            'description': '<p>Sunny unit.</p><p>Walk to station.</p>',
            'address': {'display': {'shortAddress': '2/10 Example Street'},
                        'suburb': 'Carlton', 'state': 'VIC', 'postcode': '3053'},
            'price': {'display': '$600 per week'},
            'generalFeatures': {'bedrooms': {'value': 2}, 'bathrooms': {'value': 1},
                                'parkingSpaces': {'value': 0}},
            'propertyType': {'id': 'apartment', 'display': 'Apartment'},
            'media': {'mainImage': {'templatedUrl': 'https://i2.au.reastatic.net/{size}/abc/image.jpg'}},
            '_links': {'canonical': {'href': 'https://www.realestate.com.au/property-apartment-vic-carlton-' + house_id}},
            'availableDate': {'display': 'Available 18 Nov 2026'}}


class EmbeddedListingTests(unittest.TestCase):
    def test_domain_embedded_card_retains_unit_state_and_search_id_scope(self):
        model = {'url': '/2-10-example-street-carlton-vic-3053-123', 'price': 'Contact agent',
                 'address': {'street': '2/10 Example Street', 'suburb': 'Carlton', 'state': 'VIC', 'postcode': '3053'},
                 'features': {'beds': 0, 'baths': 1, 'parking': 0, 'propertyTypeFormatted': 'Studio'},
                 'images': ['https://cdn.example/property.jpg']}
        component = {'listingSearchResultIds': [123], 'listingsMap': {
            '123': {'listingModel': model}, '999': {'listingModel': model}}}
        data = {'props': {'pageProps': {'componentProps': component}}}
        html = '<script id="__NEXT_DATA__">' + json.dumps(data) + '</script>'
        html += '''<li data-testid="listing-999"><a href="https://www.domain.com.au/sponsored-vic-3000-999">
          <span data-testid="address-line1">9 Sponsored Street</span>
          <span data-testid="address-line2">Melbourne VIC 3000</span></a>
          <p data-testid="listing-card-price">$900 per week</p></li>'''
        props = DomainScraper().parse_listing_page(html)
        self.assertEqual([prop.house_id for prop in props], ['123'])
        self.assertEqual((props[0].address_line1, props[0].state, props[0].postcode, props[0].bedroom_count),
                         ('2/10 Example Street', 'VIC', '3053', 0))
        self.assertEqual(props[0].price_per_week, 0)

    def test_list_response_supplies_description_location_and_date_without_detail_request(self):
        props = RealEstateScraper().parse_listing_page(listing_html([embedded_listing()]))
        self.assertEqual(len(props), 1)
        prop = props[0]
        self.assertEqual((prop.house_id, prop.address_line1, prop.state, prop.postcode),
                         ('456', '2/10 Example Street', 'VIC', '3053'))
        self.assertEqual(prop.description_en, 'Sunny unit.\nWalk to station.')
        self.assertEqual(prop.available_date, datetime(2026, 11, 18))
        self.assertEqual(prop.thumbnail_url, 'https://i2.au.reastatic.net/800x600/abc/image.jpg')
        self.assertEqual(prop.price_per_week, 600)

    def test_unknown_price_and_full_description_are_preserved(self):
        item = embedded_listing()
        item['price']['display'] = 'Contact agent'
        item['description'] = 'Full description ' * 200
        props = RealEstateScraper().parse_listing_page(listing_html([item]))
        self.assertEqual(len(props), 1)
        self.assertEqual(props[0].price_per_week, 0)
        self.assertEqual(props[0].description_en, item['description'].strip())

    def test_mixed_case_source_state_is_normalized_before_metro_validation(self):
        item = embedded_listing()
        item['address']['state'] = 'Vic'
        prop = RealEstateScraper().parse_listing_page(listing_html([item]))[0]
        self.assertEqual(prop.state, 'VIC')
        self.assertEqual(prop.address_line2, 'carlton-vic-3053')

    def test_sponsored_items_outside_search_results_are_not_added(self):
        html = listing_html([embedded_listing()])
        root = json.loads(html.split('=', 1)[1].split(';</script>')[0])
        cache = json.loads(root['resi-property_listing-experience-web']['urqlClientCache'])
        data = json.loads(cache['1']['data'])
        data['rentSearch']['exclusiveShowcase'] = {'listings': [embedded_listing('999')]}
        cache['1']['data'] = json.dumps(data)
        root['resi-property_listing-experience-web']['urqlClientCache'] = json.dumps(cache)
        html = '<script>window.ArgonautExchange=' + json.dumps(root) + ';</script>'
        self.assertEqual([p.house_id for p in RealEstateScraper().parse_listing_page(html)], ['456'])

    def test_malformed_embedded_json_does_not_break_legacy_parser(self):
        self.assertEqual(RealEstateScraper().parse_listing_page('<script>window.ArgonautExchange={broken;</script>'), [])


if __name__ == '__main__':
    unittest.main()

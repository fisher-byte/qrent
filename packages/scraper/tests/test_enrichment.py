import sys
import unittest
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.config import ScoringConfig, CommuteConfig
from src.models import PropertyData, PropertySource, RegionInfo
from src.services.scoring import ScoringService
from src.services.commute import CommuteService
from src.services.database import DatabaseService
from src.services.publication import publication_errors, website_timestamp, mysql_availability
from src.scrapers.domain import DomainScraper


def complete_property(code='UNSW'):
    return PropertyData(house_id='123', source=PropertySource.DOMAIN,
                        price_per_week=600, price_text='$600 per week', address_line1='1 Example Street',
                        address_line2='carlton-vic-3053', suburb='Carlton', state='VIC', postcode='3053',
                        description_en='Sunny apartment.', description_cn='采光充足',
                        keywords='natural light', average_score=13.5, scores=[13.5] * 8,
                        url='https://www.domain.com.au/carlton-123',
                        thumbnail_url='https://cdn.example/property.jpg', commute_times={code: 41})


def scored_text(colon=':'):
    return '\n'.join(f'总评分{colon}{score}' for score in (12, 13, 14, 15))


class ScoringIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.service = ScoringService(ScoringConfig(api_key='test', num_calls=2))

    def test_failed_calls_do_not_create_the_old_fixed_thirteen_score(self):
        with patch.object(self.service, '_call_model', return_value=None), patch('src.services.scoring.time.sleep'):
            average, scores = self.service.score_property('A sunny two bedroom apartment.')
        self.assertIsNone(average)
        self.assertEqual(scores, [])

    def test_partial_groups_do_not_pass_as_the_complete_two_call_result(self):
        with patch.object(self.service, '_call_model', side_effect=[scored_text(), 'total unavailable']), \
                patch('src.services.scoring.time.sleep'):
            average, scores = self.service.score_property('A sunny two bedroom apartment.')
        self.assertIsNone(average)
        self.assertEqual(scores, [])

    def test_fullwidth_colons_preserve_all_eight_independent_scores(self):
        with patch.object(self.service, '_call_model', return_value=scored_text('：')), \
                patch('src.services.scoring.time.sleep'):
            average, scores = self.service.score_property('A sunny two bedroom apartment.')
        self.assertEqual(scores, [12, 13, 14, 15] * 2)
        self.assertEqual(average, 13.5)


class CommuteIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.service = CommuteService(CommuteConfig(api_key=''))
        self.service.gmaps = MagicMock()
        self.service.gmaps.directions.return_value = [{'legs': [{'duration': {'value': 2460}}]}]

    def test_school_departure_uses_australian_time_not_the_crawler_host_timezone(self):
        self.assertEqual(self.service.calculate_transit_time('Sydney NSW', 'UNSW Sydney'), 41)
        departure = self.service.gmaps.directions.call_args.kwargs['departure_time']
        self.assertIsNotNone(departure.tzinfo)
        self.assertEqual((departure.hour, departure.minute), (8, 30))
        self.assertEqual(departure.tzinfo, ZoneInfo('Australia/Sydney'))

    def test_raw_street_number_ranges_are_not_changed_before_geocoding(self):
        prop = PropertyData(house_id='123', source=PropertySource.DOMAIN,
                            address_line1='1-3 Example Street', address_line2='carlton-vic-3053')
        self.assertEqual(self.service._get_address(prop), '1-3 Example Street, carlton vic 3053, Australia')


class ExistingSchoolContractTests(unittest.TestCase):
    def test_same_suburb_name_in_different_states_does_not_reuse_the_wrong_region(self):
        service = DatabaseService()
        service.cursor = MagicMock()
        service.connection = MagicMock()
        service.cursor.fetchone.side_effect = [None, (9, 'NSW', 2753), None]
        service.cursor.lastrowid = 42
        self.assertEqual(service.get_or_create_region(RegionInfo('richmond', 'VIC', 3121)), 42)
        self.assertEqual(service.cursor.execute.call_args.args[1], ('richmond (VIC 3121)', 'VIC', 3121))
        service.connection.commit.assert_not_called()

    def test_save_uses_existing_school_codes_and_does_not_create_full_name_duplicates(self):
        for code, school_id in (('UNSW', 30), ('UNIMELB', 28)):
            with self.subTest(code=code):
                service = DatabaseService()
                service.cursor = MagicMock()
                service.connection = MagicMock()
                service.cursor.lastrowid = 999
                service.cursor.fetchone.side_effect = lambda: ((school_id,) if service.cursor.execute.call_args.args[1][0] == code else None)
                service.load_existing_house_ids = MagicMock()
                service.get_or_create_region = MagicMock(return_value=1)
                service.get_property_by_house_id = MagicMock(return_value=None)
                service.insert_property = MagicMock(return_value=42)
                service.upsert_property_school = MagicMock(return_value=True)
                prop = complete_property(code)
                result = service.save_properties([prop], code)
                self.assertEqual(result['inserted'], 1)
                service.upsert_property_school.assert_called_once_with(42, school_id, 41)
                self.assertFalse(any('INSERT INTO schools' in call.args[0] for call in service.cursor.execute.call_args_list))

    def test_existing_property_query_uses_the_actual_production_timestamp_column(self):
        service = DatabaseService()
        service.cursor = MagicMock()
        service.cursor.fetchone.return_value = None
        service.get_property_by_house_id('123')
        query = service.cursor.execute.call_args.args[0]
        self.assertIn('published_at', query)
        self.assertNotIn('release_time', query)

    def test_cross_source_numeric_id_collision_cannot_overwrite_another_source(self):
        service = DatabaseService()
        service.cursor = MagicMock()
        service.connection = MagicMock()
        service.get_school_id = MagicMock(return_value=30)
        service.load_existing_house_ids = MagicMock()
        service.get_or_create_region = MagicMock(return_value=1)
        service.get_property_by_house_id = MagicMock(return_value={'id': 42, 'url': 'https://www.realestate.com.au/property-house-vic-carlton-123'})
        service.update_property = MagicMock(return_value=True)
        service.upsert_property_school = MagicMock(return_value=True)
        result = service.save_properties([complete_property()], 'UNSW')
        self.assertEqual(result['inserted'] + result['updated'], 0)
        service.update_property.assert_not_called()

    def test_failed_school_link_is_not_counted_as_successful_property_publication(self):
        service = DatabaseService()
        service.cursor = MagicMock()
        service.connection = MagicMock()
        service.get_school_id = MagicMock(return_value=30)
        service.load_existing_house_ids = MagicMock()
        service.get_or_create_region = MagicMock(return_value=1)
        service.get_property_by_house_id = MagicMock(return_value=None)
        service.insert_property = MagicMock(return_value=42)
        service.upsert_property_school = MagicMock(return_value=False)
        result = service.save_properties([complete_property()], 'UNSW')
        self.assertEqual(result['inserted'], 0)
        self.assertEqual(result['errors'], 1)
        self.assertTrue(any('ROLLBACK TO SAVEPOINT' in c.args[0] for c in service.cursor.execute.call_args_list))

    def test_failed_image_link_rolls_back_the_property_and_school_association(self):
        service = DatabaseService()
        service.cursor = MagicMock()
        service.connection = MagicMock()
        service.get_school_id = MagicMock(return_value=30)
        service.load_existing_house_ids = MagicMock()
        service.get_or_create_region = MagicMock(return_value=1)
        service.get_property_by_house_id = MagicMock(return_value=None)
        service.insert_property = MagicMock(return_value=42)
        service.upsert_property_school = MagicMock(return_value=True)
        service.upsert_property_image = MagicMock(return_value=False)
        result = service.save_properties([complete_property()], 'UNSW')
        self.assertEqual(result['inserted'], 0)
        self.assertEqual(result['errors'], 1)
        self.assertNotIn('123', service._existing_house_ids)
        self.assertTrue(any('ROLLBACK TO SAVEPOINT' in c.args[0] for c in service.cursor.execute.call_args_list))

    def test_primary_image_update_preserves_gallery_rows(self):
        service = DatabaseService()
        service.cursor = MagicMock()
        service.cursor.fetchone.return_value = (99,)
        self.assertTrue(service.upsert_property_image(42, 'https://cdn.example/new.jpg'))
        self.assertEqual(service.cursor.execute.call_args.args,
                         ('UPDATE property_images SET url = %s WHERE id = %s', ('https://cdn.example/new.jpg', 99)))
        self.assertFalse(any('DELETE' in c.args[0] for c in service.cursor.execute.call_args_list))

    def test_missing_existing_school_is_reported_as_a_publication_error(self):
        service = DatabaseService()
        service.get_school_id = MagicMock(return_value=None)
        self.assertEqual(service.save_properties([complete_property()], 'UNSW')['errors'], 1)


class PublicationIntegrityTests(unittest.TestCase):
    def test_inactive_listing_or_agent_contact_is_not_a_scorable_description(self):
        prop=complete_property()
        prop.description_en='Deposit Taken'
        self.assertIn('inactive_listing',publication_errors(prop,'UNSW'))
        prop.description_en='Please message Danielle Yu on 0432 000 000 for inspection! Do not miss out!'
        self.assertIn('description_en',publication_errors(prop,'UNSW'))
        prop.description_en='Contact us to inspect this sunny apartment with a private balcony.'
        self.assertNotIn('description_en',publication_errors(prop,'UNSW'))

    def test_english_text_cannot_satisfy_the_chinese_field(self):
        prop=complete_property();prop.description_cn='private terrace, island kitchen'
        self.assertIn('description_cn',publication_errors(prop,'UNSW'))
        prop.description_cn='独立露台, Burwood Westfield附近'
        self.assertNotIn('description_cn',publication_errors(prop,'UNSW'))
        prop.keywords='no relevant information'
        self.assertIn('keywords',publication_errors(prop,'UNSW'))

    def test_availability_keeps_the_australian_calendar_day_at_utc_boundary(self):
        self.assertEqual(mysql_availability('2026-11-15T00:00:00+11:00'), datetime(2026, 11, 15))
        self.assertIsNone(mysql_availability(None))

    def test_incomplete_ai_and_routes_stay_out_of_the_non_nullable_website_contract(self):
        prop = complete_property()
        prop.average_score = None
        prop.scores = []
        prop.commute_times = {}
        self.assertIn('complete_ai_scores', publication_errors(prop, 'UNSW'))
        self.assertIn('commute_time', publication_errors(prop, 'UNSW'))

    def test_ingestion_timestamp_is_utc_without_inventing_a_source_publication_date(self):
        prop = complete_property()
        prop.scraped_at = datetime(2026, 10, 29, 8, 30)
        self.assertEqual(website_timestamp(prop), datetime(2026, 10, 29, 0, 30))
        self.assertIsNone(prop.published_at)


class DomainDetailIntegrityTests(unittest.TestCase):
    def test_public_detail_graph_keeps_untruncated_description_and_actual_dates(self):
        listing = {'id': '123', 'headline': 'Sunny apartment',
                   'description': 'Full original description including the final wardrobes and views.',
                   'dateListedV2': {'isoDateWithTimeZone': '2026-10-28T19:31:24+11:00'},
                   'dateAvailableV2': {'isoDate': '2026-11-15T00:00:00'}}
        data = {'props': {'pageProps': {'componentProps': {'rootGraphQuery': {'listingByIdV2': listing}}}}}
        html = '<script id="__NEXT_DATA__" type="application/json">' + json.dumps(data) + '</script>'
        html += '<div data-testid="listing-details__description"><p>truncated final...</p></div>'
        prop = DomainScraper().parse_detail_page(complete_property(), html)
        self.assertIn('final wardrobes and views.', prop.description_en)
        self.assertNotIn('truncated', prop.description_en)
        self.assertEqual(prop.available_date, datetime(2026, 11, 15))
        self.assertEqual(prop.published_at.astimezone(timezone.utc), datetime(2026, 10, 28, 8, 31, 24, tzinfo=timezone.utc))

    def test_detail_graph_for_a_different_listing_cannot_enrich_this_property(self):
        data = {'props': {'pageProps': {'componentProps': {'rootGraphQuery': {'listingByIdV2':
                {'id': '999', 'description': 'Wrong apartment'}}}}}}
        html = '<script id="__NEXT_DATA__" type="application/json">' + json.dumps(data) + '</script>'
        prop = complete_property()
        prop.description_en = None
        self.assertIsNone(DomainScraper().parse_detail_page(prop, html).description_en)


if __name__ == '__main__':
    unittest.main()

import json
import sys
import tempfile
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.services.enrichment_state import APILimitReached, EnrichmentState
from src.services.commute_pool import CommutePool, normalized_address
from src.models import PropertyData, PropertySource
from src.config import ScoringConfig
from src.services.scoring import ScoringService
from ops.backup_mysql import encode, decode
from enrichment_runner import enrich


class EnrichmentCostTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = EnrichmentState(Path(self.temp.name) / 'cache.db', 'pilot')
        self.prop = PropertyData(house_id='123', source=PropertySource.DOMAIN,
                                 address_line1='1 Example Street', address_line2='newtown-nsw-2042',
                                 price_per_week=600, price_text='$600 per week', state='NSW', postcode='2042',
                                 url='https://www.domain.com.au/sydney-123',
                                 thumbnail_url='https://cdn.example/property.jpg')

    def tearDown(self):
        self.state.close()
        self.temp.cleanup()

    def test_cash_ceiling_is_four_dollars_even_when_old_environment_requests_five(self):
        self.state.close()
        with patch.dict('os.environ', {'QRENT_MONTHLY_USD_LIMIT': '5'}):
            self.state = EnrichmentState(Path(self.temp.name) / 'cache.db', 'four-dollar-cap')
        self.assertEqual(self.state.budget_summary()['ceiling_usd'], 4)
        self.state.record_expense('existing-paid-and-reserved', .65)
        self.state.reserve('maps', 'one', 100, usd_ceiling=3.35)
        with self.assertRaises(APILimitReached):
            self.state.reserve('maps', 'one', 100, usd_ceiling=.000001)

    def test_enrichment_does_not_spend_on_a_metro_listing_outside_original_school_areas(self):
        self.prop.address_line2 = 'castle-hill-nsw-2154'
        self.prop.description_en = 'A real listing description.'
        with patch('enrichment_runner.CommutePool') as maps:
            result = enrich([self.prop], self.state, use_api=True, max_model_calls=0)
        maps.return_value.calculate.assert_not_called()
        self.assertIn('out_of_school_scope', result['records'][0]['input_errors'])

    def test_melbourne_address_only_computes_its_historical_campus(self):
        self.prop.address_line2 = 'carlton-vic-3053'
        self.prop.state = 'VIC'
        self.prop.description_en = 'A real listing description.'
        with patch('enrichment_runner.CommutePool') as maps:
            enrich([self.prop], self.state, use_api=True, max_model_calls=0)
        self.assertEqual([call.args[1] for call in maps.return_value.calculate.call_args_list], ['UNIMELB'])

    def test_missing_nsw_key_cannot_fall_back_to_google(self):
        with patch.dict('os.environ', {'COMMUTE_PROVIDER': 'nsw', 'NSW_API_KEY': '',
                                       'GOOGLE_MAPS_API_KEY': 'google-key-must-not-run'}):
            pool = CommutePool(self.state)
        self.assertEqual(pool.keys, [])
        pool.close()

    def test_nsw_all_endpoints_share_one_daily_allowance(self):
        with patch('src.services.enrichment_state.datetime') as clock:
            clock.now.return_value = datetime(2026, 10, 29, 12)
            self.state.reserve('nsw', 'same-account', 100, monthly_limit=1)
            with self.assertRaises(APILimitReached):
                self.state.reserve('nsw', 'same-account', 100, monthly_limit=1)
            clock.now.return_value = datetime(2026, 10, 30, 12)
            try:
                self.state.reserve('nsw', 'same-account', 100, monthly_limit=1)
                next_day_allowed = True
            except APILimitReached:
                next_day_allowed = False
        self.assertTrue(next_day_allowed)

    def test_commute_only_mode_never_calls_the_model_even_with_a_live_model_configuration(self):
        self.prop.description_en='Actual description.'
        with patch('enrichment_runner.ScoringConfig',return_value=ScoringConfig(api_key='test')), \
                patch.object(ScoringService,'process_property') as model, \
                patch('enrichment_runner.CommutePool') as maps:
            maps.return_value.calculate.return_value=23
            try:
                result=enrich([self.prop],self.state,use_api=True,commute_only=True)
            except TypeError:
                self.fail('Free commute-only entry is missing')
        model.assert_not_called()
        self.assertEqual(maps.return_value.calculate.call_count,3)
        self.assertEqual(result['commute_returned_pairs'],3)

    def test_rotation_does_not_multiply_the_shared_billing_account_quota(self):
        self.state.reserve('maps', 'same-account', 20, monthly_limit=1)
        with self.assertRaises(APILimitReached):
            self.state.reserve('maps', 'same-account', 20, monthly_limit=1)
        self.assertEqual(self.state.summary()[0]['requests'], 1)

    def test_monthly_dollar_cap_is_checked_before_a_request_and_cannot_reset_on_restart(self):
        self.state.record_expense('proxy-purchase', 0.30)
        self.state.reserve('maps', 'one', 100, usd_ceiling=3.70)
        with self.assertRaises(APILimitReached):
            self.state.reserve('maps', 'one', 100, usd_ceiling=.000001)
        self.assertEqual(self.state.budget_summary()['remaining_usd'], 0)
        path = Path(self.temp.name) / 'cache.db'
        self.state.close()
        self.state = EnrichmentState(path, 'different-run')
        with self.assertRaises(APILimitReached):
            self.state.reserve('maps', 'one', 100, usd_ceiling=.01)
        self.assertEqual(self.state.budget_summary()['ceiling_usd'], 4)

    def test_replayed_proxy_purchase_receipt_cannot_be_charged_twice(self):
        self.state.record_expense('same-receipt', .30)
        self.state.record_expense('same-receipt', .30)
        self.assertEqual(self.state.budget_summary()['spent_or_reserved_usd'], .30)

    def test_actual_cost_releases_only_the_unused_reservation(self):
        request_id = self.state.reserve('maps', 'one', 100, usd_ceiling=.10)
        self.state.finish(request_id, 200, {'input_tokens': 100, 'output_tokens': 10}, actual_usd=.01)
        self.assertEqual(self.state.budget_summary()['spent_or_reserved_usd'], .01)

    def test_permanent_results_do_not_expire_while_addresses_keep_units_and_ranges(self):
        self.state.put('verified-route', {'minutes': 41})
        with patch('src.services.enrichment_state.time.time', return_value=9999999999):
            self.assertEqual(self.state.get('verified-route'), {'minutes': 41})
        self.assertEqual(normalized_address('Unit 2, 18 George St.'), normalized_address('2/18 George Street'))
        self.assertNotEqual(normalized_address('2-18 George Street'), normalized_address('2/18 George Street'))

    def test_run_limit_survives_a_process_restart(self):
        path = Path(self.temp.name) / 'cache.db'
        self.state.reserve('model', 'qwen-plus', 1)
        self.state.close()
        self.state = EnrichmentState(path, 'pilot')
        with self.assertRaises(APILimitReached):
            self.state.reserve('model', 'qwen-plus', 1)

    def test_concurrent_job_cannot_duplicate_address_requests(self):
        with self.assertRaises(APILimitReached):
            EnrichmentState(Path(self.temp.name) / 'cache.db', 'overlapping-job')

    def test_only_verified_successful_route_is_cached_and_reused_across_sources(self):
        pool = CommutePool(self.state, keys=[{'api_key': 'test', 'billing_group': 'one', 'provider': 'routes'}])
        response = MagicMock(status_code=200)
        response.json.return_value = {'routes': [{'duration': '2460s'}]}
        with patch.object(pool.http, 'post', return_value=response) as post:
            self.assertEqual(pool.calculate(self.prop, 'UNSW'), 41)
            self.prop.source = PropertySource.REALESTATE
            self.assertEqual(pool.calculate(self.prop, 'UNSW'), 41)
            self.assertEqual(post.call_count, 1)
            self.assertEqual(post.call_args.kwargs['json']['travelMode'], 'TRANSIT')
            self.assertEqual(post.call_args.kwargs['headers']['X-Goog-FieldMask'], 'routes.duration')
        self.assertEqual(pool.cache_hits, 1)
        self.assertFalse(pool.http.trust_env)
        pool.close()

    def test_maps_only_uses_its_explicit_ordinary_proxy(self):
        with patch.dict('os.environ', {'HTTPS_PROXY': 'http://paid.example:1234',
                                       'GOOGLE_MAPS_HTTP_PROXY': 'http://127.0.0.1:7890'}):
            pool = CommutePool(self.state, keys=[])
        self.assertFalse(pool.http.trust_env)
        self.assertEqual(pool.http.proxies['https'], 'http://127.0.0.1:7890')
        pool.close()

    def test_pool_configuration_cannot_raise_the_free_only_monthly_ceiling(self):
        pool = CommutePool(self.state, keys=[{'api_key': 'test', 'billing_group': 'one',
                                             'provider': 'routes', 'monthly_limit': 100000}])
        with patch.object(self.state, 'reserve', side_effect=APILimitReached('test cap')) as reserve:
            self.assertIsNone(pool.calculate(self.prop, 'UNSW'))
        reserve.assert_called_once_with('maps', 'one', 14, 9500)
        pool.close()

    def test_billing_failure_does_not_retry_the_same_account_through_a_second_key(self):
        pool = CommutePool(self.state, keys=[{'api_key': str(i), 'billing_group': 'same', 'provider': 'routes'} for i in range(2)])
        response = MagicMock(status_code=403)
        response.json.return_value = {'error': {'status': 'PERMISSION_DENIED'}}
        with patch.object(pool.http, 'post', return_value=response) as post:
            self.assertIsNone(pool.calculate(self.prop, 'UNSW'))
            self.assertIsNone(pool.calculate(self.prop, 'USYD'))
            self.assertEqual(post.call_count, 1)
        pool.close()

    def test_no_transit_route_does_not_become_a_driving_multiplier(self):
        pool = CommutePool(self.state, keys=[{'api_key': 'test', 'billing_group': 'one', 'provider': 'routes'}])
        response = MagicMock(status_code=200)
        response.json.return_value = {'routes': []}
        with patch.object(pool.http, 'post', return_value=response) as post:
            self.assertIsNone(pool.calculate(self.prop, 'UNSW'))
            self.assertIsNone(pool.calculate(self.prop, 'UNSW'))
            self.assertEqual(post.call_count, 1)
        pool.close()

    def test_hidden_addresses_do_not_consume_quota_or_create_fake_suburb_commutes(self):
        pool = CommutePool(self.state, keys=[{'api_key': 'test', 'billing_group': 'one', 'provider': 'routes'}])
        self.prop.address_line1 = 'Address available on request'
        with patch.object(pool.http, 'post') as post:
            self.assertIsNone(pool.calculate(self.prop, 'UNSW'))
            post.assert_not_called()
        self.assertEqual(self.state.summary(), [])
        pool.close()

    def test_model_retries_cannot_bypass_the_request_cap(self):
        service = ScoringService(ScoringConfig(api_key='test'),
            call_guard=lambda *args: self.state.reserve('model', 'qwen-plus', 1), call_observer=self.state.finish)
        response = MagicMock(status_code=429, usage={}, code='Throttling', message='Rate limit')
        with patch('src.services.scoring.dashscope.Generation.call', return_value=response) as call:
            with self.assertRaises(APILimitReached):
                service._call_model('system', 'description')
            self.assertEqual(call.call_count, 1)

    def test_typed_backup_values_roundtrip_without_losing_datetime_decimal_or_bytes(self):
        values = [datetime(2026, 10, 29, 1, 2, 3), Decimal('123.4500'), b'\x00\xff', None]
        restored = json.loads(json.dumps(values, default=encode), object_hook=decode)
        self.assertEqual(restored, values)

    def test_identical_description_reuses_evaluation_but_changed_description_does_not(self):
        def score(prop, **kwargs):
            prop.average_score = 14.0
            prop.scores = [14.0] * 8
            prop.keywords = 'natural light'
            prop.description_cn = '采光充足'
            return prop
        self.prop.description_en = 'A sunny apartment.'
        with patch('enrichment_runner.ScoringConfig', return_value=ScoringConfig(api_key='test')), \
                patch('enrichment_runner.CommutePool') as maps, \
                patch.object(ScoringService, 'process_property', side_effect=score) as model:
            maps.return_value.calculate.return_value = None
            maps.return_value.keys = []
            maps.return_value.cache_hits = 0
            maps.return_value.errors = []
            enrich([self.prop], self.state, use_api=True)
            self.prop.source = PropertySource.REALESTATE
            self.prop.url = 'https://www.realestate.com.au/property-house-nsw-sydney-123'
            enrich([self.prop], self.state, use_api=True)
            self.assertEqual(model.call_count, 1)
            self.prop.description_en = 'A different apartment without sunlight.'
            enrich([self.prop], self.state, use_api=True)
            self.assertEqual(model.call_count, 2)

    def test_failed_route_cannot_reuse_an_unproven_time_from_input(self):
        self.prop.description_en = 'A sunny apartment.'
        self.prop.commute_times = {'UNSW': 1}
        with patch('enrichment_runner.ScoringConfig', return_value=ScoringConfig(api_key='')), \
                patch('enrichment_runner.CommutePool') as maps:
            maps.return_value.calculate.return_value = None
            maps.return_value.keys = []
            maps.return_value.cache_hits = 0
            maps.return_value.errors = []
            result = enrich([self.prop], self.state, use_api=True)
        self.assertNotIn('UNSW', self.prop.commute_times)
        self.assertIn('commute_time', result['records'][0]['checks']['UNSW'])

    def test_unpublishable_price_does_not_spend_model_or_route_allowance(self):
        self.prop.description_en = 'Sunny apartment.'
        self.prop.price_text = 'Contact agent'
        self.prop.price_per_week = 0
        with patch.object(ScoringService, 'process_property') as model, \
                patch('enrichment_runner.CommutePool') as maps:
            maps.return_value.keys = []
            maps.return_value.cache_hits = 0
            maps.return_value.errors = []
            result = enrich([self.prop], self.state, use_api=True)
        model.assert_not_called()
        maps.return_value.calculate.assert_not_called()
        self.assertIn('verified_weekly_price', result['records'][0]['input_errors'])
        self.assertEqual(self.state.summary(), [])


if __name__ == '__main__':
    unittest.main()

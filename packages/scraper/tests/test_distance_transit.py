import copy
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

import requests

from src.models import PropertyData, PropertySource
from src.services.commute_pool import CommutePool
from src.services.enrichment_state import EnrichmentState, APILimitReached


class DistanceTransitTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / 'state.db'
        self.state = EnrichmentState(self.path, 'gift-pilot')
        self.prop = PropertyData(source=PropertySource.DOMAIN, house_id='101', state='NSW',
            address_line1='2/18 George Street', address_line2='redfern-nsw-2016')
        self.keys = [{'provider':'distancematrix', 'api_key':'offline-test-key',
                      'billing_group':'distancematrix-growth-account'}]
        self.pool = CommutePool(self.state, keys=self.keys, max_calls=10)
        self.payload = {'status':'OK', 'origin_addresses':['18 George St, Redfern NSW 2016, Australia'],
            'destination_addresses':['UNSW Sydney, Kensington NSW 2052, Australia'],
            'rows':[{'elements':[{'status':'OK', 'duration':{'value':1380,'text':'23 mins'},
                                 'distance':{'value':5100,'text':'5.1 km'}}]}]}

    def tearDown(self):
        self.pool.close()
        self.state.close()
        self.folder.cleanup()

    def response(self, payload=None):
        response = requests.Response()
        response.status_code = 200
        import json
        response._content = json.dumps(self.payload if payload is None else payload).encode()
        return response

    def test_transit_pair_is_reserved_before_http_and_shared_cache_prevents_another_request(self):
        sent=[]
        def send(url, **kwargs):
            sent.append((url,kwargs))
            self.assertEqual(self.state.db.execute("SELECT status FROM calls WHERE service='distancematrix'").fetchone()[0], 'reserved')
            self.assertAlmostEqual(self.state.credit_budget_summary()['spent_or_reserved_usd'], .002)
            return self.response()
        with patch.object(self.pool.http,'get',side_effect=send):
            self.assertEqual(self.pool.calculate(self.prop,'UNSW'),23)
            other=copy.copy(self.prop);other.source=PropertySource.REALESTATE;other.house_id='999'
            self.assertEqual(self.pool.calculate(other,'UNSW'),23)
        self.assertEqual(len(sent),1)
        url,kwargs=sent[0]
        self.assertEqual(url,'https://api.distancematrix.ai/maps/api/distancematrix/json')
        self.assertEqual(kwargs['params']['mode'],'transit')
        self.assertNotIn('|',kwargs['params']['origins'])
        self.assertNotIn('|',kwargs['params']['destinations'])
        self.assertEqual(kwargs['params']['origins'],'18 George Street, redfern nsw 2016, Australia')
        self.assertIsInstance(kwargs['params']['departure_time'],int)
        self.assertFalse(kwargs['allow_redirects'])
        self.assertEqual(self.state.budget_summary()['spent_or_reserved_usd'],0)
        self.assertEqual(self.state.summary()[0]['requests'],1)

    def test_timeout_is_not_repeated_after_restart_new_day_or_key_change(self):
        sent=[]
        def timeout(*args,**kwargs):
            sent.append(1)
            raise requests.ReadTimeout('Never print a credential-bearing URL')
        with patch.object(self.pool.http,'get',side_effect=timeout):
            self.assertIsNone(self.pool.calculate(self.prop,'UNSW'))
        self.pool.close();self.state.close()
        self.state=EnrichmentState(self.path,'next-run')
        keys=[{**self.keys[0],'api_key':'different-offline-key'}]
        self.pool=CommutePool(self.state,keys=keys,max_calls=10)
        with patch.object(self.pool.http,'get',side_effect=timeout):
            self.assertIsNone(self.pool.calculate(self.prop,'UNSW'))
        self.assertEqual(len(sent),1)
        self.assertAlmostEqual(self.state.credit_budget_summary()['spent_or_reserved_usd'],.002)
        self.assertEqual(self.state.db.execute("SELECT status FROM calls WHERE service='distancematrix'").fetchone()[0],'ReadTimeout')

    def test_lifetime_gift_limit_is_atomic_across_runs_and_months(self):
        self.assertTrue(callable(getattr(self.state,'reserve_credit',None)), 'Gift needs its own lifetime ledger')
        self.state.reserve_credit('distancematrix','distancematrix-growth-account',10,40)
        self.state.close()
        with patch.object(EnrichmentState,'budget_period',return_value='2026-11'):
            self.state=EnrichmentState(self.path,'new-month')
            with self.assertRaises(APILimitReached):
                self.state.reserve_credit('distancematrix','distancematrix-growth-account',10,.002,lifetime_limit=500)
        self.assertEqual(self.state.db.execute("SELECT COUNT(*) FROM calls WHERE service='distancematrix'").fetchone()[0],1)
        self.assertEqual(self.state.budget_summary()['spent_or_reserved_usd'],0)

    def test_exhausted_credit_prevents_http(self):
        self.assertTrue(callable(getattr(self.state,'reserve_credit',None)))
        self.state.reserve_credit('distancematrix','distancematrix-growth-account',10,40)
        sent=[]
        with patch.object(self.pool.http,'get',side_effect=lambda *a,**k:sent.append(1)):
            self.assertIsNone(self.pool.calculate(self.prop,'UNSW'))
        self.assertEqual(sent,[])

    def test_provider_rejection_stops_other_addresses_after_restart(self):
        sent=[]
        response=self.response();response.status_code=403
        with patch.object(self.pool.http,'get',side_effect=lambda *a,**k:(sent.append(1) or response)):
            self.assertIsNone(self.pool.calculate(self.prop,'UNSW'))
        self.pool.close();self.state.close()
        self.state=EnrichmentState(self.path,'next-batch')
        self.pool=CommutePool(self.state,keys=self.keys,max_calls=10)
        other=copy.copy(self.prop);other.address_line1='4/20 George Street'
        with patch.object(self.pool.http,'get',side_effect=lambda *a,**k:(sent.append(1) or response)):
            self.assertIsNone(self.pool.calculate(other,'UNSW'))
        self.assertEqual(len(sent),1)
        self.assertAlmostEqual(self.state.credit_budget_summary()['spent_or_reserved_usd'],.002)

    def test_wrong_origin_is_not_published_as_an_exact_address(self):
        payload=copy.deepcopy(self.payload)
        payload['origin_addresses']=['20 George St, Redfern NSW 2016, Australia']
        with patch.object(self.pool.http,'get',return_value=self.response(payload)):
            self.assertIsNone(self.pool.calculate(self.prop,'UNSW'))
        self.assertTrue(any('ORIGIN_NOT_EXACT' in str(x) for x in self.pool.errors))

    def test_observed_street_type_aliases_match_the_same_building(self):
        from src.services.distance_transit import origin_matches
        for expected,actual in [('Close','Cl'),('Circuit','Cct'),('Boulevard','Bvd'),
                                ('Boulevard','Blvd'),('Alley','Al'),('Alley','Ally')]:
            with self.subTest(expected=expected,actual=actual):
                prop=copy.copy(self.prop);prop.address_line1='2/18 George '+expected
                self.assertTrue(origin_matches(prop,'18 George '+actual+', Redfern NSW 2016, Australia'))

    def test_street_aliases_do_not_relax_number_range_name_or_postcode(self):
        from src.services.distance_transit import origin_matches
        prop=copy.copy(self.prop);prop.address_line1='2/18 George Close'
        for returned in ['20 George Cl, Redfern NSW 2016, Australia',
                         '18-20 George Cl, Redfern NSW 2016, Australia',
                         '18B George Cl, Redfern NSW 2016, Australia',
                         '18 George Cl, Redfern NSW 2017, Australia',
                         '18 George Cl, Redfern VIC 2016, Australia',
                         'George Cl, Redfern NSW 2016, Australia',
                         '18 George Court, Redfern NSW 2016, Australia']:
            with self.subTest(returned=returned):self.assertFalse(origin_matches(prop,returned))
        prop.address_line1='2/18 Al Street'
        self.assertFalse(origin_matches(prop,'18 Alley Street, Redfern NSW 2016, Australia'))

    def test_reviewed_street_type_response_reuses_original_credit_without_http(self):
        self.prop.address_line1='2/18 George Close'
        self.payload['origin_addresses']=['18 George Cl, Redfern NSW 2016, Australia']
        pair=EnrichmentState.key('transit-permanent-v1','2/18 george close','redfern nsw 2016',
                                'UNSW','weekday-08:30')
        request_id=self.state.reserve_credit('distancematrix','distancematrix-growth-account',10,.002)
        self.state.finish(request_id,'DISTANCE_ORIGIN_NOT_EXACT')
        self.state.put(self.state.key('distancematrix-pair-attempt-v1',pair),
                       {'request_id':request_id,'status':'reserved'})
        self.state.put(self.state.key('distancematrix-response-v1',pair),{'request_id':request_id,
            'destination':'University of New South Wales, Kensington NSW 2052, Australia',
            'mode':'transit','data':self.payload})
        with patch.object(self.pool.http,'get',side_effect=AssertionError('No repeat request')):
            self.assertEqual(self.pool.calculate(self.prop,'UNSW'),23)
        self.assertEqual(self.state.db.execute('SELECT status FROM calls').fetchall(),
                         [('DISTANCE_ORIGIN_NOT_EXACT',)])
        self.assertAlmostEqual(self.state.credit_budget_summary()['spent_or_reserved_usd'],.002)

    def test_old_abbreviated_street_permanent_cache_identity_is_unchanged(self):
        self.prop.address_line1='2/18 George Cl'
        pair=EnrichmentState.key('transit-permanent-v1','2/18 george cl','redfern nsw 2016',
                                'UNSW','weekday-08:30')
        self.state.put(pair,{'minutes':23,'mode':'transit','provider':'distancematrix'})
        with patch.object(self.pool.http,'get',side_effect=AssertionError('Old key must remain usable')):
            self.assertEqual(self.pool.calculate(self.prop,'UNSW'),23)
        self.assertEqual(self.state.db.execute('SELECT COUNT(*) FROM calls').fetchone()[0],0)

    def test_street_center_or_wrong_postcode_cannot_substitute_for_an_address(self):
        payload=copy.deepcopy(self.payload)
        payload['origin_addresses']=['George Street, Sydney NSW 2000, Australia']
        with patch.object(self.pool.http,'get',return_value=self.response(payload)):
            self.assertIsNone(self.pool.calculate(self.prop,'UNSW'))
        self.assertTrue(any('ORIGIN_NOT_EXACT' in str(x) for x in self.pool.errors))

    def assert_destination_held(self,destination):
        payload=copy.deepcopy(self.payload);payload['destination_addresses']=[destination]
        sent=[]
        with patch.object(self.pool.http,'get',side_effect=lambda *a,**k:(sent.append(1) or self.response(payload))):
            self.assertIsNone(self.pool.calculate(self.prop,'UNSW'))
            self.assertIsNone(self.pool.calculate(self.prop,'UNSW'))
        self.assertEqual(len(sent),1)
        self.assertTrue(any('DESTINATION_NOT_EXACT' in str(x) for x in self.pool.errors))
        self.assertEqual(self.state.db.execute("SELECT COUNT(*) FROM calls WHERE status='OK'").fetchone()[0],0)

    def test_wrong_state_destination_cannot_become_a_sydney_school_cache(self):
        self.assert_destination_held('University of Melbourne, Parkville VIC 3010, Australia')

    def test_wrong_sydney_university_cannot_become_an_unsw_cache(self):
        self.assert_destination_held('University of Sydney, Camperdown NSW 2006, Australia')

    def test_different_school_name_with_the_expected_postcode_is_still_rejected(self):
        self.assert_destination_held('University of Sydney, Kensington NSW 2052, Australia')

    def test_empty_destination_text_is_not_a_verified_campus(self):
        self.assert_destination_held('')

    def test_named_main_campus_without_a_postcode_requires_the_exact_requested_destination_echo(self):
        self.payload['destination_addresses']=['The University of Sydney']
        self.payload['rows'][0]['elements'][0]['destination']='University of Sydney, Camperdown NSW 2006, Australia'
        with patch.object(self.pool.http,'get',return_value=self.response()):
            self.assertEqual(self.pool.calculate(self.prop,'USYD'),23)

    def test_postcode_alias_is_limited_to_the_observed_unsw_kensington_campus(self):
        self.payload['destination_addresses']=['UNSW Sydney, Sydney NSW 2033, Australia']
        self.payload['rows'][0]['elements'][0]['destination']='University of New South Wales, Kensington NSW 2052, Australia'
        with patch.object(self.pool.http,'get',return_value=self.response()):
            self.assertEqual(self.pool.calculate(self.prop,'UNSW'),23)

    def test_reviewed_campus_label_without_its_exact_request_echo_is_still_held(self):
        self.assert_destination_held('UNSW Sydney, Sydney NSW 2033, Australia')

    def test_spaced_number_range_can_reuse_a_verified_response_after_review_without_http(self):
        self.prop.address_line1='2 - 4 Fern St';self.prop.address_line2='randwick-nsw-2031'
        self.payload['origin_addresses']=['2-4 Fern St, Randwick NSW 2031, Australia']
        from src.services.enrichment_state import EnrichmentState
        from src.services.address_identity import normalized_address
        pair=EnrichmentState.key('transit-permanent-v1',normalized_address(self.prop.address_line1),
            normalized_address(self.prop.address_line2.replace('-',' ')),'UNSW','weekday-08:30')
        request_id=self.state.reserve_credit('distancematrix','distancematrix-growth-account',10,.002)
        self.state.finish(request_id,'DISTANCE_ORIGIN_NOT_EXACT')
        self.state.put(self.state.key('distancematrix-pair-attempt-v1',pair),{'request_id':request_id,'status':'reserved'})
        self.state.put(self.state.key('distancematrix-response-v1',pair),{'request_id':request_id,
            'destination':'University of New South Wales, Kensington NSW 2052, Australia','mode':'transit','data':self.payload})
        with patch.object(self.pool.http,'get',side_effect=AssertionError('Reviewed response must not make another request')):
            self.assertEqual(self.pool.calculate(self.prop,'UNSW'),23)
        self.assertEqual(self.state.db.execute('SELECT status FROM calls').fetchone()[0],'DISTANCE_ORIGIN_NOT_EXACT')
        self.assertAlmostEqual(self.state.credit_budget_summary()['spent_or_reserved_usd'],.002)

    def test_non_success_or_empty_element_is_held_without_another_http(self):
        payload=copy.deepcopy(self.payload);payload['rows']=[]
        sent=[]
        with patch.object(self.pool.http,'get',side_effect=lambda *a,**k:(sent.append(1) or self.response(payload))):
            self.assertIsNone(self.pool.calculate(self.prop,'UNSW'))
            self.assertIsNone(self.pool.calculate(self.prop,'UNSW'))
        self.assertEqual(len(sent),1)
        self.assertAlmostEqual(self.state.credit_budget_summary()['spent_or_reserved_usd'],.002)

    def test_melbourne_does_not_consume_gift(self):
        prop=copy.copy(self.prop);prop.state='VIC';prop.address_line2='carlton-vic-3053'
        sent=[]
        with patch.object(self.pool.http,'get',side_effect=lambda *a,**k:sent.append(1)):
            self.assertIsNone(self.pool.calculate(prop,'UNIMELB'))
        self.assertEqual(sent,[])
        self.assertEqual(self.state.db.execute('SELECT COUNT(*) FROM calls').fetchone()[0],0)


if __name__=='__main__':unittest.main()

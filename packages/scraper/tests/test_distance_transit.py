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

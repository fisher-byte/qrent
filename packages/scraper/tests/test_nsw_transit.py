import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.services.enrichment_state import EnrichmentState
from src.services.nsw_transit import NSWTransit, NSWTransitError, building_address, journey_minutes, compact_response
from src.services.commute_pool import CommutePool
from src.models import PropertyData, PropertySource


class NSWTransitTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.state=EnrichmentState(Path(self.folder.name)/'state.db','offline-nsw-test')
        self.http=MagicMock()
        self.client=NSWTransit(self.state,self.http,'test-key-not-a-real-secret',10)

    def tearDown(self):
        self.state.close();self.folder.cleanup()

    def test_transfer_waiting_is_included_and_driving_is_rejected(self):
        def leg(mode,start,end):
            return {'transportation':{'product':{'class':mode}},
                    'origin':{'departureTimePlanned':start},'destination':{'arrivalTimePlanned':end}}
        legs=[leg(100,'2026-10-30T08:30:00+11:00','2026-10-30T08:35:00+11:00'),
              leg(5,'2026-10-30T09:05:00+11:00','2026-10-30T09:10:00+11:00')]
        self.assertEqual(journey_minutes({'journeys':[{'legs':legs}]}),40)
        legs[0]['transportation']['product']['class']=99
        self.assertEqual(journey_minutes(compact_response('trip',{'journeys':[{'legs':legs}]})),40)
        legs[1]['transportation']['product']={'class':2,'name':'Sydney Metro Network'}
        self.assertEqual(journey_minutes(compact_response('trip',{'journeys':[{'legs':legs}]})),40)
        legs[-1]['transportation']['product']['class']=101
        self.assertIsNone(journey_minutes({'journeys':[{'legs':legs}]}))

    def test_request_reuse_spends_one_shared_daily_reservation(self):
        self.http.get.return_value=MagicMock(status_code=200)
        self.http.get.return_value.json.return_value={'locations':[]}
        with patch('src.services.nsw_transit.time.sleep'):
            self.client.request('stop_finder',{'name_sf':'1 Example Street'})
            self.client.request('stop_finder',{'name_sf':'1 Example Street'})
        self.assertEqual(self.http.get.call_count,1)
        self.assertEqual(self.state.summary()[0]['service'],'nsw')
        self.assertEqual(self.state.summary()[0]['requests'],1)
        self.assertEqual(self.state.budget_summary()['spent_or_reserved_usd'],0)

    def test_failed_or_uncertain_requests_are_not_silently_repeated(self):
        self.http.get.return_value=MagicMock(status_code=403)
        with patch('src.services.nsw_transit.time.sleep'):
            with self.assertRaisesRegex(NSWTransitError,'NSW_HTTP_403'):
                self.client.request('stop_finder',{'name_sf':'1 Example Street'})
            with self.assertRaisesRegex(NSWTransitError,'requires review'):
                self.client.request('stop_finder',{'name_sf':'1 Example Street'})
        self.assertEqual(self.http.get.call_count,1)
        self.assertEqual(self.state.db.execute('SELECT status FROM calls').fetchone()[0],'NSW_HTTP_403')

    def test_locality_or_street_center_cannot_substitute_for_a_property(self):
        self.http.get.return_value=MagicMock(status_code=200)
        self.http.get.return_value.json.return_value={'locations':[{'isBest':True,'type':'locality','coord':[-33.87,151.20]}]}
        with self.assertRaisesRegex(NSWTransitError,'NSW_ADDRESS_NOT_EXACT'):
            self.client.resolve('1 Example Street, Sydney NSW',origin=True)

    def test_nsw_singlehouse_requires_the_actual_number_street_and_postcode(self):
        self.http.get.return_value=MagicMock(status_code=200)
        location={'isBest':True,'type':'singlehouse','coord':[-33.910683,151.223324],
                  'streetName':'Anzac Pde','buildingNumber':'194','postCode':'2033'}
        self.http.get.return_value.json.return_value={'locations':[location]}
        try:
            result=self.client.resolve('194 Anzac Parade, Kensington NSW 2033, Australia',origin=True)
        except NSWTransitError:
            result=None
        self.assertEqual(result,'151.223324:-33.910683:EPSG:4326')
        location['buildingNumber']='196'
        with self.assertRaises(NSWTransitError):
            self.client.resolve('198 Anzac Parade, Kensington NSW 2033, Australia',origin=True)

    def test_geocoding_entrance_does_not_change_property_unit_identity(self):
        self.assertEqual(building_address('2/18 George Street'),'18 George Street')
        self.assertEqual(building_address('Unit 2, 18 George Street'),'18 George Street')
        self.assertEqual(building_address('2-18 George Street'),'2-18 George Street')

    def test_provider_street_abbreviations_and_number_case_do_not_change_the_address(self):
        for expected,actual in [('Magdalene Terrace','Magdalene Tce'),
                                ('Brittain Crescent','Brittain Cr'),('Watermans Quay','Watermans Qy')]:
            with self.subTest(expected=expected):
                self.http.get.return_value=MagicMock(status_code=200)
                self.http.get.return_value.json.return_value={'locations':[{
                    'isBest':True,'type':'singlehouse','coord':[-33.929812,151.153213],
                    'buildingNumber':'11A','streetName':actual,'postCode':'2205'}]}
                self.assertEqual(self.client.resolve('11a '+expected+', Wolli Creek NSW 2205',origin=True),
                                 '151.153213:-33.929812:EPSG:4326')

    def test_nsw_unresolved_pair_is_held_across_runs_for_review(self):
        prop=PropertyData(source=PropertySource.DOMAIN,house_id='123',address_line1='1 Example Street',
                          address_line2='newtown-nsw-2042',state='NSW')
        keys=[{'api_key':'test-key','billing_group':'tfnsw-account','provider':'nsw'}]
        with patch('src.services.commute_pool.NSWTransit.calculate',return_value=None) as request:
            for _ in range(2):
                pool=CommutePool(self.state,keys=keys)
                try:self.assertIsNone(pool.calculate(prop,'UNSW'))
                finally:pool.close()
            self.assertEqual(request.call_count,1)


if __name__=='__main__': unittest.main()

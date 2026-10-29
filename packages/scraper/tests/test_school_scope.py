import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.models import PropertyData, PropertySource
from src.config import TARGET_AREAS
from src.scrapers.realestate import RealEstateScraper
from src.services.school_scope import schools_for_property, capture_areas, schools_for_area
from src.services.inventory_selection import select_inventory


def prop(source='domain', ident='123', first='2/18 Example Street', area='newtown-nsw-2042', state='NSW', **changes):
    return PropertyData(source=PropertySource(source), house_id=ident, address_line1=first,
                        address_line2=area, state=state, price_per_week=600,
                        bedroom_count=2, bathroom_count=1, property_type=2, **changes)


class SchoolScopeTests(unittest.TestCase):
    def test_sydney_preserves_original_ten_areas_per_university_and_overlap(self):
        self.assertEqual({s:len(v) for s,v in TARGET_AREAS.items()}, {'UNSW':10,'USYD':10,'UTS':10})
        self.assertEqual(schools_for_property(prop()), ['UNSW','USYD','UTS'])
        self.assertEqual(schools_for_property(prop(area='zetland-nsw-2017')), ['UNSW'])

    def test_postcode_search_matches_original_realestate_behavior(self):
        self.assertEqual(schools_for_property(prop(source='realestate', area='zetland-nsw-2017')),
                         ['UNSW','USYD','UTS'])
        areas=capture_areas(['sydney'], 'realestate')
        scraper=RealEstateScraper(profile_dir='unused-unit-test-profile')
        urls=[scraper.get_search_url(area) for area in areas]
        self.assertEqual(len(urls),len(set(urls)))
        self.assertIn('sydney-city-nsw', areas)

    def test_state_and_campus_are_not_inferred_from_a_colliding_suburb_name(self):
        self.assertEqual(schools_for_property(prop(area='carlton-vic-3053',state='VIC')), ['UNIMELB'])
        self.assertEqual(schools_for_property(prop(area='carlton-nsw-2218')), [])
        self.assertEqual(schools_for_property(prop(area='clayton-vic-3168',state='VIC')), ['MONASH'])
        self.assertEqual(schools_for_property(prop(area='reservoir-vic-3073',state='VIC')), [])

    def test_query_provenance_keeps_original_school_assignment_and_source_query_overlap(self):
        self.assertEqual(schools_for_area('newtown-nsw-2042','domain'),['UNSW','USYD','UTS'])
        self.assertEqual(schools_for_area('zetland-nsw-2017','domain'),['UNSW'])
        self.assertEqual(schools_for_area('waterloo-nsw-2017','realestate'),['UNSW','USYD','UTS'])
        self.assertEqual(schools_for_area('sydney-city-nsw','realestate'),['USYD','UTS'])
        p=prop(area='glebe-nsw-2037')
        selected=select_inventory([p],{('domain','123'):['UNSW']})
        self.assertEqual(selected['groups'][0]['schools'],['UNSW'])

    def test_cross_source_id_difference_can_merge_but_units_never_collapse(self):
        a=prop(description_en='Domain description')
        b=prop(source='realestate',ident='900',description_en='Different REA description')
        result=select_inventory([a,b,prop(ident='124',first='3/18 Example Street')])
        self.assertEqual(result['selected_source_ids'],3)
        self.assertEqual(len(result['groups']),2)
        merged=next(g for g in result['groups'] if len(g['members'])==2)
        self.assertEqual(merged['review'],[])
        self.assertEqual(merged['primary'].description_en,'Domain description')
        self.assertEqual({(p.source.value,p.house_id) for p in merged['members']}, {('domain','123'),('realestate','900')})

    def test_conflicting_prices_and_same_source_relistings_are_held_for_review(self):
        a=prop();b=prop(source='realestate',ident='999');b.price_per_week=700
        result=select_inventory([a,b,prop(ident='124')])
        group=result['groups'][0]
        self.assertIn('price_conflict',group['review'])
        self.assertIn('multiple_ids_in_one_source',group['review'])

    def test_hidden_or_street_only_addresses_are_not_physical_property_groups(self):
        result=select_inventory([prop(first='Address available on request'), prop(ident='2',first='Example Street'),
                                 prop(ident='3',first='1 bed/2-6 Willis Street')])
        self.assertEqual(len(result['unknown_addresses']),3)
        self.assertEqual(result['groups'],[])


if __name__=='__main__': unittest.main()

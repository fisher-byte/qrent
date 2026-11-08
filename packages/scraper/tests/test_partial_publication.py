"""Partial collection must not block independently valid, identity-safe updates."""
import copy
import importlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime
import subprocess

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from capture_fixtures import RUN, report, save
from src.models import PropertyData, PropertySource
from src.services.inventory_selection import address_key
from src.services.sqlite_store import SQLiteStore


class PartialCaptureTests(unittest.TestCase):
    def module(self):
        return importlib.import_module('ops.publishable_capture')

    def original(self, root, data):
        return save(root/'output'/('school-daily-'+data['run'][:8])/(data['run']+'_result.json'), data)

    def test_failed_region_preserves_original_partial_and_other_regions_can_publish(self):
        with tempfile.TemporaryDirectory() as temp:
            data=report(failed=['newtown-nsw-2042'])
            path=self.original(Path(temp).resolve(),data);before=path.read_bytes()
            actual=self.module().validate_publishable_capture(path)
            self.assertEqual(actual['status'],'partial')
            self.assertEqual(actual['listings'],40)
            self.assertEqual(path.read_bytes(),before)
            summary=self.module().capture_summary(actual)
            self.assertFalse(summary['capture_window_complete'])
            self.assertEqual((summary['successful_queries'],summary['failed_queries']),(40,1))

    def test_missing_source_after_proxy_preflight_does_not_block_other_source(self):
        with tempfile.TemporaryDirectory() as temp:
            data=report()
            data['areas']=[a for a in data['areas'] if a['source']=='domain']
            data['captured_source_ids']=[x for x in data['captured_source_ids'] if x[0]=='domain']
            for sources in data['school_cohorts'].values():sources.pop('realestate',None)
            data.update(status='partial',exit_code=1,listings=22,
                        errors=[{'source':'realestate','stage':'proxy_check','error':'RuntimeError'}])
            path=self.original(Path(temp).resolve(),data)
            actual=self.module().validate_publishable_capture(path)
            summary=self.module().capture_summary(actual)
            self.assertEqual(actual['listings'],22)
            self.assertEqual(summary['unattempted_queries'],19)
            self.assertFalse(summary['capture_window_complete'])

    def test_paused_domain_queries_are_unattempted_after_access_denial(self):
        domain_areas=[a['area'] for a in report()['areas'] if a['source']=='domain']
        data=report(failed=domain_areas)
        for item,error in zip(data['areas'][:len(domain_areas)],data['errors'],strict=True):
            cause='HTTP403' if item['area']==domain_areas[0] else 'HTTP403SourcePaused'
            item.update(error=cause,attempts=int(cause=='HTTP403'))
            error['error']=cause
        with tempfile.TemporaryDirectory() as temp:
            path=self.original(Path(temp).resolve(),data)
            actual=self.module().validate_publishable_capture(path)
            summary=self.module().capture_summary(actual)
        self.assertEqual(summary['successful_queries'],19)
        self.assertEqual(summary['failed_queries'],1)
        self.assertEqual(summary['unattempted_queries'],21)
        self.assertFalse(summary['capture_window_complete'])

    def test_foreign_query_and_unproven_school_identity_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve()
            for mutate in ('query','identity','empty'):
                with self.subTest(mutate=mutate):
                    data=report(failed=['newtown-nsw-2042'])
                    if mutate=='query':data['areas'][1]['area']='unknown-nsw-2999'
                    if mutate=='identity':data['school_cohorts']['UNSW']['domain'].append('999999')
                    if mutate=='empty':data.update(captured_source_ids=[],listings=0,status='failed')
                    path=self.original(root,data)
                    with self.assertRaises(ValueError):self.module().validate_publishable_capture(path)

    def test_latest_partial_is_selected_without_falling_back_to_old_complete(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve()
            old=self.original(root,report())
            data=report(failed=['newtown-nsw-2042'],run='20261031_040005_264602')
            latest=self.original(root,data)
            self.assertEqual(self.module().select_publishable_capture(old.parent),latest)

    def test_subset_success_is_never_reported_as_a_complete_window(self):
        with tempfile.TemporaryDirectory() as temp:
            data=report(queries=[('domain','newtown-nsw-2042')])
            path=self.original(Path(temp).resolve(),data)
            summary=self.module().capture_summary(self.module().validate_publishable_capture(path))
            self.assertEqual(summary['successful_queries'],1)
            self.assertFalse(summary['capture_window_complete'])
            self.assertEqual(summary['unattempted_queries'],40)

    def test_school_without_any_supporting_source_query_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            data=report(queries=[('domain','maroubra-nsw-2035')])
            data['school_cohorts']={'USYD':{'domain':['100']}}
            path=self.original(Path(temp).resolve(),data)
            with self.assertRaises(ValueError):self.module().validate_publishable_capture(path)

    def test_inventory_audit_does_not_call_a_subset_success_a_complete_window(self):
        from ops.select_school_inventory import audit
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve()
            data=report(queries=[('domain','newtown-nsw-2042')])
            path=self.original(root,data)
            store=SQLiteStore(root/'properties.db');store.save([PublishedOwnerTests().prop('domain','100')]);store.close()
            result=audit(root/'properties.db',path.parent,root/'audit',capture_result=path)
            self.assertFalse(result['capture_window_complete'])


class PublishedOwnerTests(unittest.TestCase):
    def prop(self, source, identity, unit='2'):
        return PropertyData(source=PropertySource(source),house_id=identity,state='NSW',postcode='2042',
            address_line1=unit+'/18 Example Street',address_line2='newtown-nsw-2042',
            bedroom_count=2,bathroom_count=1,property_type=2,price_per_week=600,price_text='$600 per week',
            description_en='A sunny apartment with a balcony.',url='https://www.'+
            ('domain.com.au/example-' if source=='domain' else 'realestate.com.au/property-')+identity,
            thumbnail_url='https://cdn.example/house.jpg')

    def test_absent_published_domain_identity_blocks_replacement_but_not_existing_rea_or_other_unit(self):
        prepare=importlib.import_module('ops.prepare_school_enrichment')
        old=self.prop('domain','123');new=self.prop('realestate','900')
        other=self.prop('realestate','901','3')
        with tempfile.TemporaryDirectory() as temp:
            db=Path(temp).resolve()/'properties.db';store=SQLiteStore(db)
            store.save([old,new,other]);store.close()
            owners=prepare.published_address_owners(db,{('domain','123'):71})
            self.assertEqual(owners[address_key(new)],{('domain','123')})
            self.assertFalse(prepare.identity_safe(new,owners,{('domain','123'):71}))
            self.assertTrue(prepare.identity_safe(other,owners,{('domain','123'):71}))
            self.assertTrue(prepare.identity_safe(new,owners,{('domain','123'):71,('realestate','900'):81}))

    def test_legacy_website_owner_missing_from_sqlite_still_blocks_a_duplicate(self):
        prepare=importlib.import_module('ops.prepare_school_enrichment')
        from src.services.address_identity import normalized_address
        new=self.prop('realestate','900')
        streets={normalized_address('2/18 Example Street'):{('domain','123')}}
        self.assertFalse(prepare.identity_safe(new,{}, {('domain','123'):71},streets))
        self.assertTrue(prepare.identity_safe(new,{}, {('realestate','900'):81},streets))

    def test_real_partial_preparation_holds_absent_owner_and_freezes_other_records(self):
        prepare=importlib.import_module('ops.prepare_school_enrichment')
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve();release=root/'release';source=release/'ops/prepare_school_enrichment.py'
            source.parent.mkdir(parents=True);source.write_text('fixture')
            save(release/'DEPLOYMENT.json',{'files_sha256':{}})
            data=report(failed=[a['area'] for a in report()['areas'] if a['source']=='domain'])
            props=[self.prop('realestate',identity,str(index+2))
                   for index,(source_name,identity) in enumerate(data['captured_source_ids'])]
            store=SQLiteStore(root/'properties.db');store.save([self.prop('domain','999'),*props]);store.close()
            capture=save(root/'output/school-daily-20261031'/(RUN+'_result.json'),data)
            before=capture.read_bytes()
            with patch.object(prepare,'__file__',str(source)), \
                    patch.object(prepare,'published_source_priority',return_value=({('domain','999'):71},{})):
                result=prepare.prepare(root/'properties.db',capture,root/'out',root/'writer.env')
            records=json.loads((root/'out/input.json').read_text())
            self.assertEqual(result['eligible_addresses'],18)
            self.assertEqual(result['published_address_conflicts_held'],1)
            self.assertFalse(result['capture_window_complete'])
            self.assertNotIn('122',[p['house_id'] for p in records])
            self.assertEqual(capture.read_bytes(),before)


class PartialDailyTests(unittest.TestCase):
    def test_daily_runs_valid_partial_and_receipt_retains_source_gaps(self):
        daily=importlib.import_module('ops.daily_enrich')
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve()
            data=report(failed=['newtown-nsw-2042'])
            save(root/'output/school-daily-20261031'/(RUN+'_result.json'),data)
            save(root/'enrichment/bootstrap-first-pass.json',{'status':'processed'})
            def run(command,**kwargs):
                if 'enrichment_queue.py' in command[1]:
                    save(root/('output/school-enrichment-'+RUN+'/queue/result.json'),
                         {'addresses':10,'published_addresses':9,'held_addresses':1})
                return subprocess.CompletedProcess(command,0)
            with patch.object(daily.subprocess,'run',side_effect=run), \
                    patch.object(daily,'model_authorized',return_value=False):
                result=daily.run_daily(root,datetime(2026,10,31))
            self.assertEqual(result['capture_status'],'partial')
            self.assertFalse(result['capture_window_complete'])
            self.assertEqual(result['published_addresses'],9)
            self.assertEqual(result['failed_queries'],1)


class AccessDeniedTests(unittest.TestCase):
    def test_detail_http_403_stops_remaining_details(self):
        from src.scrapers.domain import DomainScraper
        from src.config import ScraperConfig
        class Browser:
            _navigation_status=403
            def __init__(self):self.calls=0
            def navigate(self,*args,**kwargs):self.calls+=1;return False
            def close(self):pass
        browser=Browser();scraper=DomainScraper(config=ScraperConfig(retain_browser=True))
        scraper.browser=browser
        props=[PublishedOwnerTests().prop('domain',identity) for identity in ('100','101')]
        for prop in props:prop.description_en=''
        scraper.scrape_property_details(props)
        self.assertEqual(browser.calls,1)
        self.assertEqual(scraper.last_run_error,'HTTP403')

    def test_detail_denial_keeps_captured_rows_and_pauses_remaining_source_queries(self):
        import recovery_runner
        calls=[]
        class Domain:
            def __init__(self,**kwargs):self.last_run_error=None;self.browser=None
            def scrape_area(self,area):
                calls.append(('domain',area))
                prop=PublishedOwnerTests().prop('domain','100');prop.description_en='';return [prop]
            def scrape_property_details(self,props,**kwargs):self.last_run_error='HTTP403';return props
        class Healthy:
            def __init__(self,**kwargs):self.last_run_error=None;self.browser=None
            def scrape_area(self,area):
                calls.append(('realestate',area))
                return [PublishedOwnerTests().prop('realestate','900' if area=='newtown-nsw-2042' else '901')]
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve()
            args=recovery_runner.build_parser().parse_args(['--school-scope','--cities','sydney',
                '--scrapers','domain','realestate','--areas','newtown-nsw-2042','eastgardens-nsw-2036',
                '--pages','7','--retries','0','--skip-proxy-check','--details-per-area','1','--detail-limit','10',
                '--db',str(root/'properties.db'),'--output',str(root/'out')])
            result=recovery_runner.capture(args,{'domain':Domain,'realestate':Healthy})
            self.assertEqual(calls,[('domain','newtown-nsw-2042'),
                ('realestate','newtown-nsw-2042'),('realestate','eastgardens-nsw-2036')])
            self.assertEqual(result['listings'],3)
            self.assertIn(['domain','100'],result['captured_source_ids'])
            self.assertEqual(result['status'],'partial')

    def test_domain_http_403_stops_source_retries_but_other_source_continues(self):
        import recovery_runner
        calls=[]
        class Denied:
            def __init__(self,**kwargs):self.last_run_error='HTTP403';self.browser=None
            def scrape_area(self,area):calls.append(('domain',area));return []
        class Healthy:
            def __init__(self,**kwargs):self.last_run_error=None;self.browser=None
            def scrape_area(self,area):
                calls.append(('realestate',area))
                identity='900' if area=='newtown-nsw-2042' else '901'
                return [PublishedOwnerTests().prop('realestate',identity)]
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve()
            args=recovery_runner.build_parser().parse_args(['--school-scope','--cities','sydney',
                '--scrapers','domain','realestate','--areas','newtown-nsw-2042','eastgardens-nsw-2036',
                '--pages','7','--retries','1','--no-details','--skip-proxy-check',
                '--db',str(root/'properties.db'),'--output',str(root/'out')])
            result=recovery_runner.capture(args,{'domain':Denied,'realestate':Healthy})
            self.assertEqual(calls,[('domain','newtown-nsw-2042'),
                ('realestate','newtown-nsw-2042'),('realestate','eastgardens-nsw-2036')])
            self.assertEqual((result['status'],result['listings']),('partial',2))
            domain=[a for a in result['areas'] if a['source']=='domain']
            self.assertEqual([a['attempts'] for a in domain],[1,0])
            self.assertTrue(all(a.get('error') for a in domain))

    def test_domain_scraper_preserves_access_denied_status_for_source_pause(self):
        from src.scrapers.domain import DomainScraper
        from src.config import ScraperConfig
        class Browser:
            _navigation_status=403
            def navigate(self,*args,**kwargs):return False
        scraper=DomainScraper(config=ScraperConfig(retain_browser=True))
        scraper.browser=Browser()
        self.assertEqual(scraper.scrape_area('newtown-nsw-2042'),[])
        self.assertEqual(scraper.last_run_error,'HTTP403')


if __name__=='__main__':unittest.main()

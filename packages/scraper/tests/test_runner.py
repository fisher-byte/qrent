import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_recovery import listing
from src.models import PropertySource
from src.services.sqlite_store import SQLiteStore


class RunnerTests(unittest.TestCase):
    def test_a_failed_ip_check_after_rotation_cannot_reuse_the_previous_exit(self):
        from recovery_runner import build_parser, capture
        observed=[]
        class RealEstate:
            browser=None
            def __init__(self, **kwargs):observed.append(kwargs.get('geoip_address'))
            def scrape_area(self, area):
                if area.startswith('newtown'):raise RuntimeError('Blocked query')
                return [listing(PropertySource.REALESTATE)]
        with tempfile.TemporaryDirectory() as tmp:
            args=build_parser().parse_args(['--areas','newtown-nsw-2042','kensington-nsw-2033',
                '--scrapers','realestate','--no-details','--retries','1','--db',str(Path(tmp)/'p.db'),'--output',tmp])
            with patch('recovery_runner.verify_au_proxy',side_effect=[{'country':'AU','ip':'203.0.113.7'},
                    RuntimeError('Verification failed'),{'country':'AU','ip':'203.0.113.9'}]):
                capture(args,factories={'realestate':RealEstate})
            self.assertEqual(observed,['203.0.113.7','203.0.113.9'])

    def test_realestate_reuses_verified_ip_for_browser_geolocation_without_public_ip_rediscovery(self):
        from recovery_runner import build_parser, capture
        seen=[]
        class RealEstate:
            browser=None
            def __init__(self, **kwargs):seen.append(kwargs.get('geoip_address'))
            def scrape_area(self, area):return [listing(PropertySource.REALESTATE)]
        with tempfile.TemporaryDirectory() as tmp:
            args=build_parser().parse_args(['--areas','newtown-nsw-2042','--scrapers','realestate',
                '--no-details','--retries','0','--db',str(Path(tmp)/'p.db'),'--output',tmp])
            with patch('recovery_runner.verify_au_proxy',return_value={'country':'AU','ip':'203.0.113.7'}):
                result=capture(args,factories={'realestate':RealEstate})
            self.assertEqual(seen,['203.0.113.7'])
            self.assertNotIn('ip',result['exit'])

    def test_profiles_keep_cookies_and_resource_cache_between_areas_and_cleanup_after_capture(self):
        from recovery_runner import build_parser, capture
        observed=[];paths=[]
        class Domain:
            browser=None
            def __init__(self, profile_dir, **kwargs):
                self.path=Path(profile_dir);paths.append(self.path)
                observed.append((self.path/'cache-marker').exists())
                self.path.mkdir(parents=True,exist_ok=True);(self.path/'cache-marker').write_text('test')
            def scrape_area(self, area):return [listing()]
        with tempfile.TemporaryDirectory() as tmp:
            args=build_parser().parse_args(['--areas','newtown-nsw-2042','kensington-nsw-2033',
                '--scrapers','domain','--no-details','--retries','0','--db',str(Path(tmp)/'p.db'),'--output',tmp])
            capture(args,factories={'domain':Domain})
            self.assertEqual(observed,[False,True])
            self.assertTrue(all(not path.exists() for path in paths))

    def test_school_capture_keeps_query_cohort_for_adjacent_results_before_id_deduplication(self):
        from recovery_runner import build_parser, capture
        class Domain:
            browser = None
            def __init__(self, **kwargs): pass
            def scrape_area(self, area):
                return [listing(address_line2='alexandria-nsw-2015', suburb='Alexandria',
                                state='NSW', postcode='2015')]
        with tempfile.TemporaryDirectory() as tmp:
            args = build_parser().parse_args(['--school-scope', '--cities', 'sydney',
                '--scrapers', 'domain', '--no-details', '--retries', '0',
                '--db', str(Path(tmp)/'p.db'), '--output', tmp])
            with patch('recovery_runner.capture_areas', return_value=['zetland-nsw-2017','newtown-nsw-2042']):
                result = capture(args, factories={'domain':Domain})
            self.assertEqual(result['listings'], 1)
            self.assertEqual(result['school_cohorts'],
                             {school:{'domain':['123']} for school in ('UNSW','USYD','UTS')})

    def test_proxy_outage_keeps_direct_domain_capture(self):
        from recovery_runner import build_parser, capture
        class Domain:
            browser = None
            def __init__(self, **kwargs): pass
            def scrape_area(self, area): return [listing()]
        class PaidSource(Domain):
            def scrape_area(self, area): raise AssertionError("Unverified proxy must not be used")
        with tempfile.TemporaryDirectory() as tmp:
            args = build_parser().parse_args(["--areas", "melbourne-vic-3000", "--no-details",
                "--db", str(Path(tmp)/"p.db"), "--output", tmp])
            with patch("recovery_runner.verify_au_proxy", side_effect=RuntimeError("Unavailable")):
                result = capture(args, factories={"domain": Domain, "realestate": PaidSource})
            self.assertEqual((result["status"], result["listings"], result["exit_code"]), ("partial", 1, 1))
            store = SQLiteStore(args.db)
            self.assertEqual(store.connection.execute("SELECT source FROM properties").fetchall(), [("domain",)])
            store.close()

    def test_failed_detail_download_is_partial_and_keeps_listings(self):
        from recovery_runner import build_parser, capture
        class NoDetails:
            browser = None
            def __init__(self, **kwargs): pass
            def scrape_area(self, area): return [listing()]
            def scrape_property_details(self, properties, **kwargs): pass
        with tempfile.TemporaryDirectory() as tmp:
            args = build_parser().parse_args(["--areas", "melbourne-vic-3000", "--scrapers", "domain",
                "--db", str(Path(tmp)/"p.db"), "--output", tmp, "--retries", "0"])
            result = capture(args, factories={"domain": NoDetails})
            self.assertEqual((result["status"], result["exit_code"], result["listings"]), ("partial", 1, 1))
            self.assertEqual(result["details_failed"], 1)

    def test_empty_crawl_exits_failed_and_keeps_old_active_rows(self):
        from recovery_runner import build_parser, capture
        class Empty:
            browser = None
            def __init__(self, **kwargs): pass
            def scrape_area(self, area): return []
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "properties.db"
            store = SQLiteStore(db)
            store.save([listing(PropertySource.REALESTATE)])
            store.close()
            args = build_parser().parse_args(["--areas", "melbourne-vic-3000", "--db", str(db),
                "--output", tmp, "--no-details", "--skip-proxy-check", "--retries", "0"])
            result = capture(args, factories={"domain": Empty, "realestate": Empty})
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["exit_code"], 1)
            store = SQLiteStore(db)
            self.assertEqual(store.connection.execute("SELECT COUNT(*) FROM properties WHERE is_active=1").fetchone()[0], 1)
            store.close()

    def test_domain_complete_details_skip_realestate_detail_download(self):
        from recovery_runner import build_parser, capture
        class CompleteDomain:
            browser = None
            def __init__(self, **kwargs): pass
            def scrape_area(self, area):
                return [listing(description_en="Sunny", thumbnail_url="https://cdn.example/a.jpg",
                                available_date=datetime(2026, 11, 1))]
            def scrape_property_details(self, properties, **kwargs):
                raise AssertionError("Already complete; must not fetch")
        class SameRealEstate(CompleteDomain):
            def scrape_area(self, area):
                return [listing(PropertySource.REALESTATE, house_id="456")]
        with tempfile.TemporaryDirectory() as tmp:
            args = build_parser().parse_args(["--areas", "melbourne-vic-3000", "--db", str(Path(tmp)/"p.db"),
                                             "--output", tmp, "--skip-proxy-check"])
            result = capture(args, factories={"domain": CompleteDomain, "realestate": SameRealEstate})
            self.assertEqual((result["status"], result["listings"], result["reused"]), ("success", 2, 1))
            self.assertEqual(result["detail_requests"], 0)

    def test_known_same_source_description_is_not_downloaded_again_for_an_unknown_availability(self):
        from recovery_runner import build_parser,capture
        class KnownDomain:
            browser=None
            def __init__(self,**kwargs):pass
            def scrape_area(self,area):return [listing(thumbnail_url='https://cdn.example/house.jpg')]
            def scrape_property_details(self,properties,**kwargs):
                raise AssertionError('Unknown availability must not repeat a successful description download')
        with tempfile.TemporaryDirectory() as temp:
            db=Path(temp)/'properties.db';store=SQLiteStore(db)
            store.save([listing(description_en='A verified sunny apartment.',thumbnail_url='https://cdn.example/house.jpg')]);store.close()
            args=build_parser().parse_args(['--areas','melbourne-vic-3000','--scrapers','domain',
                '--db',str(db),'--output',temp,'--retries','0'])
            result=capture(args,factories={'domain':KnownDomain})
            self.assertEqual(result['status'],'success')
            self.assertEqual((result['detail_requests'],result['reused']),(0,1))
            store=SQLiteStore(db)
            self.assertIsNone(store.recent()[0].available_date)
            store.close()


if __name__ == "__main__":
    unittest.main()

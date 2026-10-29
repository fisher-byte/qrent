import sys
import os
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.config import ScraperConfig
from src.scrapers.domain import DomainScraper
from test_recovery import listing


class DomainSessionTests(unittest.TestCase):
    def test_public_json_detail_parses_full_identity_checked_description_without_scrolling(self):
        from datetime import datetime
        browser=Mock();browser.navigate.return_value=True
        prop=listing();payload={'props':{'rootGraphQuery':{'listingByIdV2':{'id':prop.house_id,
            'headline':'Sunny home','description':'A complete description with a private balcony.',
            'dateAvailableV2':{'isoDate':'2026-11-01'}}}}}
        browser.get_page_source.return_value=payload
        scraper=DomainScraper();scraper.browser=browser
        with patch.dict(os.environ,{'DOMAIN_DETAIL_TRANSPORT':'json'}),patch('src.scrapers.domain.time.sleep'):
            scraper.scrape_property_details([prop])
        self.assertIn('A complete description with a private balcony.',prop.description_en)
        self.assertEqual(prop.available_date,datetime(2026,11,1))
        browser.scroll_page.assert_not_called();browser.close.assert_called_once()

    def test_public_json_other_or_missing_listing_cannot_become_this_property_description(self):
        for listing_payload in ({'id':'other','description':'Wrong home'},{}):
            with self.subTest(listing=listing_payload):
                prop=listing()
                DomainScraper().parse_detail_page(prop,{'props':{'rootGraphQuery':{'listingByIdV2':listing_payload}}})
                self.assertFalse(prop.description_en)

    def test_list_and_detail_share_one_browser_fingerprint_and_close_once(self):
        browser = Mock()
        browser.navigate.return_value = True
        browser.get_page_source.side_effect = ['list', '<div data-testid="listing-details__description"><p>Sunny</p></div>' + '<!--' + 'x' * 5000 + '-->']
        prop = listing()
        with patch('src.scrapers.domain.BrowserManager', return_value=browser) as factory, patch('src.scrapers.domain.time.sleep'):
            scraper = DomainScraper(ScraperConfig(max_pages=1, retain_browser=True))
            with patch.object(scraper, 'parse_listing_page', return_value=[prop]), patch.object(scraper, '_has_next_page', return_value=False):
                scraper.scrape_area('melbourne-vic-3000')
            browser.close.assert_not_called()
            scraper.scrape_property_details([prop])
            self.assertIn('Sunny', prop.description_en)
            factory.assert_called_once()
            browser.close.assert_called_once()

    def test_failed_detail_navigation_is_not_parsed_as_a_success(self):
        browser = Mock()
        browser.navigate.return_value = False
        scraper = DomainScraper()
        scraper.browser = browser
        prop = listing()
        with patch.object(scraper, 'parse_detail_page') as parse, patch('src.scrapers.domain.time.sleep'):
            scraper.scrape_property_details([prop])
        parse.assert_not_called()
        browser.get_page_source.assert_not_called()
        self.assertFalse(prop.description_en)


if __name__ == '__main__':
    unittest.main()

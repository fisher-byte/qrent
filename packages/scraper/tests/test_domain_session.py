import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.config import ScraperConfig
from src.scrapers.domain import DomainScraper
from test_recovery import listing


class DomainSessionTests(unittest.TestCase):
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

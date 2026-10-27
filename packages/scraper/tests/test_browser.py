import sys
import unittest
import os
import socket
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.utils.browser import BrowserManager, BrowserType


class Page:
    def __init__(self, status):
        self.status = status

    def goto(self, *args, **kwargs):
        return self

    def wait_for_timeout(self, milliseconds):
        pass


class BrowserTests(unittest.TestCase):
    def test_domain_can_use_stock_firefox_without_changing_realestate_engine(self):
        context, playwright = MagicMock(), MagicMock()
        context.pages = [MagicMock()]
        playwright.firefox.launch_persistent_context.return_value = context
        launcher = MagicMock()
        launcher.start.return_value = playwright
        with tempfile.TemporaryDirectory() as profile, patch.dict(os.environ, {
            'BROWSER_ENGINE': 'camoufox', 'DOMAIN_BROWSER_ENGINE': 'firefox',
            'PROXY_SOURCES': 'realestate', 'DOMAIN_NATIVE_NETWORK': 'true'}, clear=True), \
                patch('playwright.sync_api.sync_playwright', return_value=launcher), \
                patch('camoufox.sync_api.Camoufox') as camoufox:
            browser = BrowserManager(browser_type=BrowserType.PLAYWRIGHT, profile_dir=profile, source='domain')
            try:
                browser.create_driver()
                playwright.firefox.launch_persistent_context.assert_called_once()
                playwright.chromium.launch_persistent_context.assert_not_called()
                camoufox.assert_not_called()
                self.assertIsNone(browser.egress_proxy)
                self.assertFalse(browser.network_policy.proxied)
                context.route.assert_not_called()
                context.on.assert_any_call('request', browser.network_policy.observe_native)
            finally:
                browser.close()

    def test_missing_engine_closes_its_started_egress_listener(self):
        from src.utils.egress import EgressProxy
        created = []
        def gateway(*args, **kwargs):
            proxy = EgressProxy(*args, **kwargs)
            created.append(proxy)
            return proxy
        try:
            with tempfile.TemporaryDirectory() as profile, patch.dict(os.environ, {
                "PROXY_SERVER": "http://127.0.0.1:9", "PROXY_SOURCES": "realestate",
                "PROXY_COUNTRY": "AU", "BROWSER_ENGINE": "camoufox"}, clear=True), \
                    patch.dict(sys.modules, {"camoufox.sync_api": None}), \
                    patch("src.utils.browser.EgressProxy", side_effect=gateway):
                browser = BrowserManager(browser_type=BrowserType.PLAYWRIGHT, profile_dir=profile)
                with self.assertRaises(ImportError):
                    browser.create_driver()
                self.assertTrue(created)
                with self.assertRaises(OSError):
                    with socket.create_connection(("127.0.0.1", created[0].port), timeout=0.3):
                        pass
        finally:
            for proxy in created:
                proxy.close()

    def test_http_challenge_is_navigation_failure(self):
        for status in (403, 429, 502):
            browser = BrowserManager(browser_type=BrowserType.PLAYWRIGHT)
            browser._playwright_page = Page(status)
            self.assertFalse(browser.navigate("https://www.realestate.com.au/rent/", wait_time=0))


if __name__ == "__main__":
    unittest.main()

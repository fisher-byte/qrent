import sys
import unittest
import os
import json
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
    def test_domain_camoufox_can_keep_one_preset_without_ubo_or_locale_override(self):
        from camoufox import DefaultAddons
        context = MagicMock()
        context.pages = [MagicMock()]
        with tempfile.TemporaryDirectory() as folder:
            preset_path = Path(folder) / 'preset.json'
            preset = {'navigator': {'userAgent': 'Firefox fixture'}}
            preset_path.write_text(json.dumps(preset))
            with patch.dict(os.environ, {
                'DOMAIN_BROWSER_ENGINE': 'camoufox', 'PROXY_SOURCES': 'realestate',
                'DOMAIN_NATIVE_NETWORK': 'true', 'DOMAIN_CAMOUFOX_NO_UBO': 'true',
                'DOMAIN_CAMOUFOX_PRESET_FILE': str(preset_path)}, clear=True), \
                    patch('camoufox.sync_api.Camoufox') as camoufox, \
                    patch('src.utils.browser.time.sleep'):
                camoufox.return_value.__enter__.return_value = context
                browser = BrowserManager(browser_type=BrowserType.PLAYWRIGHT, source='domain',
                                         profile_dir=str(Path(folder) / 'profile'))
                try:
                    browser.create_driver()
                    config = camoufox.call_args.kwargs
                    self.assertEqual(config['fingerprint_preset'], preset)
                    self.assertEqual(config['exclude_addons'], [DefaultAddons.UBO])
                    self.assertNotIn('locale', config)
                    self.assertFalse(config['geoip'])
                    context.route.assert_not_called()
                finally:
                    browser.close()

    def test_direct_domain_json_search_uses_one_homepage_and_rejects_failed_payloads(self):
        url = 'https://www.domain.com.au/rent/sydney-region-nsw/?page=1'
        page = MagicMock()
        page.goto.return_value.status = 200
        page.url = 'https://www.domain.com.au/'
        payload = {'props': {'listingSearchResultIds': [123]}}
        response = {'status': 200, 'url': url, 'type': 'application/json; charset=utf-8', 'body': json.dumps(payload)}
        page.evaluate.return_value = response
        browser = BrowserManager(browser_type=BrowserType.PLAYWRIGHT, source='domain')
        browser._playwright_page = page
        browser.network_policy = MagicMock(proxied=False)
        browser.network_policy.budget.exhausted = False
        with patch.dict(os.environ, {'DOMAIN_SEARCH_TRANSPORT': 'json', 'DOMAIN_NATIVE_NETWORK': 'true'}):
            self.assertTrue(browser.navigate(url, wait_time=0))
            self.assertEqual(browser.get_page_source(), payload)
            self.assertTrue(browser.navigate(url, wait_time=0))
            page.goto.assert_called_once()
            page.evaluate.return_value = {**response, 'status': 403}
            self.assertFalse(browser.navigate(url, wait_time=0))
            self.assertIsNone(browser._search_payload)
            page.evaluate.return_value = {**response, 'body': '{bad-json'}
            self.assertFalse(browser.navigate(url, wait_time=0))
            browser.network_policy.proxied = True
            before = page.evaluate.call_count
            self.assertFalse(browser.navigate(url, wait_time=0))
            self.assertEqual(page.evaluate.call_count, before)

    def test_patchright_domain_preserves_native_fingerprint_and_direct_network(self):
        context, runtime, launcher = MagicMock(), MagicMock(), MagicMock()
        context.pages = [MagicMock()]
        runtime.chromium.launch_persistent_context.return_value = context
        launcher.return_value.start.return_value = runtime
        api = MagicMock(sync_playwright=launcher)
        with tempfile.TemporaryDirectory() as profile, patch.dict(os.environ, {
            'BROWSER_ENGINE': 'camoufox', 'DOMAIN_BROWSER_ENGINE': 'patchright',
            'PROXY_SOURCES': 'realestate', 'DOMAIN_NATIVE_NETWORK': 'true', 'HEADLESS': 'false'}, clear=True), \
                patch.dict(sys.modules, {'patchright': MagicMock(), 'patchright.sync_api': api}), \
                patch('camoufox.sync_api.Camoufox') as camoufox, \
                patch('src.utils.browser.time.sleep'):
            browser = BrowserManager(browser_type=BrowserType.PLAYWRIGHT, profile_dir=profile, source='domain')
            try:
                browser.create_driver()
                config = runtime.chromium.launch_persistent_context.call_args.kwargs
                self.assertTrue(config['no_viewport'])
                self.assertFalse(config['headless'])
                self.assertEqual(config['channel'], 'chrome')
                self.assertFalse({'viewport', 'locale', 'user_agent', 'extra_http_headers'} & config.keys())
                context.route.assert_not_called()
                self.assertFalse(browser.network_policy.proxied)
                camoufox.assert_not_called()
            finally:
                browser.close()

    def test_camoufox_reuses_the_verified_exit_without_another_ip_lookup(self):
        context = MagicMock()
        context.pages = [MagicMock()]
        with tempfile.TemporaryDirectory() as profile, patch.dict(os.environ, {
            'BROWSER_ENGINE': 'camoufox'}, clear=True), \
                patch('src.utils.browser.ProxyPool.config', return_value={'server': 'http://127.0.0.1:9'}), \
                patch('src.utils.browser.EgressProxy'), \
                patch('camoufox.sync_api.Camoufox') as camoufox, \
                patch('src.utils.browser.time.sleep'):
            camoufox.return_value.__enter__.return_value = context
            browser = BrowserManager(browser_type=BrowserType.PLAYWRIGHT, profile_dir=profile,
                                     source='realestate', geoip_address='203.0.113.7')
            try:
                browser.create_driver()
                self.assertEqual(camoufox.call_args.kwargs['geoip'], '203.0.113.7')
            finally:
                browser.close()

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

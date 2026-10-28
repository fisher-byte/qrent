import os
import sys
import unittest
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class NetworkTests(unittest.TestCase):
    def test_sticky_session_and_explicit_rotation_keep_australian_targeting(self):
        from src.utils.network import ProxyPool
        with patch.dict(os.environ, {"PROXY_SERVER": "http://proxy.example:9000",
                                    "PROXY_USERNAME": "customer-country-{country}-session-{session}",
                                    "PROXY_PASSWORD": "secret", "PROXY_COUNTRY": "AU"}, clear=True):
            pool = ProxyPool("realestate")
            first = pool.config()
            self.assertEqual(pool.config(), first)
            pool.rotate()
            self.assertNotEqual(pool.config()["username"], first["username"])
            self.assertIn("country-au-", pool.config()["username"])
            self.assertNotIn("secret", repr(pool))

    def test_media_are_blocked_and_third_party_scripts_are_direct(self):
        from src.utils.network import resource_route
        self.assertEqual(resource_route("https://www.realestate.com.au/photo.jpg", "image"), "block")
        self.assertEqual(resource_route("https://cdn.example/app.js", "script"), "direct")
        self.assertEqual(resource_route("https://www.realestate.com.au/rent/", "document"), "proxy")
        self.assertEqual(resource_route("https://www.realestate.com.au.evil.test/x", "script"), "direct")

    def test_same_origin_scripts_are_direct_but_documents_and_validation_stay_proxied(self):
        from src.utils.network import resource_route
        self.assertEqual(resource_route("https://www.realestate.com.au/static/js/app.js", "script"), "direct")
        for path in ("/challenge/session/ips.js", "/opaque/session/ips.js?KP_UIDz=fixture&x-kpsdk-im=fixture"):
            self.assertEqual(resource_route("https://www.realestate.com.au" + path, "script"), "proxy")
        self.assertEqual(resource_route("https://cdn.example/ips.js", "script"), "direct")
        self.assertEqual(resource_route("https://www.realestate.com.au/app.css", "stylesheet"), "direct")
        self.assertEqual(resource_route("https://www.realestate.com.au/rent/in-2042/list-1", "document"), "proxy")
        self.assertEqual(resource_route("https://www.realestate.com.au/challenge/tl", "xhr"), "proxy")
        self.assertEqual(resource_route("https://www.realestate.com.au/challenge/r", "fetch"), "proxy")
        self.assertEqual(resource_route("https://csp.sec-team.realestate.com.au/report", "fetch"), "direct")

    def test_browser_verification_script_uses_original_browser_transport(self):
        from src.utils.network import NetworkPolicy, TrafficBudget
        from unittest.mock import MagicMock
        policy = NetworkPolicy(proxied=True, budget=TrafficBudget())
        route = MagicMock()
        route.request.url = 'https://www.realestate.com.au/opaque/session/ips.js?x-kpsdk-im=fixture'
        route.request.resource_type = 'script'
        try:
            with patch.object(policy.direct, 'send') as static_download:
                policy.route(route)
                route.continue_.assert_called_once()
                route.fulfill.assert_not_called()
                static_download.assert_not_called()
                self.assertEqual(policy.budget.proxy_requests, 1)
        finally:
            policy.close()

    def test_domain_hosts_stay_direct_when_only_realestate_is_enabled(self):
        from src.utils.network import resource_route, ProxyPool
        with patch.dict(os.environ, {"PROXY_SOURCES": "realestate",
                                    "PROXY_TARGET_HOSTS": "realestate.com.au,domain.com.au"}, clear=True):
            self.assertEqual(resource_route("https://www.domain.com.au/rent/", "document"), "direct")
            self.assertEqual(resource_route("https://www.realestate.com.au/rent/", "document"), "proxy")
            self.assertIsNone(ProxyPool("domain").config())

    def test_static_direct_request_drops_sensitive_headers(self):
        from src.utils.network import direct_headers
        headers = direct_headers({"Cookie": "session=private", "Authorization": "Bearer private",
                                  "Proxy-Authorization": "Basic private", "Accept": "text/css",
                                  "User-Agent": "Test", "Referer": "https://example/?secret=private"})
        self.assertEqual(headers, {"accept": "text/css", "user-agent": "Test"})

    def test_budget_stops_new_requests_after_limit_or_response_bytes(self):
        from src.utils.network import TrafficBudget, BudgetExceeded
        budget = TrafficBudget(max_requests=2, max_proxy_bytes=100)
        budget.reserve("proxy")
        budget.reserve("direct")
        with self.assertRaises(BudgetExceeded):
            budget.reserve("proxy")
        budget = TrafficBudget(max_requests=10, max_proxy_bytes=100)
        budget.record("proxy", 100)
        with self.assertRaises(BudgetExceeded):
            budget.reserve("proxy")
        self.assertEqual(budget.snapshot()["proxy_bytes"], 100)

    def test_direct_cdn_script_keeps_cors_without_set_cookie(self):
        from src.utils.network import NetworkPolicy, TrafficBudget
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/javascript")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Set-Cookie", "private=value")
                self.end_headers()
                self.wfile.write(b"window.app=true")
            def log_message(self, *args): pass
        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        class Route:
            request = SimpleNamespace(url=f"http://127.0.0.1:{server.server_port}/app.js",
                resource_type="script", method="GET", headers={"Cookie": "user=secret"})
            def fulfill(self, **kwargs): self.result = kwargs
            def abort(self): raise AssertionError("Direct script should load")
        policy = NetworkPolicy(proxied=True, budget=TrafficBudget())
        try:
            route = Route()
            policy.route(route)
            self.assertEqual(route.result["body"], b"window.app=true")
            headers = {k.lower():v for k,v in route.result["headers"].items()}
            self.assertEqual(headers.get("access-control-allow-origin"), "*")
            self.assertNotIn("set-cookie", headers)
        finally:
            policy.close()
            server.shutdown()
            server.server_close()


class ResourceRoute:
    def __init__(self, url, resource_type="script", headers=None, method="GET"):
        class Request:
            def sizes(self):
                return {"responseBodySize": 7}
        self.request = Request()
        self.request.url = url
        self.request.resource_type = resource_type
        self.request.method = method
        self.request.headers = headers or {}
        self.result = None
        self.aborted = False
        self.continued = False

    def fulfill(self, **kwargs):
        self.result = kwargs

    def abort(self):
        self.aborted = True

    def continue_(self):
        self.continued = True


class NativeObservationTests(unittest.TestCase):
    def setUp(self):
        from src.utils.network import NetworkPolicy, TrafficBudget
        self.budget = TrafficBudget()
        self.policy = NetworkPolicy(proxied=False, budget=self.budget)

    def tearDown(self):
        self.policy.close()

    def test_native_request_and_completion_are_accounted_as_direct(self):
        request = ResourceRoute('https://www.domain.com.au/rent/').request
        self.policy.observe_native(request)
        self.assertEqual(self.policy.pending[request], 'direct')
        self.assertEqual((self.budget.requests, self.budget.direct_requests), (1, 1))
        self.policy.finished(request)
        self.policy.finished(request)
        self.assertNotIn(request, self.policy.pending)
        self.assertEqual((self.budget.direct_bytes, self.budget.proxy_bytes), (7, 0))

    def test_failed_native_request_uses_existing_failure_accounting(self):
        request = ResourceRoute('https://www.domain.com.au/app.js').request
        self.policy.observe_native(request)
        self.policy.failed(request)
        self.policy.finished(request)
        self.assertEqual((self.budget.requests, self.budget.direct_requests), (1, 1))
        self.assertEqual((self.budget.direct_bytes, self.budget.proxy_bytes), (1024, 0))
        self.assertNotIn(request, self.policy.pending)

    def test_exhausted_native_budget_does_not_escape_event_callback_or_lose_bytes(self):
        self.budget.max_requests = 1
        first = ResourceRoute('https://www.domain.com.au/first.js').request
        second = ResourceRoute('https://www.domain.com.au/second.js').request
        self.policy.observe_native(first)
        self.policy.observe_native(second)
        self.assertTrue(self.budget.exhausted)
        self.assertEqual((self.budget.requests, self.budget.direct_requests), (1, 1))
        self.assertEqual(self.policy.pending[second], 'direct')
        self.policy.finished(second)
        self.policy.failed(first)
        self.assertEqual((self.budget.direct_bytes, self.budget.proxy_bytes), (1031, 0))

    def test_proxied_policy_rejects_native_observation_without_misclassifying_traffic(self):
        from src.utils.network import NetworkPolicy
        policy = NetworkPolicy(proxied=True, budget=self.budget)
        request = ResourceRoute('https://www.realestate.com.au/rent/').request
        try:
            with self.assertRaises(ValueError):
                policy.observe_native(request)
            self.assertFalse(policy.pending)
            self.assertEqual((self.budget.requests, self.budget.direct_requests, self.budget.proxy_requests), (0, 0, 0))
        finally:
            policy.close()


class StaticCacheTests(unittest.TestCase):
    def setUp(self):
        from src.utils.network import NetworkPolicy, TrafficBudget
        self.responses = {}
        self.received = []
        responses, received = self.responses, self.received

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                received.append((self.path, dict(self.headers)))
                status, headers, body = responses.get(self.path, (200, {
                    "Content-Type": "application/javascript",
                    "Cache-Control": "public, max-age=600",
                    "Access-Control-Allow-Origin": "*"}, b"window.app=true"))
                headers = {"Content-Type": "application/javascript", "Date": self.date_time_string(), **headers}
                self.send_response_only(status)
                for name, value in headers.items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True)
        self.thread.start()
        self.budget = TrafficBudget()
        self.policy = NetworkPolicy(proxied=True, budget=self.budget)

    def tearDown(self):
        self.policy.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1)

    def load(self, path="/app.js", **kwargs):
        route = ResourceRoute(f"http://127.0.0.1:{self.server.server_port}" + path, **kwargs)
        self.policy.route(route)
        return route

    def test_public_static_cache_hit_preserves_response_and_counts_download_once(self):
        first, second = self.load(), self.load()
        self.assertEqual(first.result, second.result)
        self.assertEqual(second.result["body"], b"window.app=true")
        self.assertEqual(second.result["headers"]["Access-Control-Allow-Origin"], "*")
        self.assertEqual(len(self.received), 1)
        traffic = self.budget.snapshot()
        self.assertEqual((traffic["requests"], traffic["direct_requests"]), (2, 2))
        self.assertEqual(traffic["direct_bytes"], 15)
        self.assertEqual((traffic["cache_hits"], traffic["cache_saved_body_bytes"]), (1, 15))

    def test_cache_key_uses_full_url_and_safe_request_header_values(self):
        self.load("/app.js?v=1", headers={"Accept-Language": "en-AU", "Cookie": "private=one"})
        self.load("/app.js?v=1", headers={"Accept-Language": "en-AU", "Cookie": "private=two"})
        self.load("/app.js?v=2", headers={"Accept-Language": "en-AU"})
        self.load("/app.js?v=1", headers={"Accept-Language": "fr-FR"})
        self.assertEqual(len(self.received), 3)
        self.assertTrue(all("Cookie" not in headers for _, headers in self.received))
        self.assertEqual(self.budget.snapshot()["cache_hits"], 1)

    def test_forbidden_or_unfresh_responses_are_not_cached(self):
        cases = [
            (200, {"Cache-Control": "public, max-age=600, no-store"}),
            (200, {"Cache-Control": 'public, max-age=600, no-cache="Set-Cookie"'}),
            (200, {"Cache-Control": "private, max-age=600"}),
            (200, {"Cache-Control": "public, max-age=0"}),
            (200, {"Cache-Control": "public, max-age=0, s-maxage=600"}),
            (200, {"Cache-Control": "public, max-age=invalid"}),
            (200, {"Cache-Control": "public, max-age=60, max-age=600"}),
            (200, {"Cache-Control": "public"}),
            (200, {"Cache-Control": "public, max-age=600", "Vary": "Accept, *"}),
            (200, {"Cache-Control": "public, max-age=600", "Set-Cookie": "private=value"}),
            (200, {"Cache-Control": "public, max-age=30", "Age": "31"}),
            (200, {"Cache-Control": "public, max-age=30", "Age": "invalid"}),
            (200, {"Cache-Control": "public, max-age=600", "Date": "Thu, 01 Jan 1970 00:00:00 GMT"}),
            (200, {"Cache-Control": "public, max-age=600", "Content-Type": "text/html"}),
            (200, {}),
            (302, {"Cache-Control": "public, max-age=600", "Location": "/other.js"}),
            (404, {"Cache-Control": "public, max-age=600"}),
        ]
        for number, (status, headers) in enumerate(cases):
            with self.subTest(status=status, headers=headers):
                path = f"/uncached-{number}.js"
                self.responses[path] = status, headers, b"response"
                before = len(self.received)
                self.load(path)
                self.load(path)
                self.assertEqual(len(self.received) - before, 2)
        self.assertTrue(all("Cookie" not in headers for _, headers in self.received))

    def test_request_revalidation_bypasses_and_discards_the_old_cached_response(self):
        for number, control in enumerate(('no-cache', 'max-age=0')):
            with self.subTest(control=control):
                path = f'/refresh-{number}.js'
                before = len(self.received)
                self.load(path)
                self.responses[path] = 200, {"Cache-Control": "public, max-age=600"}, b"window.new=true"
                refreshed = self.load(path, headers={"Cache-Control": control})
                following = self.load(path)
                self.assertEqual(refreshed.result['body'], b"window.new=true")
                self.assertEqual(following.result['body'], b"window.new=true")
                self.assertEqual(len(self.received) - before, 3)

    def test_cache_expires_at_local_ttl_cap_and_remaining_origin_age(self):
        with patch('src.utils.network.time.monotonic', return_value=100) as clock:
            self.load()
            clock.return_value = 399
            self.load()
            clock.return_value = 401
            self.load()
            self.assertEqual(len(self.received), 2)
            self.responses['/short.js'] = 200, {"Cache-Control": "public, max-age=30", "Age": "20"}, b"short"
            clock.return_value = 500
            self.load('/short.js')
            clock.return_value = 509
            self.load('/short.js')
            clock.return_value = 511
            self.load('/short.js')
        self.assertEqual(len(self.received), 4)
        self.assertEqual(self.budget.snapshot()['cache_hits'], 2)

    def test_entry_limit_evicts_least_recently_used_asset(self):
        from src.utils.network import NetworkPolicy
        self.policy.close()
        self.policy = NetworkPolicy(proxied=True, budget=self.budget, cache_max_entries=2)
        for path in ('/a.js', '/b.js', '/a.js', '/c.js', '/a.js', '/b.js'):
            self.load(path)
        self.assertEqual([path for path, _ in self.received], ['/a.js', '/b.js', '/c.js', '/b.js'])

    def test_total_byte_limit_evicts_and_oversized_items_are_not_cached(self):
        from src.utils.network import NetworkPolicy
        self.policy.close()
        self.policy = NetworkPolicy(proxied=True, budget=self.budget,
                                    cache_max_bytes=1000, cache_max_item_bytes=700)
        for path in ('/a.js', '/b.js', '/large.js'):
            self.responses[path] = 200, {"Cache-Control": "public, max-age=600"}, b'x' * (701 if path == '/large.js' else 600)
        for path in ('/a.js', '/a.js', '/b.js', '/a.js', '/large.js', '/large.js'):
            self.load(path)
        self.assertEqual([path for path, _ in self.received], ['/a.js', '/b.js', '/a.js', '/large.js', '/large.js'])
        self.assertLessEqual(self.policy._cache_bytes, 1000)

    def test_cache_is_scoped_to_one_policy_and_close_releases_entries(self):
        from src.utils.network import NetworkPolicy
        self.load()
        self.load()
        self.policy.close()
        self.assertFalse(self.policy._cache)
        self.assertEqual(self.policy._cache_bytes, 0)
        self.policy = NetworkPolicy(proxied=True, budget=self.budget)
        self.load()
        self.assertEqual(len(self.received), 2)

    def test_stylesheet_can_be_cached_with_its_css_mime_type(self):
        self.responses['/app.css'] = 200, {"Cache-Control": "max-age=60", "Content-Type": "text/css"}, b'body{}'
        first = self.load('/app.css', resource_type='stylesheet')
        second = self.load('/app.css', resource_type='stylesheet')
        self.assertEqual(first.result, second.result)
        self.assertEqual(len(self.received), 1)

    def test_validation_and_non_static_requests_never_use_static_cache(self):
        for path, resource_type in (("/page", "document"), ("/data", "fetch"),
                                    ("/data-xhr", "xhr"), ("/challenge/session/ips.js", "script")):
            with self.subTest(path=path, resource_type=resource_type):
                before = len(self.received)
                self.load(path, resource_type=resource_type)
                self.load(path, resource_type=resource_type)
                self.assertEqual(len(self.received) - before, 2)

    def test_cached_resource_still_obeys_request_and_paid_byte_budget(self):
        self.budget.max_requests = 2
        self.load()
        self.load()
        denied = self.load()
        self.assertTrue(denied.aborted)
        self.assertIsNone(denied.result)
        self.assertEqual(len(self.received), 1)
        from src.utils.network import TrafficBudget
        self.policy.budget = TrafficBudget()
        self.policy.budget.record_wire("proxy", "www.realestate.com.au", 30_000_000)
        self.assertTrue(self.load().aborted)
        self.assertEqual(len(self.received), 1)

    def test_only_unproxied_policy_can_opt_into_native_media_requests(self):
        from src.utils.network import NetworkPolicy, TrafficBudget
        for proxied, allow_media, allowed in ((False, True, True), (False, False, False), (True, True, False)):
            for resource_type in ("image", "media", "font"):
                with self.subTest(proxied=proxied, allow_media=allow_media, resource_type=resource_type):
                    budget = TrafficBudget()
                    policy = NetworkPolicy(proxied=proxied, budget=budget, allow_media=allow_media)
                    try:
                        route = ResourceRoute("https://www.realestate.com.au/image", resource_type=resource_type)
                        policy.route(route)
                        self.assertEqual(route.continued, allowed)
                        self.assertEqual(route.aborted, not allowed)
                        if allowed:
                            policy.finished(route.request)
                            self.assertEqual((budget.direct_requests, budget.direct_bytes), (1, 7))
                        self.assertEqual((budget.proxy_requests, budget.proxy_bytes), (0, 0))
                    finally:
                        policy.close()


if __name__ == "__main__":
    unittest.main()

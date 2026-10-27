"""Bounded proxy sessions and resource routing for the property scrapers.

The session idea comes from /root/qrent-scraper's LycheeIP/gost deployment.
Provider credentials and targeting syntax are supplied through private env files.
"""
import os
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from urllib.parse import unquote, urlsplit

import requests


class BudgetExceeded(RuntimeError):
    pass


class ProxyPool:
    def __init__(self, source="realestate"):
        self.source = source
        self.session = os.getenv("PROXY_SESSION") or uuid.uuid4().hex[:12]
        self.server = os.getenv("PROXY_SERVER", "").strip()
        self.username = os.getenv("PROXY_USERNAME", "")
        self.password = os.getenv("PROXY_PASSWORD", "")
        self.country = os.getenv("PROXY_COUNTRY", "AU").upper()
        self.sources = os.getenv("PROXY_SOURCES", "realestate").split(",")
        if self.server:
            parsed = urlsplit(self.server)
            if parsed.scheme not in ("http", "https", "socks5") or not parsed.hostname:
                raise ValueError("PROXY_SERVER must be an HTTP(S) or SOCKS5 proxy URL")
            if self.country != "AU":
                raise ValueError("This deployment requires PROXY_COUNTRY=AU")
            self.username = self.username or unquote(parsed.username or "")
            self.password = self.password or unquote(parsed.password or "")
            host = parsed.hostname
            if ":" in host:
                host = f"[{host}]"
            self.server = f"{parsed.scheme}://{host}" + (f":{parsed.port}" if parsed.port else "")

    def config(self):
        if not self.server or self.source not in self.sources:
            return None
        config = {"server": self.server, "bypass": "localhost,127.0.0.1,[::1]"}
        if self.username:
            config["username"] = self.username.replace("{country}", "au").replace("{session}", self.session)
            config["password"] = self.password
        return config

    def rotate(self):
        self.session = uuid.uuid4().hex[:12]

    def __repr__(self):
        return f"ProxyPool(source={self.source!r}, country={self.country!r})"


def direct_headers(headers):
    allowed = {"accept", "accept-language", "user-agent"}
    return {k.lower(): v for k, v in headers.items() if k.lower() in allowed}


def cache_directives(value):
    directives = {}
    for part in value.split(','):
        name, _, value = part.strip().partition('=')
        name = name.strip().lower()
        if name in directives:
            return None
        directives[name] = value.strip().strip('"')
    return directives


def target_hosts():
    hosts = {host.strip().lower().rstrip('.') for host in
             os.getenv("PROXY_TARGET_HOSTS", "realestate.com.au,domain.com.au").split(',') if host.strip()}
    sources = {source.strip() for source in os.getenv("PROXY_SOURCES", "realestate").split(',')}
    for source, suffix in (("realestate", "realestate.com.au"), ("domain", "domain.com.au")):
        if source not in sources:
            hosts = {host for host in hosts if host != suffix and not host.endswith('.' + suffix)}
    return hosts | {"www." + host for host in hosts if not host.startswith('www.')}


def resource_route(url, resource_type):
    if resource_type in {"image", "media", "font"}:
        return "block"
    # Same-origin bundles and the validation SDK are public downloads too.
    # Only documents and stateful validation/data requests need the AU session.
    if resource_type in {"script", "stylesheet"}:
        return "direct"
    hostname = (urlsplit(url).hostname or "").lower()
    if hostname.rstrip('.') in target_hosts():
        return "proxy"
    return "direct"


@dataclass
class TrafficBudget:
    max_requests: int = 1000
    max_proxy_bytes: int = 30_000_000
    requests: int = 0
    proxy_requests: int = 0
    direct_requests: int = 0
    proxy_bytes: int = 0
    direct_bytes: int = 0
    blocked: int = 0
    exhausted: bool = False
    proxy_wire_bytes: int = 0
    direct_wire_bytes: int = 0
    proxy_connections: int = 0
    direct_connections: int = 0
    cache_hits: int = 0
    cache_saved_body_bytes: int = 0
    _wire_hosts: dict = field(default_factory=dict, repr=False)
    _lock: object = field(default_factory=threading.Lock, repr=False, compare=False)

    def _check(self):
        if (self.exhausted or self.requests >= self.max_requests
                or max(self.proxy_bytes, self.proxy_wire_bytes) >= self.max_proxy_bytes):
            self.exhausted = True
            raise BudgetExceeded("Scraper network budget reached")

    def reserve(self, mode):
        with self._lock:
            self._check()
            self.requests += 1
            if mode == "proxy":
                self.proxy_requests += 1
            else:
                self.direct_requests += 1

    def record(self, mode, size):
        with self._lock:
            if mode == "proxy":
                self.proxy_bytes += max(0, size)
            else:
                self.direct_bytes += max(0, size)
            if self.proxy_bytes >= self.max_proxy_bytes:
                self.exhausted = True

    def reserve_connection(self, mode, host):
        with self._lock:
            if self.exhausted or max(self.proxy_bytes, self.proxy_wire_bytes) >= self.max_proxy_bytes:
                self.exhausted = True
                raise BudgetExceeded("Scraper network budget reached")
            if self.proxy_connections + self.direct_connections >= self.max_requests:
                self.exhausted = True
                raise BudgetExceeded("Scraper connection budget reached")
            name = mode + "_connections"
            setattr(self, name, getattr(self, name) + 1)
            entry = self._wire_hosts.setdefault((mode, host), {"mode": mode, "host": host,
                                                              "connections": 0, "bytes": 0})
            entry["connections"] += 1

    def record_wire(self, mode, host, size):
        with self._lock:
            name = mode + "_wire_bytes"
            setattr(self, name, getattr(self, name) + max(0, size))
            entry = self._wire_hosts.setdefault((mode, host), {"mode": mode, "host": host,
                                                              "connections": 0, "bytes": 0})
            entry["bytes"] += max(0, size)
            if self.proxy_wire_bytes >= self.max_proxy_bytes:
                self.exhausted = True

    def record_cache_hit(self, size):
        with self._lock:
            self.cache_hits += 1
            self.cache_saved_body_bytes += max(0, size)

    def snapshot(self):
        with self._lock:
            result = {name: getattr(self, name) for name in self.__dataclass_fields__ if not name.startswith('_')}
            result["wire_hosts"] = [dict(entry) for entry in self._wire_hosts.values()]
            return result


_run_budget = None


def set_run_budget(budget):
    global _run_budget
    _run_budget = budget


def get_run_budget():
    global _run_budget
    if _run_budget is None:
        _run_budget = TrafficBudget(
            max_requests=int(os.getenv("SCRAPER_MAX_REQUESTS", "1000")),
            max_proxy_bytes=int(os.getenv("SCRAPER_MAX_PROXY_BYTES", "30000000")))
    return _run_budget


class NetworkPolicy:
    def __init__(self, proxied, budget=None, *, allow_media=False,
                 cache_max_bytes=32_000_000, cache_max_entries=128,
                 cache_max_item_bytes=4_000_000, cache_ttl=300):
        self.proxied = proxied
        self.allow_media = allow_media and not proxied
        self.budget = budget or get_run_budget()
        self.pending = {}
        self.direct = requests.Session()
        self.direct.trust_env = False
        self._cache = OrderedDict()
        self._cache_bytes = 0
        self._cache_max_bytes = max(0, cache_max_bytes)
        self._cache_max_entries = max(0, cache_max_entries)
        self._cache_max_item_bytes = max(0, cache_max_item_bytes)
        self._cache_ttl = max(0, cache_ttl)

    def _cache_request(self, request):
        parsed = urlsplit(request.url)
        headers = {name.lower(): value for name, value in request.headers.items()}
        controls = cache_directives(headers.get('cache-control', ''))
        if controls is None or {'no-cache', 'no-store', 'private'} & controls.keys():
            return False
        try:
            if 'max-age' in controls and int(controls['max-age']) <= 0:
                return False
        except ValueError:
            return False
        return (request.resource_type in {'script', 'stylesheet'}
                and parsed.scheme in {'http', 'https'} and parsed.username is None
                and parsed.password is None
                and not any(part in {'challenge', 'challenge-platform', 'captcha', 'recaptcha', 'turnstile'}
                            for part in parsed.path.lower().split('/'))
                and headers.get('pragma', '').lower() != 'no-cache')

    def _cached(self, key):
        now = time.monotonic()
        for old_key, entry in list(self._cache.items()):
            if entry[0] <= now:
                self._cache_bytes -= self._cache.pop(old_key)[1]
        entry = self._cache.get(key)
        if entry:
            self._cache.move_to_end(key)
            return entry[2]
        return None

    def _store_cache(self, key, response, result, resource_type):
        if (response.status_code != 200 or not self._cache_max_entries
                or len(result['body']) > self._cache_max_item_bytes
                or 'set-cookie' in response.headers
                or '*' in {part.strip() for part in response.headers.get('vary', '').split(',')}):
            return
        content_type = response.headers.get('content-type', '').split(';', 1)[0].strip().lower()
        allowed = {'text/css'} if resource_type == 'stylesheet' else {
            'application/javascript', 'text/javascript', 'application/x-javascript',
            'application/ecmascript', 'text/ecmascript'}
        if content_type not in allowed:
            return
        directives = cache_directives(response.headers.get('cache-control', ''))
        if directives is None or {'no-store', 'no-cache', 'private'} & directives.keys():
            return
        try:
            # This is a per-browser cache; a shared-cache lifetime must never
            # relax an origin's stricter max-age for this client.
            lifetime = min(int(directives[name]) for name in ('max-age', 's-maxage') if name in directives)
            age = max(0, int(response.headers.get('age', '0')))
            if response.headers.get('date'):
                age = max(age, time.time() - parsedate_to_datetime(response.headers['date']).timestamp())
            ttl = min(self._cache_ttl, lifetime - age)
        except (ValueError, TypeError, OverflowError):
            return
        cost = (len(result['body']) + len(key[0].encode())
                + sum(len(name.encode()) + len(value.encode()) for name, value in key[1])
                + sum(len(name.encode()) + len(value.encode()) for name, value in result['headers'].items()))
        if ttl <= 0 or cost > self._cache_max_bytes:
            return
        while self._cache and (len(self._cache) >= self._cache_max_entries
                               or self._cache_bytes + cost > self._cache_max_bytes):
            _, entry = self._cache.popitem(last=False)
            self._cache_bytes -= entry[1]
        self._cache[key] = (time.monotonic() + ttl, cost, result)
        self._cache_bytes += cost

    def observe_native(self, request):
        """Count unproxied browser traffic without intercepting its transport."""
        if self.proxied:
            raise ValueError('Native observation requires an unproxied browser')
        # Native requests have already started. Keep their eventual byte counts
        # even when the budget signals navigation should stop.
        self.pending[request] = 'direct'
        try:
            self.budget.reserve('direct')
        except BudgetExceeded:
            pass

    def route(self, route):
        request = route.request
        mode = resource_route(request.url, request.resource_type)
        if mode == "block" and self.allow_media:
            mode = "direct"
        if mode == "block":
            self.budget.blocked += 1
            route.abort()
            return
        if not self.proxied:
            mode = "direct"
        try:
            self.budget.reserve(mode)
            if mode == "proxy" or not self.proxied:
                self.pending[request] = mode
                route.continue_()
                return
            # Independent, credential-free transport for public static resources.
            if request.method != "GET":
                route.abort()
                return
            self.direct.cookies.clear()
            prepared = self.direct.prepare_request(requests.Request(
                'GET', request.url, headers=direct_headers(request.headers)))
            key = (request.url, tuple(sorted((name.lower(), value) for name, value in prepared.headers.items())))
            cacheable = self._cache_request(request)
            cached = self._cached(key) if cacheable else None
            if not cacheable:
                previous = self._cache.pop(key, None)
                if previous:
                    self._cache_bytes -= previous[1]
            if cached is not None:
                self.budget.record_cache_hit(len(cached['body']))
                route.fulfill(**cached)
                return
            with self.direct.send(prepared, timeout=8, stream=True, allow_redirects=False) as response:
                chunks = []
                size = 0
                for chunk in response.iter_content(65536):
                    size += len(chunk)
                    if size > 4_000_000:
                        raise ValueError("Static resource exceeds direct resource limit")
                    chunks.append(chunk)
                self.budget.record("direct", size)
                headers = {k: v for k, v in response.headers.items()
                           if k.lower() in {"content-type", "cache-control", "location", "vary",
                                            "access-control-allow-origin", "access-control-allow-credentials",
                                            "cross-origin-resource-policy", "etag", "last-modified"}}
                result = dict(status=response.status_code, headers=headers, body=b"".join(chunks))
                if cacheable:
                    self._store_cache(key, response, result, request.resource_type)
                route.fulfill(**result)
        except (requests.RequestException, ValueError, BudgetExceeded):
            # Never retry a failed static download through the paid proxy.
            self.budget.blocked += 1
            route.abort()
        finally:
            self.direct.cookies.clear()

    def finished(self, request):
        mode = self.pending.pop(request, None)
        if mode:
            try:
                self.budget.record(mode, sum(request.sizes().values()))
            except Exception:
                # Account conservatively if Chromium cannot provide wire sizes.
                self.budget.record(mode, 65536)

    def failed(self, request):
        mode = self.pending.pop(request, None)
        if mode:
            self.budget.record(mode, 1024)

    def close(self):
        self._cache.clear()
        self._cache_bytes = 0
        self.direct.cookies.clear()
        self.direct.close()

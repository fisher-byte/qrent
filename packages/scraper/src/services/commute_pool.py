"""Bounded Google Maps pool; billing groups, not API keys, own monthly quotas."""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import googlemaps
import requests

from ..config import CommuteConfig, SCHOOL_COORDINATES
from .commute import CommuteService
from .enrichment_state import APILimitReached
from .publication import has_routable_address
from .nsw_transit import NSWTransit
from .address_identity import normalized_address




def load_pool(path=None):
    mode = os.getenv('COMMUTE_PROVIDER', 'google')
    if mode == 'nsw':
        key = os.getenv('NSW_API_KEY')
        return [{'api_key': key, 'billing_group': 'tfnsw-account', 'provider': 'nsw'}] if key else []
    if mode != 'google':
        raise ValueError('Provider not enabled; no automatic paid or bonus fallback')
    path = path or os.getenv('GOOGLE_MAPS_POOL_FILE')
    if path:
        data = json.loads(Path(path).read_text())
        if not isinstance(data, list):
            raise ValueError('Maps pool must be a list')
        for key in data:
            if not all(key.get(name) for name in ('api_key', 'billing_group', 'provider')):
                raise ValueError('Maps entries require api_key, billing_group and provider')
            if key['provider'] not in {'routes', 'legacy'}:
                raise ValueError('Maps provider must be routes or legacy')
        return data
    if os.getenv('GOOGLE_MAPS_API_KEY'):
        return [{'api_key': os.environ['GOOGLE_MAPS_API_KEY'],
                 'provider': os.getenv('GOOGLE_MAPS_API_MODE', 'routes'),
                 'billing_group': os.getenv('GOOGLE_MAPS_BILLING_GROUP', 'qrent'),
                 'monthly_limit': int(os.getenv('GOOGLE_MAPS_MONTHLY_REQUEST_LIMIT', '9500'))}]
    return []


class CommutePool:
    def __init__(self, state, *, keys=None, max_calls=14):
        self.state, self.max_calls = state, max_calls
        self.keys = load_pool() if keys is None else keys
        self.disabled = set()
        self.cache_hits = 0
        self.errors = []
        self.addresses = CommuteService(CommuteConfig(api_key=''))
        self.http = requests.Session()
        self.http.trust_env = False  # Never charge the residential proxy for Maps.
        # A separate, ordinary egress may be needed from mainland China. Do not
        # inherit HTTP(S)_PROXY, which may point at paid residential traffic.
        normal_proxy = os.getenv('GOOGLE_MAPS_HTTP_PROXY')
        if normal_proxy:
            self.http.proxies.update({'http': normal_proxy, 'https': normal_proxy})

    def calculate(self, prop, school):
        if not has_routable_address(prop):
            return None
        destination = SCHOOL_COORDINATES[school]
        origin = self.addresses._get_address(prop)
        if not origin:
            return None
        departure = (datetime.now(ZoneInfo('Australia/Sydney')).replace(hour=8, minute=30, second=0, microsecond=0)
                     + timedelta(days=1))
        while departure.weekday() >= 5:
            departure += timedelta(days=1)
        # Successful pairs are permanent until an explicit refresh. Keep unit
        # numbers and street-number ranges distinct during normalization.
        key = self.state.key('transit-permanent-v1', normalized_address(prop.address_line1),
                             normalized_address(prop.address_line2.replace('-', ' ')), school, 'weekday-08:30')
        cached = self.state.get(key)
        if cached:
            self.cache_hits += 1
            return cached['minutes']
        if self.keys and self.keys[0]['provider'] == 'nsw':
            group=self.keys[0]['billing_group']
            if group in self.disabled:
                return None
            if prop.state != 'NSW':
                if 'NSW_ONLY_NO_FALLBACK' not in self.errors:
                    self.errors.append('NSW_ONLY_NO_FALLBACK')
                return None
            try:
                client = NSWTransit(self.state, self.http, self.keys[0]['api_key'], self.max_calls)
                minutes = client.calculate(prop, destination, departure)
                if minutes is not None:
                    self.state.put(key, {'minutes': minutes, 'mode': 'transit', 'provider': 'nsw',
                        'attribution': 'Transport for NSW', 'requested_at': datetime.now(timezone.utc).isoformat()})
                return minutes
            except Exception as exc:
                message = str(exc) if isinstance(exc, (RuntimeError, APILimitReached)) else type(exc).__name__
                self.errors.append({'provider': 'nsw', 'error': message})
                if (isinstance(exc,APILimitReached) or message in
                        {'NSW_HTTP_401','NSW_HTTP_403','NSW_HTTP_429','NSW_HTTP_500',
                         'NSW_HTTP_502','NSW_HTTP_503','ReadTimeout','ConnectTimeout','ConnectionError'}):
                    self.disabled.add(group)
                return None
        for entry in self.keys:
            group = entry['billing_group']
            if group in self.disabled:
                continue
            try:
                # This integration currently reserves only the verified free
                # Essentials allowance. Configuration cannot enable paid usage.
                monthly_limit = min(9500, max(0, int(entry.get('monthly_limit', 9500))))
                request_id = self.state.reserve('maps', group, self.max_calls, monthly_limit)
            except APILimitReached as exc:
                self.errors.append(str(exc))
                continue
            try:
                if entry['provider'] == 'routes':
                    response = self.http.post('https://routes.googleapis.com/directions/v2:computeRoutes',
                        headers={'X-Goog-Api-Key': entry['api_key'], 'X-Goog-FieldMask': 'routes.duration'},
                        json={'origin': {'address': origin}, 'destination': {'address': destination},
                              'travelMode': 'TRANSIT', 'computeAlternativeRoutes': False,
                              'departureTime': departure.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')},
                        timeout=25)
                    data = response.json()
                    if response.status_code != 200:
                        # Never include request URLs/headers or supplier messages
                        # in logs: those can contain a key.
                        code = data.get('error', {}).get('status', str(response.status_code))
                        raise RuntimeError(code)
                    routes = data.get('routes') or []
                    seconds = float(routes[0]['duration'].removesuffix('s')) if routes else None
                else:
                    client = googlemaps.Client(key=entry['api_key'], timeout=25, retry_over_query_limit=False)
                    client.session.trust_env = False
                    client.session.proxies.update(self.http.proxies)
                    routes = client.directions(origin=origin, destination=destination, mode='transit',
                                               departure_time=departure, alternatives=False)
                    seconds = routes[0]['legs'][0]['duration']['value'] if routes else None
                self.state.finish(request_id, 'OK' if seconds else 'ZERO_RESULTS')
                if seconds is None:
                    self.state.put(key, {'minutes': None, 'mode': 'transit', 'status': 'ZERO_RESULTS',
                                         'requested_at': datetime.now(timezone.utc).isoformat()})
                    return None  # No invented driving multiplier in a transit field.
                minutes = int(round(seconds / 60))
                if 0 < minutes <= 1440:
                    self.state.put(key, {'minutes': minutes, 'mode': 'transit', 'provider': entry['provider'],
                                         'requested_at': datetime.now(timezone.utc).isoformat()})
                    return minutes
                return None
            except Exception as exc:
                self.state.finish(request_id, type(exc).__name__)
                code = getattr(exc, 'status', None) or (str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__)
                self.errors.append({'billing_group': group, 'error': code})
                # Shared-account billing/quota failures are not retried through
                # another key in the same billing group.
                self.disabled.add(group)
        return None

    def close(self):
        self.http.close()

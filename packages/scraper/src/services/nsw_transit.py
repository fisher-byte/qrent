"""Free TfNSW journeys with pre-request reservations and durable request reuse."""
import hashlib
import math
import re
import time
from datetime import datetime, timezone

from .enrichment_state import APILimitReached
from .address_identity import normalized_address


class NSWTransitError(RuntimeError):
    pass


def compact_response(endpoint,data):
    """Cache route evidence, without duplicating large stop catalogues/shapes."""
    result={key:data[key] for key in ('version','systemMessages') if key in data}
    if endpoint=='stop_finder':
        fields=('id','name','coord','streetName','buildingNumber','postCode','type','matchQuality','isBest','parent')
        result['locations']=[{key:loc[key] for key in fields if key in loc} for loc in data.get('locations',[])]
    elif endpoint=='trip':
        journeys=[]
        for journey in data.get('journeys',[]):
            legs=[]
            for leg in journey.get('legs',[]):
                transport=leg.get('transportation',{})
                fields=('name','coord','departureTimePlanned','arrivalTimePlanned')
                legs.append({'transportation':{key:transport[key] for key in ('product','number','name') if key in transport},
                             'origin':{k:v for k,v in leg.get('origin',{}).items() if k in fields},
                             'destination':{k:v for k,v in leg.get('destination',{}).items() if k in fields},
                             'duration':leg.get('duration')})
            journeys.append({'legs':legs})
        result['journeys']=journeys
    return result


def building_address(value):
    """Geocode a building entrance; property/route identity still retains the unit."""
    value = (value or '').strip()
    match = re.match(r'^(?:(?:unit|apartment|apt|flat)\s+)?[\w-]+/(\d[\w-]*\s+.+)$', value, re.I)
    if not match:
        match = re.match(r'^(?:unit|apartment|apt|flat)\s+[\w-]+\s*,?\s+(\d[\w-]*\s+.+)$', value, re.I)
    return match.group(1) if match else value


def journey_minutes(data):
    """Include transfer waiting; reject drive/taxi legs instead of faking transit."""
    for journey in data.get('journeys') or []:
        legs = journey.get('legs') or []
        if not legs:
            continue
        classes = [leg.get('transportation', {}).get('product', {}).get('class') for leg in legs]
        # TfNSW uses both 99 and 100 for footpath legs.
        if any(value not in {1, 4, 5, 7, 9, 11, 99, 100} for value in classes):
            continue
        try:
            start = datetime.fromisoformat(legs[0]['origin']['departureTimePlanned'].replace('Z', '+00:00'))
            finish = datetime.fromisoformat(legs[-1]['destination']['arrivalTimePlanned'].replace('Z', '+00:00'))
            seconds = (finish - start).total_seconds()
        except (KeyError, ValueError, TypeError):
            continue
        if math.isfinite(seconds) and 0 < seconds <= 86400:
            return max(1, round(seconds / 60))
    return None


class NSWTransit:
    BASE = 'https://api.transport.nsw.gov.au/v1/tp/'

    def __init__(self, state, session, key, max_calls):
        self.state, self.http, self.api_key, self.max_calls = state, session, key, max_calls
        self.credential_tag = hashlib.sha256(key.encode()).hexdigest()

    def request(self, endpoint, params):
        params = {'outputFormat': 'rapidJSON', 'coordOutputFormat': 'EPSG:4326', **params}
        cache_key = self.state.key('nsw-http-v1', endpoint, params)
        cached = self.state.get(cache_key)
        if cached is not None:
            return cached
        attempt_key = self.state.key('nsw-attempt-v1', self.credential_tag, endpoint, params)
        if self.state.get(attempt_key):
            raise NSWTransitError('NSW previous failed or uncertain request requires review')
        request_id = self.state.reserve('nsw', 'tfnsw-account', self.max_calls, monthly_limit=59000)
        self.state.put(attempt_key, {'request_id': request_id, 'status': 'reserved'})
        pace_key = self.state.key('nsw-last-request-v1')
        previous = self.state.get(pace_key) or {}
        time.sleep(max(0, previous.get('time', 0) + .30 - time.time()))
        self.state.put(pace_key, {'time': time.time()})
        try:
            response = self.http.get(self.BASE + endpoint,
                headers={'Authorization': 'apikey ' + self.api_key}, params=params, timeout=25)
            self.state.finish(request_id, 'HTTP_' + str(response.status_code))
            if response.status_code != 200:
                raise NSWTransitError('NSW_HTTP_' + str(response.status_code))
            data = response.json()
            if not isinstance(data, dict):
                raise NSWTransitError('NSW_INVALID_JSON')
            expected='locations' if endpoint=='stop_finder' else 'journeys'
            if expected not in data:
                raise NSWTransitError('NSW_INVALID_RESPONSE')
            data=compact_response(endpoint,data)
            self.state.put(cache_key, data)
            self.state.put(attempt_key, {'request_id': request_id, 'status': 'OK'})
            return data
        except Exception as error:
            self.state.finish(request_id, str(error) if isinstance(error, NSWTransitError) else type(error).__name__)
            # No URL, supplier error text, or authorization headers in logs.
            if isinstance(error, (NSWTransitError, APILimitReached)):
                raise
            raise NSWTransitError(type(error).__name__) from None

    def resolve(self, address, *, origin=False):
        coordinate_key = self.state.key('nsw-coordinate-v1', address, origin)
        cached = self.state.get(coordinate_key)
        if cached:
            return cached['coord']
        # This NSW-local index treats "Australia" as a place-name token (e.g.
        # Australia St). Keep full identity for caching/validation, not search.
        query = re.sub(r',?\s*Australia\s*$', '', address, flags=re.I).strip()
        data = self.request('stop_finder', {'type_sf': 'any', 'name_sf': query,
                                           'anyMaxSizeHitList': 5})
        locations = [loc for loc in data.get('locations', []) if loc.get('isBest') is True]
        for location in locations:
            # A whole locality or an approximate street center cannot stand in
            # for a property address. A named POI is permitted for a campus.
            allowed = {'address', 'singlehouse'} if origin else {'poi'}
            if location.get('type') not in allowed:
                continue
            if origin:
                number, _, street = address.split(',', 1)[0].strip().partition(' ')
                postcode = re.search(r'\bNSW\s+(\d{4})\b', address, re.I)
                if (str(location.get('buildingNumber', '')) != number
                        or normalized_address(location.get('streetName', '')) != normalized_address(street)
                        or not postcode or str(location.get('postCode', '')) != postcode.group(1)):
                    continue
            elif address.split(',',1)[0].casefold() not in location.get('name','').casefold():
                continue
            coord = location.get('coord') or []
            if len(coord) != 2 or not -34.5 < coord[0] < -33 or not 150 < coord[1] < 152.5:
                continue
            result = f'{coord[1]}:{coord[0]}:EPSG:4326'
            self.state.put(coordinate_key, {'coord': result, 'name': location.get('name'),
                                          'attribution': 'Transport for NSW'})
            return result
        raise NSWTransitError('NSW_ADDRESS_NOT_EXACT')

    def calculate(self, prop, destination, departure):
        if prop.state != 'NSW':
            return None
        origin = building_address(prop.address_line1) + ', ' + prop.address_line2.replace('-', ' ') + ', Australia'
        first = self.resolve(origin, origin=True)
        last = self.resolve(destination)
        data = self.request('trip', {'type_origin': 'coord', 'name_origin': first,
            'type_destination': 'coord', 'name_destination': last,
            'depArrMacro': 'dep', 'itdDate': departure.strftime('%Y%m%d'),
            'itdTime': departure.strftime('%H%M'), 'calcNumberOfTrips': 3, 'TfNSWTR': 'true'})
        return journey_minutes(data)

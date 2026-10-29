"""Explicit 1x1 Accurate transit requests against the existing non-cash gift."""
import math
import re
import hashlib

from .address_identity import normalized_address
from .nsw_transit import building_address


class DistanceTransitError(RuntimeError):
    pass


def origin_matches(prop, returned):
    if not isinstance(returned,str):
        return False
    expected=normalized_address(building_address(prop.address_line1))
    actual=normalized_address(building_address(returned.split(',',1)[0]))
    postcode=re.search(r'\bNSW\s+(\d{4})\b',prop.address_line2.replace('-',' '),re.I)
    return bool(expected==actual and postcode and
                re.search(r'\bNSW\s+'+postcode.group(1)+r'\b',returned,re.I))


def destination_matches(expected,returned):
    if not isinstance(returned,str):return False
    postcode=re.search(r'\bNSW\s+(\d{4})\b',expected,re.I)
    if not postcode or not re.search(r'\bNSW\s+'+postcode.group(1)+r'\b',returned,re.I):
        return False
    names={
        'university of new south wales':('university of new south wales','unsw'),
        'university of sydney':('university of sydney',),
        'university of technology sydney':('university of technology sydney','uts')}
    campus=expected.split(',',1)[0].strip().casefold()
    return any(re.search(r'\b'+re.escape(name)+r'\b',returned,re.I) for name in names.get(campus,()))


class DistanceTransit:
    BASE='https://api.distancematrix.ai/maps/api/distancematrix/json'

    def __init__(self,state,session,key,max_calls):
        self.state,self.http,self.api_key,self.max_calls=state,session,key,max_calls
        self.provider_hold=state.key('distancematrix-provider-hold-v1',hashlib.sha256(key.encode()).hexdigest())

    def calculate(self,prop,destination,departure,pair_key):
        if prop.state!='NSW':
            return None
        if self.state.get(self.provider_hold):
            raise DistanceTransitError('DISTANCE_PROVIDER_HELD_FOR_REVIEW')
        attempt=self.state.key('distancematrix-pair-attempt-v1',pair_key)
        if self.state.get(attempt):
            raise DistanceTransitError('DISTANCE_PREVIOUS_ATTEMPT_REQUIRES_REVIEW')
        origin=building_address(prop.address_line1)+', '+prop.address_line2.replace('-',' ')+', Australia'
        if '|' in origin or '|' in destination:
            raise DistanceTransitError('DISTANCE_ONE_ELEMENT_ONLY')
        request_id=self.state.reserve_credit('distancematrix','distancematrix-growth-account',self.max_calls,.002)
        self.state.put(attempt,{'request_id':request_id,'status':'reserved'})
        try:
            response=self.http.get(self.BASE,params={'key':self.api_key,'origins':origin,
                'destinations':destination,'mode':'transit','departure_time':int(departure.timestamp()),
                'language':'en','units':'metric'},timeout=25,allow_redirects=False)
            if response.status_code!=200:
                raise DistanceTransitError('DISTANCE_HTTP_'+str(response.status_code))
            data=response.json()
            if not isinstance(data,dict) or data.get('status')!='OK':
                raise DistanceTransitError('DISTANCE_INVALID_RESPONSE')
            origins=data.get('origin_addresses') or []
            destinations=data.get('destination_addresses') or []
            rows=data.get('rows') or []
            if len(origins)!=1 or len(destinations)!=1 or len(rows)!=1 or len(rows[0].get('elements') or [])!=1:
                raise DistanceTransitError('DISTANCE_INVALID_MATRIX')
            if not origin_matches(prop,origins[0]):
                raise DistanceTransitError('DISTANCE_ORIGIN_NOT_EXACT')
            if not destination_matches(destination,destinations[0]):
                raise DistanceTransitError('DISTANCE_DESTINATION_NOT_EXACT')
            element=rows[0]['elements'][0]
            if element.get('status')!='OK':
                raise DistanceTransitError('DISTANCE_NO_TRANSIT_ROUTE')
            seconds=float(element.get('duration',{}).get('value',0))
            if not math.isfinite(seconds) or not 0<seconds<=86400:
                raise DistanceTransitError('DISTANCE_INVALID_DURATION')
            minutes=max(1,round(seconds/60))
            self.state.finish(request_id,'OK')
            self.state.put(attempt,{'request_id':request_id,'status':'OK','origin':origins[0],
                                   'destination':destinations[0],'mode':'transit','minutes':minutes})
            return minutes
        except Exception as exc:
            code=str(exc) if isinstance(exc,DistanceTransitError) else type(exc).__name__
            self.state.finish(request_id,code)
            if code.startswith('DISTANCE_HTTP_') or code in {'ReadTimeout','ConnectTimeout',
                    'ConnectionError','JSONDecodeError','DISTANCE_INVALID_RESPONSE'}:
                self.state.put(self.provider_hold,{'request_id':request_id,'reason':code})
            raise DistanceTransitError(code) from None

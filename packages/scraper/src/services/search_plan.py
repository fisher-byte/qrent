"""Verified metro queries and partitions that stay below each site's result window."""
import hashlib
import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from urllib.parse import urlencode

from bs4 import BeautifulSoup
from ..utils.embedded import domain_search, realestate_search, residential_listings


class SearchMetadataError(RuntimeError):
    pass


@dataclass(frozen=True)
class SearchQuery:
    source: str
    city: str
    region: str = ''
    atlas_id: str = ''
    low: int | None = None
    high: int | None = None
    beds: int | None = None

    @property
    def key(self):
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()[:20]

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        return cls(**value)

    def url(self, page=1):
        state = 'nsw' if self.city == 'sydney' else 'vic'
        if self.source == 'domain':
            params = {'excludedeposittaken': '1', 'sort': 'dateupdated-desc', 'page': str(page)}
            if self.low is not None:
                params['price'] = f"{self.low}-{self.high if self.high is not None else 'any'}"
            if self.beds is not None:
                params['bedrooms'] = f"{self.beds}-{self.beds if self.beds < 5 else 'any'}"
            return f"https://www.domain.com.au/rent/{self.city}-region-{state}/?" + urlencode(params)
        prefix = ''
        if self.low is not None:
            prefix = f"between-{self.low}-{self.high if self.high is not None else 'any'}-"
        if self.beds is not None:
            prefix = f'with-{self.beds}-bedrooms-' + prefix
        region = self.region.lower().replace(' ', '+')
        return (f'https://www.realestate.com.au/rent/{prefix}in-{region}/list-{page}'
                '?includeSurrounding=false&activeSort=list-date')

    def split(self):
        low = self.low or 0
        if self.high is None:
            middle = max(750, low * 2)
        else:
            if self.high - low <= 1:
                if self.beds is not None:
                    raise SearchMetadataError('Result window remains truncated for a single price/bedroom bucket')
                return [replace(self, beds=beds) for beds in range(6)]
            middle = (low + self.high) // 2
        # Both filters include the boundary. Source/id deduplication removes the overlap.
        return [replace(self, low=low, high=middle), replace(self, low=middle)]


@dataclass
class SearchSnapshot:
    total: int
    pages: int
    current: int
    page_size: int
    scope: str
    request: dict
    result_ids: frozenset | None = None

    @property
    def truncated(self):
        return self.total > self.pages * self.page_size


def read_snapshot(source, html, query, page=1):
    try:
        soup = html if isinstance(html, dict) else BeautifulSoup(html, 'html.parser')
        if source == 'domain':
            data = domain_search(soup)
            request = data['pageViewMetadata']['searchRequest']
            ordering = request.get('sort') or {}
            if ordering.get('sortKey') != 'DateUpdated' or ordering.get('direction') != 'Descending':
                raise SearchMetadataError('Domain ignored newest-first ordering; incremental early stop is unsafe')
            locations = request['locations']
            state = 'NSW' if query.city == 'sydney' else 'VIC'
            expected = query.city.title() + ' Region'
            if locations != [{'region': expected, 'state': state}]:
                raise SearchMetadataError('Domain resolved a different metro area')
            known_fields = {'page', 'pageSize', 'locations', 'sort', 'listingType', 'ruralOnly', 'tags',
                            'excludeDepositTaken', 'minPrice', 'maxPrice', 'minBedrooms', 'maxBedrooms'}
            if (set(request) - known_fields or request.get('listingType') != 'Rent'
                    or request.get('ruralOnly', False) is not False or request.get('tags')
                    or request.get('excludeDepositTaken') is not True or request.get('page') != page):
                raise SearchMetadataError('Domain applied unrequested search restrictions or a different page')
            snapshot = SearchSnapshot(int(data['totalListings']), int(data['totalPages']),
                                      int(data['currentPage']), int(request['pageSize']), expected, request,
                                      frozenset(str(value) for value in data['listingSearchResultIds']))
            minimum, maximum = request.get('minPrice', 0), request.get('maxPrice')
            min_beds, max_beds = request.get('minBedrooms'), request.get('maxBedrooms')
        else:
            search = realestate_search(soup)
            resolved = search['resolvedQuery']
            localities = resolved['localities']
            if query.atlas_id and not any(item.get('atlasId') == query.atlas_id for item in localities):
                raise SearchMetadataError('Real Estate resolved a different area')
            request = json.loads(resolved['metadata']['savedSearchQuery'])
            if request.get('sortType') != 'new-desc':
                raise SearchMetadataError('Real Estate ignored newest-first ordering; incremental early stop is unsafe')
            results = search['results']
            pagination = results['pagination']
            snapshot = SearchSnapshot(int(results['totalResultsCount']), int(pagination['maxPageNumberAvailable']),
                                      int(pagination['page']), int(request['pageSize']),
                                      ', '.join(item['display'] for item in localities), request,
                                      frozenset(str(item['id']) for item in residential_listings(results)))
            price = request.get('filters', {}).get('priceRange') or {}
            minimum, maximum = price.get('minimum', 0), price.get('maximum')
            bedrooms = request.get('filters', {}).get('bedroomRange') or {}
            min_beds, max_beds = bedrooms.get('minimum'), bedrooms.get('maximum')
        if snapshot.current != page or snapshot.total < 0 or snapshot.pages < 0 or snapshot.page_size < 1:
            raise SearchMetadataError('Search pagination is inconsistent or redirected')
        if (0 if minimum is None else minimum) != (query.low or 0) or maximum != query.high:
            raise SearchMetadataError('Site ignored or changed the requested price filter')
        expected_max = query.beds if query.beds is not None and query.beds < 5 else None
        if (0 if min_beds is None else min_beds) != (query.beds or 0) or max_beds != expected_max:
            raise SearchMetadataError('Site ignored or changed the requested bedroom filter')
        return snapshot
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        raise SearchMetadataError('No verified search metadata; page may be blocked') from error


def metro_queries(cities, sources):
    regions = json.loads((Path(__file__).resolve().parents[1] / 'config/metro_regions.json').read_text())
    queries = []
    for source in sorted(set(sources), key=lambda item: item != 'domain'):
        for city in cities:
            if source == 'domain':
                queries.append(SearchQuery(source, city))
            else:
                for region in regions['cities'][city]:
                    queries.append(SearchQuery(source, city, region['name'] + ', ' + region['state'], region['atlas_id']))
    return queries

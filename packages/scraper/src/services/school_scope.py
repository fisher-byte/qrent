"""Restore original university cohorts; a metro record is not an all-campus record."""
import json
import re
from functools import lru_cache
from pathlib import Path

from ..config import TARGET_AREAS
from ..models import RegionInfo


def suburb_key(value):
    return re.sub(r'[^a-z0-9]+', ' ', (value or '').casefold()).strip()


@lru_cache(maxsize=1)
def school_areas():
    result = {}
    for school, slugs in TARGET_AREAS.items():
        result[school] = []
        for slug in slugs:
            if slug == 'sydney-city-nsw':
                name, state, postcode = 'sydney', 'NSW', '2000'
            else:
                name, state, postcode = re.fullmatch(r'(.*)-(nsw|vic)-(\d{4})', slug).groups()
                state = state.upper()
            result[school].append({'suburb': suburb_key(name), 'state': state,
                                  'postcode': postcode, 'slug': slug})
    path = Path(__file__).resolve().parents[1] / 'config/historical_victoria_regions.json'
    for school, areas in json.loads(path.read_text())['schools'].items():
        result[school] = [{**item, 'slug': item['suburb'].replace(' ', '-') + '-vic-' + item['postcode']}
                          for item in areas]
    return result


def schools_for_property(prop):
    region = RegionInfo.from_address_line2(prop.address_line2)
    if not region or region.state != prop.state:
        return []
    source = getattr(prop.source, 'value', prop.source)
    found = []
    for school, areas in school_areas().items():
        for item in areas:
            if item['state'] != region.state or item['postcode'] != str(region.postcode):
                continue
            # The original REA adapter searches a postcode, while Domain uses
            # a suburb slug. Keep those original cohorts source-aware.
            if (region.state == 'NSW' and source == 'realestate'
                    or suburb_key(region.name) == item['suburb']):
                found.append(school)
                break
    return found


def capture_areas(cities, source):
    wanted = {'NSW' if city == 'sydney' else 'VIC' for city in cities}
    selected, seen = [], set()
    for areas in school_areas().values():
        for item in areas:
            if item['state'] not in wanted:
                continue
            # Identical postcode URLs must not be fetched once per school or
            # once per suburb sharing that postcode.
            identity = (item['state'], item['postcode']) if source == 'realestate' else item['slug']
            if identity not in seen:
                seen.add(identity)
                selected.append(item['slug'])
    return selected


def schools_for_area(slug, source):
    """Attach query provenance before de-duplicating observations, as the old pipeline did."""
    def identity(value):
        if source == 'realestate':
            if value == 'sydney-city-nsw':
                return 'NSW', '2000'
            postcode = re.search(r'\d{4}', value)
            if postcode:
                state = 'VIC' if '-vic-' in value else 'NSW'
                return state, postcode.group()
        return value
    return [school for school, areas in school_areas().items()
            if any(identity(item['slug']) == identity(slug) for item in areas)]


def targets_from_capture(metadata):
    """Only persisted source identities may inherit a query's school roles."""
    captured={(str(source),str(house_id)) for source,house_id in metadata['captured_source_ids']}
    result={}
    for school,sources in metadata['school_cohorts'].items():
        if school not in school_areas():
            raise ValueError('Unknown school in capture provenance')
        for source,ids in sources.items():
            if source not in {'domain','realestate'}:
                raise ValueError('Unknown source in capture provenance')
            for house_id in ids:
                identity=(source,str(house_id))
                if identity in captured:
                    result.setdefault(identity,[]).append(school)
    return result

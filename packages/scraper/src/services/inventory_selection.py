"""Conservative cross-source groups; raw listing identities are never discarded."""
from collections import Counter, defaultdict
import re

from ..models import RegionInfo
from .commute_pool import normalized_address
from .publication import has_routable_address
from .school_scope import schools_for_property, suburb_key


def address_key(prop):
    region = RegionInfo.from_address_line2(prop.address_line2)
    if not region or region.state != prop.state or not has_routable_address(prop):
        return None
    address = normalized_address(prop.address_line1)
    if re.search(r'\b(?:bed|beds|bedroom|bedrooms|furnished|level|lvl|floor)\b', address):
        return None  # An advertised floor/bed count is not an apartment number.
    if not re.match(r'^(?:(?:unit|apartment|apt|flat)\s+)?(?:[\w-]+/)?\d[\w-]*\s+.+$', address):
        return None
    return address, suburb_key(region.name), region.state, str(region.postcode)


def select_inventory(properties, school_targets=None):
    # Repeated observations of the same source ID are not new properties.
    identities = {(prop.source.value, str(prop.house_id)): prop for prop in properties}
    def schools(prop):
        return (school_targets.get((prop.source.value, str(prop.house_id)), [])
                if school_targets is not None else schools_for_property(prop))
    selected = [prop for prop in identities.values() if schools(prop)]
    grouped, unknown = defaultdict(list), []
    for prop in selected:
        key = address_key(prop)
        (grouped[key] if key else unknown).append(prop)
    groups = []
    for address, members in sorted(grouped.items()):
        sources = Counter(prop.source.value for prop in members)
        features = {(prop.bedroom_count, prop.bathroom_count, prop.property_type) for prop in members}
        prices = {prop.price_per_week for prop in members}
        reasons = []
        if any(count > 1 for count in sources.values()):
            reasons.append('multiple_ids_in_one_source')
        if len(features) > 1:
            reasons.append('feature_conflict')
        if len(prices) > 1:
            reasons.append('price_conflict')
        # Prefer a complete description, then Domain; never copy a rating from
        # one different description into another model input.
        primary = min(members, key=lambda p: (not bool(p.description_en), p.source.value != 'domain', str(p.house_id)))
        target_schools = sorted({school for member in members for school in schools(member)})
        groups.append({'key': address, 'primary': primary, 'members': members,
                       'schools': target_schools, 'review': reasons})
    return {'input_source_ids': len(identities), 'selected_source_ids': len(selected),
            'out_of_scope_source_ids': len(identities) - len(selected), 'groups': groups,
            'unknown_addresses': unknown}

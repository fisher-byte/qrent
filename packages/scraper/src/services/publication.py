"""Validation for publishing into the existing website schema, without migrations."""
import math
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from ..models import PropertyData, RegionInfo
from ..utils import extract_price, safe_datetime


SOURCE_HOSTS = {'domain': 'domain.com.au', 'realestate': 'realestate.com.au'}


def chinese_keywords_valid(value):
    return bool(re.search(r'[\u4e00-\u9fff]',value or ''))


def ai_field_errors(prop):
    errors=[]
    if not chinese_keywords_valid(prop.description_cn):
        errors.append('description_cn')
    keywords=(prop.keywords or '').strip()
    if not keywords or re.fullmatch(r'no (?:relevant )?(?:information|keywords)(?: available)?[.!]?',keywords,re.I):
        errors.append('keywords')
    scores=prop.scores or []
    valid=len(scores)==8 and all(isinstance(x,(int,float)) and math.isfinite(x) and 0<=x<=20 for x in scores)
    if (prop.average_score is None or not math.isfinite(prop.average_score)
            or not valid or abs(prop.average_score-sum(scores)/8)>.11):
        errors.append('complete_ai_scores')
    return errors


def has_routable_address(prop):
    address = (prop.address_line1 or '').strip().replace('-', ' ')
    return bool(address and not re.search(r'address.*(?:request|withheld|undisclosed)|^contact\s+agent', address, re.I))


def source_from_url(url):
    host = (urlsplit(url or '').hostname or '').lower().removeprefix('www.')
    return next((source for source, expected in SOURCE_HOSTS.items() if host == expected), None)


def publication_errors(prop: PropertyData, university: str):
    errors = []
    if (not str(prop.house_id).isdigit() or not 0 < int(prop.house_id) <= 4294967295):
        errors.append('house_id')
    if source_from_url(prop.url) != prop.source.value or not urlsplit(prop.url).path.rstrip('/').endswith('-' + str(prop.house_id)):
        errors.append('source_url')
    if (not prop.price_text or extract_price(prop.price_text) != prop.price_per_week
            or not 100 <= prop.price_per_week <= 20000):
        errors.append('verified_weekly_price')
    region = RegionInfo.from_address_line2(prop.address_line2)
    if not has_routable_address(prop) or not region or region.state != prop.state:
        errors.append('address')
    for name in ('bedroom_count', 'bathroom_count', 'parking_count', 'property_type'):
        value = getattr(prop, name)
        if type(value) is not int or not 0 <= value <= 255:
            errors.append(name)
    description=(prop.description_en or '').strip()
    contact_only=(re.match(r'^(?:please\s+)?(?:message|contact|call|enquire|email)\b',description,re.I)
        and not re.search(r'\b(?:bed(?:room)?s?|apartment|studio|house|kitchen|bath(?:room)?s?|balcon(?:y|ies)|'
                          r'terrace|courtyard|garden|furnished|air.?conditioning|heating|wardrobe|garage|pool)\b',description,re.I))
    if not description or contact_only:
        errors.append('description_en')
    if re.match(r'^[\s*#!-]*(?:deposit\s+(?:taken|received)|leased|applications?\s+(?:approved|accepted))\b',description,re.I):
        errors.append('inactive_listing')
    errors.extend(ai_field_errors(prop))
    commute = prop.commute_times.get(university)
    if type(commute) is not int or not 0 < commute <= 1440:
        errors.append('commute_time')
    if not prop.thumbnail_url or urlsplit(prop.thumbnail_url).scheme not in {'https', 'http'}:
        errors.append('thumbnail_url')
    if len(prop.url) > 255 or len(prop.thumbnail_url or '') > 500:
        errors.append('schema_url_length')
    return errors


def mysql_datetime(value, *, captured=False):
    """Existing DATETIME values are UTC. Legacy capture files use China local time."""
    value = safe_datetime(value)
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=ZoneInfo('Asia/Shanghai') if captured else timezone.utc)
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def website_timestamp(prop):
    # The original writer used ingestion time in published_at. Preserve that
    # contract; do not alter the separately stored, unknown source timestamp.
    return (mysql_datetime(prop.published_at) or mysql_datetime(prop.scraped_at, captured=True)
            or datetime.now(timezone.utc).replace(tzinfo=None))


def mysql_availability(value):
    """The existing availability column represents an Australian calendar date."""
    value = safe_datetime(value)
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(ZoneInfo('Australia/Sydney'))
    return value.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)

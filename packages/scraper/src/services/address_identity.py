"""Stable address normalization shared by caches and provider verification."""
import re
import unicodedata


def normalized_address(value):
    value = unicodedata.normalize('NFKC', value).casefold().strip()
    value = re.sub(r'^(?:unit|apartment|apt|flat)\s+([\w-]+)\s*,?\s+(\d[\w-]*)\s+', r'\1/\2 ', value)
    value = re.sub(r'\s+', ' ', value).rstrip('.,')
    endings = {'st': 'street', 'rd': 'road', 'ave': 'avenue', 'av': 'avenue', 'pde': 'parade',
               'dr': 'drive', 'cres': 'crescent', 'ct': 'court', 'pl': 'place', 'hwy': 'highway'}
    parts = value.split()
    if parts:
        parts[-1] = endings.get(parts[-1].rstrip('.'), parts[-1])
    return ' '.join(parts)

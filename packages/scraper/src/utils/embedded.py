"""Read public server-rendered search data without executing page JavaScript."""
import json


def domain_search(soup):
    tag = soup.find('script', id='__NEXT_DATA__')
    try:
        return json.loads(tag.string)['props']['pageProps']['componentProps'] if tag else {}
    except (ValueError, TypeError, KeyError, AttributeError):
        return {}


def realestate_search(soup):
    prefix = 'window.ArgonautExchange='
    for script in soup.find_all('script'):
        text = (script.string or script.get_text()).strip()
        if not text.startswith(prefix):
            continue
        try:
            root, _ = json.JSONDecoder().raw_decode(text[len(prefix):])
            app = root.get('resi-property_listing-experience-web', {})
            cache = json.loads(app.get('urqlClientCache', '{}'))
            for entry in cache.values():
                data = json.loads(entry.get('data', '{}'))
                search = data.get('rentSearch')
                if isinstance(search, dict) and isinstance(search.get('results'), dict):
                    return search
        except (ValueError, TypeError, AttributeError):
            continue
    return {}


def residential_listings(value):
    if isinstance(value, dict):
        if value.get('__typename') == 'RentResidentialListing' and value.get('id'):
            yield value
        else:
            for child in value.values():
                yield from residential_listings(child)
    elif isinstance(value, list):
        for child in value:
            yield from residential_listings(child)

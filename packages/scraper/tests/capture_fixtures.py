"""Complete, offline Sydney capture evidence shared by daily/recovery tests."""
import json

from src.services.school_scope import capture_areas, schools_for_area


RUN = '20261031_020005_264602'
FAILED = ['maroubra-nsw-2035', 'kensington-nsw-2033', 'haymarket-nsw-2000']


def report(queries=None, failed=(), run=RUN):
    queries = queries or [(source, area) for source in ('domain', 'realestate')
                          for area in capture_areas(['sydney'], source)]
    data = {'run': run, 'status': 'partial' if failed else 'success',
            'exit_code': 1 if failed else 0, 'school_scope': True,
            'requested_cities': ['sydney'], 'max_pages_per_area': 7,
            'captured_source_ids': [], 'school_cohorts': {}, 'areas': [], 'errors': [],
            'detail_requests': 0, 'details_failed': 0, 'scored': 0,
            'traffic': {'proxy_requests': 0, 'proxy_wire_bytes': 0, 'proxy_bytes': 0}}
    for index, (source, area) in enumerate(queries):
        item = {'source': source, 'area': area, 'count': 1, 'reused': 1, 'attempts': 1}
        if source == 'domain' and area in failed:
            item.update(error='RuntimeError', count=0, attempts=2)
            data['errors'].append({'source': source, 'area': area, 'error': 'RuntimeError'})
        else:
            identity = str(100 + index)
            item['query_schools'] = schools_for_area(area, source)
            data['captured_source_ids'].append([source, identity])
            for school in item['query_schools']:
                data['school_cohorts'].setdefault(school, {}).setdefault(source, []).append(identity)
        data['areas'].append(item)
    data['listings'] = len(data['captured_source_ids'])
    if not data['listings']:
        data['status'] = 'failed'
    return data


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2))
    return path

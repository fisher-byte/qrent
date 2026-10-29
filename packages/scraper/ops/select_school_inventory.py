#!/usr/bin/env python3
"""Read captured rows without API calls; export original school cohorts and review groups."""
import argparse
from collections import Counter
from dataclasses import fields
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.models import PropertyData
from src.services.inventory_selection import select_inventory
from src.services.school_scope import schools_for_property
from src.services.publication import publication_errors


def audit(db_path, campaign, output):
    snapshot = json.loads((campaign / 'state.json').read_text())
    seen = snapshot['seen']
    names = {item.name for item in fields(PropertyData)}
    properties = []
    with sqlite3.connect(db_path.resolve().as_uri() + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        for row in db.execute('SELECT p.*,e.payload FROM properties p LEFT JOIN property_enrichment e '
                              'ON p.source=e.source AND p.house_id=e.house_id'):
            if row['source'] + ':' + row['house_id'] not in seen:
                continue
            data = {name:row[name] for name in row.keys() if name in names}
            payload = json.loads(row['payload'] or '{}')
            if payload.get('house_id',row['house_id']) != row['house_id'] or payload.get('source',row['source']) != row['source']:
                raise ValueError('Captured payload identity mismatch')
            data.update({name:value for name,value in payload.items() if name in names})
            properties.append(PropertyData.from_dict(data))
    result = select_inventory(properties)
    scoped = [p for p in properties if schools_for_property(p)]
    good = [g for g in result['groups'] if not g['review']]
    enriched = {'description_cn','keywords','complete_ai_scores','commute_time'}
    valid = [g for g in good if not any(error not in enriched for error in publication_errors(g['primary'],g['schools'][0]))]
    report = {'checked_at':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(), 'read_only':True,
        'new_api_calls':0,'production_writes':0,'campaign':campaign.name,
        'capture_complete_inventory':snapshot['complete_inventory'],
        'scope':'Original Sydney TARGET_AREAS; REA postcode queries / Domain suburb queries; historical VIC campus regions',
        'input_source_ids':result['input_source_ids'],'selected_source_ids':result['selected_source_ids'],
        'out_of_scope_source_ids':result['out_of_scope_source_ids'],
        'address_groups':len(result['groups']),'review_groups':len(result['groups'])-len(good),
        'eligible_address_groups':len(good),'unknown_address_records':len(result['unknown_addresses']),
        'valid_input_address_groups':len(valid),
        'cross_source_matched_groups':sum(len(g['members'])>1 for g in good),
        'address_school_pairs_including_review':sum(len(g['schools']) for g in result['groups']),
        'eligible_address_school_pairs':sum(len(g['schools']) for g in good),
        'valid_input_address_school_pairs':sum(len(g['schools']) for g in valid),
        'valid_input_pairs_by_state':{state:sum(len(g['schools']) for g in valid if g['primary'].state==state) for state in ['NSW','VIC']},
        'sources':dict(Counter(p.source.value for p in scoped)),
        'by_school':{school:{'source_ids':sum(school in schools_for_property(p) for p in scoped),
                            'address_groups':sum(school in g['schools'] for g in result['groups']),
                            'eligible_address_groups':sum(school in g['schools'] for g in good)}
                     for school in sorted({s for p in scoped for s in schools_for_property(p)})}}
    output.mkdir(mode=0o700,parents=True,exist_ok=True)
    groups = [{'address':g['key'],'schools':g['schools'],'review':g['review'],
               'primary':[g['primary'].source.value,g['primary'].house_id],
               'sources':[[p.source.value,p.house_id,p.url] for p in g['members']]} for g in result['groups']]
    for filename,data in [('report.json',report),('scoped-properties.json',[p.to_dict() for p in scoped]),
                          ('groups.json',groups),('unknown-addresses.json',[[p.source.value,p.house_id] for p in result['unknown_addresses']])]:
        path=output/filename;path.write_text(json.dumps(data,ensure_ascii=False,indent=2,default=str));path.chmod(0o600)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',type=Path,required=True)
    parser.add_argument('--campaign',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(audit(args.db,args.campaign,args.output),ensure_ascii=False))

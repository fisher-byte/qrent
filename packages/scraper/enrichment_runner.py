#!/usr/bin/env python3
"""Resume original AI + transit + MySQL stages from captured properties, with hard call caps.

Without --enrich this only audits; without --publish it never writes MySQL.
It never downloads listings or changes production table definitions.
"""
import argparse
import hashlib
import json
import logging
import time
from collections import Counter
from datetime import datetime, timezone
from dataclasses import replace
from pathlib import Path

from src.config import DatabaseConfig, ScoringConfig
from src.models import PropertyData
from src.services.commute_pool import CommutePool
from src.services.database import DatabaseService
from src.services.enrichment_state import APILimitReached, EnrichmentState
from src.services.publication import publication_errors, ai_field_errors
from src.services.school_scope import schools_for_property, targets_from_capture
from src.services.inventory_selection import select_inventory
from src.services.scoring import (ScoringService, SCORING_SYSTEM_PROMPT,
                                  KEYWORDS_EN_SYSTEM_PROMPT, KEYWORDS_CN_SYSTEM_PROMPT,
                                  ModelProviderUnavailable, MULTIMODAL_FLASH_MODELS)


SCHOOLS = {'NSW': ['UNSW', 'USYD', 'UTS'],
           'VIC': ['UNIMELB', 'UNIMELB_SOUTHBANK', 'MONASH', 'MONASH_CAULFIELD']}
AI_FIELDS = ('average_score', 'scores', 'keywords', 'description_cn')
ENRICHED_ERRORS = {'description_cn', 'keywords', 'complete_ai_scores', 'commute_time'}


def model_cost_cny(model, input_tokens, output_tokens):
    # Verified Beijing non-thinking list prices. Model budget is native CNY.
    rates = {'qwen-plus': (.8, 2), 'qwen-flash': (.15, 1.5)}
    rates.update({name:(.2,.8) for name in MULTIMODAL_FLASH_MODELS})
    input_limit=32000 if model in MULTIMODAL_FLASH_MODELS else 128000
    if model not in rates or input_tokens > input_limit:
        raise APILimitReached('Model pricing requires review before API use')
    input_rate, output_rate = rates[model]
    return (input_tokens * input_rate + output_tokens * output_rate) / 1000000


def complete_ai_cache(prop, value):
    return (isinstance(value,dict) and all(name in value for name in AI_FIELDS)
            and not ai_field_errors(replace(prop,**{name:value[name] for name in AI_FIELDS})))


def enrich(properties, state, *, use_api=False, max_model_calls=16, max_maps_calls=14,
           school_targets=None, commute_only=False, score_only=False, progress=None):
    config = ScoringConfig()
    def guard(system_prompt, user_prompt, output_tokens):
        input_bound = len((system_prompt + user_prompt).encode()) + 1024
        return state.reserve('model', config.model_name, max_model_calls,
                             cny_ceiling=model_cost_cny(config.model_name, input_bound, output_tokens))

    def observe(request_id, status, usage):
        cost = model_cost_cny(config.model_name, usage['input_tokens'], usage['output_tokens']) if status == 200 and usage else None
        state.finish(request_id, status, usage, actual_cny=cost)

    scorer = ScoringService(config, call_guard=guard, call_observer=observe,response_cache=state)
    maps = CommutePool(state, max_calls=max_maps_calls)
    ai_hits = 0
    records = []
    commute_required = commute_returned = blocked_inputs = 0
    score_required=score_complete=0
    model_provider_error=None
    prompt_sha = hashlib.sha256((SCORING_SYSTEM_PROMPT + KEYWORDS_EN_SYSTEM_PROMPT + KEYWORDS_CN_SYSTEM_PROMPT).encode()).hexdigest()
    try:
        for prop in properties:
            errors = []
            schools = ((school_targets or {}).get((prop.source.value, prop.house_id))
                       if school_targets is not None else schools_for_property(prop)) or []
            schools = [school for school in schools if school in SCHOOLS.get(prop.state, [])]
            # Do not spend API allowance on a record that could not be
            # published even after scoring and routes had succeeded.
            input_errors = [error for error in publication_errors(prop, schools[0] if schools else '')
                            if error not in ENRICHED_ERRORS]
            if not schools:
                input_errors.append('out_of_school_scope')
            can_enrich = bool(schools) and not input_errors
            # Cross-source reuse is valid only for exactly the same model input.
            key = state.key('original-ai-v1', prop.description_en or '', config.model_name, prompt_sha,
                            config.num_calls, config.scores_per_call, config.temperature, config.max_tokens)
            cached = state.get(key)
            cache_model=config.model_name
            if not complete_ai_cache(prop,cached):
                # Preserve completed evaluations when credentials or the model
                # change. Only full, validated results with identical inputs,
                # prompts and rating parameters qualify; retain their origin.
                for model in ('qwen-plus','qwen-flash',*sorted(MULTIMODAL_FLASH_MODELS)):
                    if model==config.model_name:continue
                    previous_key=state.key('original-ai-v1',prop.description_en or '',model,prompt_sha,
                        config.num_calls,config.scores_per_call,config.temperature,config.max_tokens)
                    previous=state.get(previous_key)
                    if complete_ai_cache(prop,previous):
                        key,cached,cache_model=previous_key,previous,model
                        break
            if cached:
                for name in AI_FIELDS:
                    setattr(prop, name, cached[name])
            elif use_api:
                # Unproven historical values cannot count as this evaluation.
                for name in AI_FIELDS:
                    setattr(prop, name, [] if name == 'scores' else None)
            complete_ai = not ai_field_errors(prop)
            if cached and complete_ai:
                ai_hits += 1
                state.put(key, cached)  # Upgrade earlier finite-TTL pilot entries.
            elif use_api and can_enrich and config.api_key and not commute_only and not model_provider_error:
                invalid=ai_field_errors(prop)
                if 'description_cn' in invalid:prop.description_cn=None
                if 'keywords' in invalid:prop.keywords=None
                if 'complete_ai_scores' in invalid:prop.average_score=None;prop.scores=[]
                try:
                    scorer.process_property(prop, force_update=False)
                except ModelProviderUnavailable as exc:
                    model_provider_error=str(exc)
                    errors.append(model_provider_error)
                except APILimitReached as exc:
                    errors.append(str(exc))
                finally:
                    # Resume completed stages if a later stage reaches its cap.
                    state.put(key, {name: getattr(prop, name) for name in AI_FIELDS})
            for school in schools:
                if use_api and can_enrich and not score_only and not model_provider_error:
                    minutes = maps.calculate(prop, school)
                    prop.commute_times.pop(school, None)
                    if minutes is not None:
                        prop.commute_times[school] = minutes
            checks = {school: publication_errors(prop, school) for school in schools}
            if can_enrich:
                score_required+=1
                score_complete+=int(not ai_field_errors(prop))
                commute_required += len(schools)
                commute_returned += sum(type(prop.commute_times.get(s)) is int and 0 < prop.commute_times[s] <= 1440 for s in schools)
            else:
                blocked_inputs += 1
            records.append({'source': prop.source.value, 'house_id': prop.house_id, 'state': prop.state,
                            'ai_cache_key': key, 'ai_model':cache_model, 'schools': schools, 'checks': checks, 'input_errors': input_errors,
                            'errors': errors})
            if progress:
                progress({'processed_addresses':len(records),'selected_addresses':len(properties),
                          'commute_required_pairs_so_far':commute_required,
                          'commute_returned_pairs':commute_returned,'blocked_input_records':blocked_inputs,
                          'maps_cache_hits':maps.cache_hits,'score_required_records':score_required,
                          'score_complete_records':score_complete,'model_call_cache_hits':scorer.cache_hits})
        return {'records': records, 'ai_cache_hits': ai_hits, 'maps_cache_hits': maps.cache_hits,
                'maps_pool_entries': len(maps.keys), 'maps_errors': maps.errors, 'api_usage': state.summary(),
                'monthly_budget': state.budget_summary(), 'commute_required_pairs':commute_required,
                'model_budget':state.model_budget_summary(),
                'provider_credit_budget':state.credit_budget_summary(),
                'score_required_records':score_required,'score_complete_records':score_complete,
                'model_call_cache_hits':scorer.cache_hits,
                'model_provider_error':model_provider_error,
                'commute_returned_pairs':commute_returned,'blocked_input_records':blocked_inputs}
    finally:
        maps.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True, help='JSON list of captured PropertyData dictionaries')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--state', type=Path, required=True, help='Persistent local cache and usage ledger')
    parser.add_argument('--limit', type=int, default=4)
    parser.add_argument('--enrich', action='store_true', help='Allow bounded model and Maps requests')
    parser.add_argument('--max-model-calls', type=int, default=16)
    parser.add_argument('--max-maps-calls', type=int, default=14)
    parser.add_argument('--publish', action='store_true', help='Back up first, then write only complete original-schema rows')
    parser.add_argument('--deduplicate', action='store_true', help='Group source IDs by full address; hold conflicts for review')
    parser.add_argument('--cohorts', type=Path, help='Capture result containing original query-to-school provenance')
    parser.add_argument('--commute-only',action='store_true',help='Never call models or publish; report transit completion separately')
    parser.add_argument('--score-only',action='store_true',help='Only complete model fields; never request routes or publish')
    parser.add_argument('--mysql-env', type=Path, default=Path(__file__).resolve().parents[2] / '.env')
    args = parser.parse_args()
    if args.limit <= 0 or min(args.max_model_calls, args.max_maps_calls) < 0:
        parser.error('limit must be positive; request caps must be nonnegative')
    if args.publish and not args.enrich:
        parser.error('--publish requires --enrich and complete cached/live results')
    if args.commute_only and (args.publish or not args.enrich):
        parser.error('--commute-only requires --enrich and cannot publish')
    if args.score_only and (args.publish or args.commute_only or not args.enrich):
        parser.error('--score-only requires --enrich and cannot publish or combine with --commute-only')
    args.output.mkdir(parents=True, exist_ok=True)
    run = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    state = EnrichmentState(args.state, run)
    try:
        raw = json.loads(args.input.read_text())
        selection, chosen, targets = None, [], None
        if args.cohorts:
            targets = targets_from_capture(json.loads(args.cohorts.read_text()))
        if args.deduplicate:
            selection = select_inventory([PropertyData.from_dict(dict(item)) for item in raw], targets)
            chosen = [group for group in selection['groups'] if not group['review']][:args.limit]
            properties = [group['primary'] for group in chosen]
            targets = {(group['primary'].source.value, group['primary'].house_id): group['schools'] for group in chosen}
        else:
            properties = [PropertyData.from_dict(dict(item)) for item in raw[:args.limit]]
        last_progress = [0.0]
        def progress(value):
            if value['processed_addresses'] != len(properties) and time.monotonic()-last_progress[0] < 10:
                return
            last_progress[0]=time.monotonic()
            value.update(run=run,updated_at=datetime.now(timezone.utc).isoformat(),
                         api_usage=state.summary(),monthly_budget=state.budget_summary(),model_budget=state.model_budget_summary())
            path=args.output/'progress.json';temp=path.with_suffix('.tmp')
            temp.write_text(json.dumps(value,ensure_ascii=False,indent=2));temp.chmod(0o600);temp.replace(path)
        result = enrich(properties, state, use_api=args.enrich, max_model_calls=args.max_model_calls,
                        max_maps_calls=args.max_maps_calls, school_targets=targets,
                        commute_only=args.commute_only,score_only=args.score_only,progress=progress)
        result.update(run=run, source_city_counts=dict(Counter(prop.source.value + '/' + prop.state for prop in properties)),
                      mysql_written=False, publication={}, production_schema_changed=False)
        ready = [(prop, [school for school in record['schools'] if not publication_errors(prop, school)])
                 for prop, record in zip(properties, result['records'])]
        result['ready_properties'] = sum(bool(schools) for _, schools in ready)
        result['complete_properties'] = sum(bool(schools) and len(schools) == len(record['schools'])
                                           for (_, schools), record in zip(ready, result['records']))
        if selection is not None:
            result['selection'] = {k:selection[k] for k in ('input_source_ids','selected_source_ids','out_of_scope_source_ids')}
            result['selection'].update(address_groups=len(selection['groups']),
                review_groups=sum(bool(g['review']) for g in selection['groups']),
                unknown_address_records=len(selection['unknown_addresses']))
            result['source_groups'] = [{'primary':[g['primary'].source.value,g['primary'].house_id],
                'schools':g['schools'],'sources':[[p.source.value,p.house_id,p.url] for p in g['members']]} for g in chosen]
        if args.publish and result['complete_properties'] == len(properties) and properties:
            from ops.backup_mysql import archive, config_from_env
            config = config_from_env(args.mysql_env)
            service = DatabaseService(DatabaseConfig(**config))
            with service.session():
                result['backup'] = archive(service.connection, args.output / ('mysql-before-' + run + '.jsonl.gz'))
                for school in [code for codes in SCHOOLS.values() for code in codes]:
                    cohort = [prop for prop, schools in ready if school in schools]
                    if cohort:
                        result['publication'][school] = service.save_properties(cohort, school)
                result['mysql_written'] = any(s['inserted'] + s['updated'] for s in result['publication'].values())
        publication_failed = any(s['errors'] or s['skipped'] for s in result['publication'].values())
        result['status'] = ('publication_failed' if publication_failed else 'published' if result['mysql_written'] else
                            'ready' if properties and result['complete_properties'] == len(properties) else 'incomplete')
        if args.commute_only:
            result['status']=('commute_complete' if result['commute_required_pairs']>0 and
                              result['commute_returned_pairs']==result['commute_required_pairs'] else 'commute_partial')
        if args.score_only:
            result['status']=('scores_complete' if result['score_required_records']>0 and
                result['score_complete_records']==result['score_required_records'] else 'scores_partial')
        if result['model_provider_error']:
            result['status']='model_provider_unavailable'
        for filename, data in [('properties.json', [prop.to_dict() for prop in properties]), ('result.json', result)]:
            path = args.output / filename
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str))
            path.chmod(0o600)
        print(json.dumps({k: v for k, v in result.items() if k != 'records'}, ensure_ascii=False, default=str))
        if result['status']=='model_provider_unavailable':
            return 3
        return 0 if result['status'] in {'published', 'ready','commute_complete','scores_complete'} else 2
    finally:
        state.close()


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    raise SystemExit(main())

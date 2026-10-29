import hashlib
import importlib
import json
from pathlib import Path
import tempfile
import unittest

from ops.enrichment_queue import cohort_subset,save_frozen
from src.services.enrichment_state import EnrichmentState


class DailyEnrichmentTests(unittest.TestCase):
    def module(self,name):
        try:return importlib.import_module(name)
        except ModuleNotFoundError:self.fail('Daily recovery interface is missing: '+name)

    def test_resume_keeps_held_and_unprocessed_addresses_but_excludes_published_ids(self):
        recover=self.module('ops.recover_enrichment_job').recover_pending
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);release=root/'release';release.mkdir()
            (release/'DEPLOYMENT.json').write_text(json.dumps({'files_sha256':{'source.py':'verified-hash'}}))
            props=[{'source':'domain','house_id':str(i),'state':'NSW'} for i in range(1,5)]
            targets={('domain',str(i)):[s] for i,s in enumerate(['UNSW','USYD','UTS','UNSW'],1)}
            save_frozen(root/'input.json',props);save_frozen(root/'cohorts.json',cohort_subset(props,targets))
            original={'release':str(release),'input':str(root/'input.json'),'cohorts':str(root/'cohorts.json'),
                'output':str(root/'old'),'source_hashes':{'old.py':'old-hash'}}
            for name in ('input','cohorts'):original[name+'_sha256']=hashlib.sha256(Path(original[name]).read_bytes()).hexdigest()
            save_frozen(root/'job.json',original)
            receipt=root/'old/batch-0001/receipt.json'
            save_frozen(receipt,{'status':'processed','published_ids':[['domain','1']],
                                  'held_ids':[['domain','2']]})
            before=receipt.read_bytes()
            result=recover(root/'job.json',root/'resume')
            self.assertEqual((result['pending_addresses'],result['excluded_published_addresses']),(3,1))
            self.assertEqual([x['house_id'] for x in json.loads((root/'resume/input.json').read_text())],['2','3','4'])
            self.assertEqual(receipt.read_bytes(),before)
            cohorts=json.loads((root/'resume/cohorts.json').read_text())
            self.assertEqual(cohorts['school_cohorts'],{'USYD':{'domain':['2']},'UTS':{'domain':['3']},'UNSW':{'domain':['4']}})
            self.assertEqual(json.loads((root/'resume/job.json').read_text())['source_hashes'],{'source.py':'verified-hash'})

    def test_only_the_reviewed_key_can_enable_models_and_a_provider_hold_still_blocks_it(self):
        authorized=self.module('ops.daily_enrich').model_authorized
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);release=root/'release';release.mkdir()
            key='offline-private-test-key'
            (release/'.env').write_text('PROPERTY_RATING_API_KEY='+key+'\nQWEN_MODEL=qwen3.7-flash-2026-07-15\n')
            with EnrichmentStateContext(root/'enrichment/state.db') as state:
                self.assertFalse(authorized(root,release))
                marker=root/'enrichment/model-investigation-cleared'
                marker.write_text(json.dumps({'key_sha256':hashlib.sha256(key.encode()).hexdigest(),
                                              'model':'qwen3.7-flash-2026-07-15'}))
                self.assertTrue(authorized(root,release))
                state.put(state.key('model-provider-hold-v1',key),{'reason':'Arrearage'})
                self.assertFalse(authorized(root,release))
                self.assertIsNotNone(state.get(state.key('model-provider-hold-v1',key)))


class EnrichmentStateContext:
    def __init__(self,path):self.path=path
    def __enter__(self):
        self.state=EnrichmentState(self.path,'offline-daily');return self.state
    def __exit__(self,*args):self.state.close()


if __name__=='__main__':unittest.main()

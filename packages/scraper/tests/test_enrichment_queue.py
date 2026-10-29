import tempfile
import unittest
import hashlib
import json
import io
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from ops.enrichment_queue import cohort_subset, ready_subset, runner_command, save_frozen, main


class EnrichmentQueueTests(unittest.TestCase):
    def test_subset_keeps_the_original_school_membership_and_rejects_melbourne(self):
        props=[{'source':'domain','house_id':'1','state':'NSW'}]
        targets={('domain','1'):['UNSW','UTS'],('domain','2'):['USYD']}
        result=cohort_subset(props,targets)
        self.assertEqual(set(result['school_cohorts']),{'UNSW','UTS'})
        self.assertEqual(result['captured_source_ids'],[['domain','1']])
        with self.assertRaises(ValueError):
            cohort_subset([{'source':'domain','house_id':'2','state':'VIC'}],targets)

    def test_only_complete_records_for_every_required_school_can_publish(self):
        props=[{'source':'domain','house_id':str(i),'state':'NSW'} for i in range(1,4)]
        result={'records':[
            {'source':'domain','house_id':'1','schools':['UNSW','UTS'],
             'input_errors':[],'checks':{'UNSW':[],'UTS':[]}},
            {'source':'domain','house_id':'2','schools':['USYD','UTS'],
             'input_errors':[],'checks':{'USYD':[],'UTS':['commute_time']}},
            {'source':'domain','house_id':'3','schools':[],
             'input_errors':[],'checks':{}}]}
        ready=ready_subset(props,result)
        self.assertEqual([p['house_id'] for p in ready],['1'])
        result['records'][0]['house_id']='99'
        with self.assertRaises(ValueError):ready_subset(props,result)

    def test_only_model_stage_has_a_positive_request_cap(self):
        for stage in ('score','commute','audit','publish'):
            cmd=runner_command(Path('/release'),Path('/official.db'),Path('/in'),
                Path('/roles'),Path('/out'),7,stage,Path('/writer.env'))
            self.assertEqual(cmd[cmd.index('--max-maps-calls')+1],'2000' if stage=='commute' else '0')
            self.assertEqual(cmd[cmd.index('--max-model-calls')+1], '35' if stage=='score' else '0')
            self.assertEqual('--score-only' in cmd,stage=='score')
            self.assertEqual('--publish' in cmd,stage=='publish')
            self.assertEqual('--commute-only' in cmd,stage=='commute')

    def test_frozen_checkpoint_refuses_different_input_instead_of_overwriting(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'input.json'
            save_frozen(path,[{'source':'domain','house_id':'1'}])
            save_frozen(path,[{'source':'domain','house_id':'1'}])
            with self.assertRaises(ValueError):save_frozen(path,[{'source':'domain','house_id':'2'}])

    def test_explicit_bonus_stage_has_no_model_or_publish_permission(self):
        try:
            cmd=runner_command(Path('/release'),Path('/official.db'),Path('/in'),
                Path('/roles'),Path('/out'),7,'bonus',Path('/writer.env'))
        except ValueError:
            self.fail('The explicitly enabled bonus transit stage is missing')
        self.assertEqual(cmd[cmd.index('--max-model-calls')+1],'0')
        self.assertEqual(cmd[cmd.index('--max-maps-calls')+1],'21')
        self.assertIn('--commute-only',cmd)
        self.assertNotIn('--publish',cmd)

    def test_cache_only_queue_publishes_complete_subset_and_restart_skips_it(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);release=Path(__file__).resolve().parents[1]
            properties=[{'source':'domain','house_id':str(i),'state':'NSW',
                'address_line1':f'{i}/1 Example Street','address_line2':'newtown-nsw-2042'} for i in (1,2)]
            targets={('domain',str(i)):['UNSW','UTS'] for i in (1,2)}
            input_path=folder/'input.json';cohorts=folder/'cohorts.json'
            save_frozen(input_path,properties);save_frozen(cohorts,cohort_subset(properties,targets))
            state=folder/'state.db'
            job={'release':str(release),'state':str(state),'source_hashes':{},
                 'input':str(input_path),'cohorts':str(cohorts),'output':str(folder/'output'),
                 'mysql_env':str(folder/'writer.env'),'batch_size':2,
                 'input_sha256':hashlib.sha256(input_path.read_bytes()).hexdigest(),
                 'cohorts_sha256':hashlib.sha256(cohorts.read_bytes()).hexdigest()}
            save_frozen(folder/'job.json',job)
            def step(job,batch,count,stage,environment,*args):
                self.assertNotEqual(stage,'score')
                if stage=='publish':return {'publication':{'UNSW':{'inserted':1,'updated':0,'errors':0}}}
                save_frozen(batch/'audit/properties.json',properties)
                return {'model_budget':{'remaining_cny':50},'records':[
                    {'source':'domain','house_id':str(i),'schools':['UNSW','UTS'],'input_errors':[],
                     'checks':{'UNSW':[],'UTS':[] if i==1 else ['commute_time']}} for i in (1,2)]}
            with patch('sys.argv',['enrichment_queue',str(folder/'job.json'),'--cache-only']), \
                 patch('ops.enrichment_queue.OFFICIAL_STATE',state.resolve()), \
                 patch('ops.enrichment_queue.run_step',side_effect=step) as run,redirect_stdout(io.StringIO()):
                main()
                self.assertEqual(run.call_count,2)
                run.reset_mock();main();run.assert_not_called()
            result=json.loads((folder/'output/result.json').read_text())
            self.assertEqual((result['published_addresses'],result['held_addresses']),(1,1))
            self.assertEqual(json.loads((folder/'output/held-input.json').read_text()),[properties[1]])

    def test_skipping_models_still_runs_free_transit_bonus_and_complete_cache_publication(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);release=Path(__file__).resolve().parents[1]
            props=[{'source':'domain','house_id':'1','state':'NSW',
                'address_line1':'1 Example Street','address_line2':'newtown-nsw-2042'}]
            targets={('domain','1'):['UNSW']}
            save_frozen(folder/'input.json',props);save_frozen(folder/'cohorts.json',cohort_subset(props,targets))
            state=folder/'state.db'
            job={'release':str(release),'state':str(state),'source_hashes':{},
                'input':str(folder/'input.json'),'cohorts':str(folder/'cohorts.json'),
                'output':str(folder/'output'),'mysql_env':str(folder/'writer.env'),'batch_size':1}
            for name in ('input','cohorts'):job[name+'_sha256']=hashlib.sha256(Path(job[name]).read_bytes()).hexdigest()
            save_frozen(folder/'job.json',job)
            stages=[]
            def step(job,batch,count,stage,environment,*args):
                stages.append(stage)
                self.assertNotEqual(stage,'score')
                if stage in ('commute','bonus'):return {'status':'commute_complete'}
                if stage=='publish':return {'publication':{'UNSW':{'inserted':1,'updated':0,'errors':0}}}
                save_frozen(batch/'audit/properties.json',props)
                return {'model_budget':{'remaining_cny':50},'records':[
                    {'source':'domain','house_id':'1','schools':['UNSW'],'input_errors':[],'checks':{'UNSW':[]}}]}
            with patch('sys.argv',['queue',str(folder/'job.json'),'--skip-model','--nsw-free','--bonus-fallback']), \
                    patch('ops.enrichment_queue.OFFICIAL_STATE',state.resolve()), \
                    patch('ops.enrichment_queue.run_step',side_effect=step),redirect_stdout(io.StringIO()):
                try:main()
                except SystemExit:self.fail('The model-free daily path is missing')
            self.assertEqual(stages,['commute','bonus','audit','publish'])
            result=json.loads((folder/'output/result.json').read_text())
            self.assertFalse(result['model_requests_allowed'])
            self.assertEqual((result['published_addresses'],result['held_addresses']),(1,0))


if __name__=='__main__':unittest.main()

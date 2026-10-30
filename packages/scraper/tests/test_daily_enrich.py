import hashlib
import importlib
import json
from pathlib import Path
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch
import subprocess
from unittest.mock import MagicMock

from ops.enrichment_queue import cohort_subset,save_frozen
from src.services.enrichment_state import EnrichmentState
from capture_fixtures import report


DAILY_RUN = '20261030_020005_158083'


def complete_capture(root):
    path=root/'output/school-daily-20261030'/(DAILY_RUN+'_result.json')
    save_frozen(path,report(run=DAILY_RUN))
    return path


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

    def test_daily_provider_exit_three_runs_the_model_free_path_and_records_the_real_partial_result(self):
        daily=self.module('ops.daily_enrich')
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);release=root/'releases/recovery-20261027';release.mkdir(parents=True)
            complete_capture(root)
            save_frozen(root/'enrichment/bootstrap-first-pass.json',{'status':'processed'})
            calls=[]
            def run(command,**kwargs):
                calls.append(command)
                if len(calls)==2:return subprocess.CompletedProcess(command,3)
                if len(calls)==3:
                    self.assertIn('--skip-model',command)
                    save_frozen(root/('output/school-enrichment-'+DAILY_RUN+'/queue/result.json'),
                        {'addresses':2,'published_addresses':1,'held_addresses':1})
                return subprocess.CompletedProcess(command,0)
            with patch('ops.daily_enrich.subprocess.run',side_effect=run), \
                    patch('ops.daily_enrich.model_authorized',return_value=True):
                result=daily.run_daily(root,datetime(2026,10,30))
            self.assertEqual(result['status'],'processed_with_held_records')
            self.assertFalse(result['models_enabled'])
            self.assertEqual((result['published_addresses'],result['held_addresses']),(1,1))
            self.assertTrue((root/('output/school-enrichment-'+DAILY_RUN+'/model-paused.json')).is_file())

    def test_daily_waits_for_actual_first_pass_before_freezing_current_published_identities(self):
        daily=self.module('ops.daily_enrich')
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);release=root/'releases/recovery-20261027';release.mkdir(parents=True)
            complete_capture(root)
            events=[]
            def wait(seconds):
                events.append('wait')
                save_frozen(root/'enrichment/bootstrap-first-pass.json',{'status':'processed'})
            def run(command,**kwargs):
                events.append('prepare' if 'prepare_school_enrichment.py' in command[1] else 'queue')
                if events[-1]=='queue':
                    save_frozen(root/('output/school-enrichment-'+DAILY_RUN+'/queue/result.json'),
                        {'addresses':2,'published_addresses':2,'held_addresses':0})
                return subprocess.CompletedProcess(command,0)
            with patch('ops.daily_enrich.time.sleep',side_effect=wait),patch('ops.daily_enrich.subprocess.run',side_effect=run),patch('ops.daily_enrich.model_authorized',return_value=False):
                daily.run_daily(root,datetime(2026,10,30))
            self.assertEqual(events,['wait','prepare','queue'])

    def test_published_priority_reads_only_property_identities_in_a_read_only_transaction(self):
        prepare=self.module('ops.prepare_school_enrichment')
        self.assertTrue(callable(getattr(prepare,'published_source_priority',None)))
        with tempfile.TemporaryDirectory() as temp:
            env=Path(temp)/'writer.env';env.write_text('DATABASE_URL=\nDB_HOST=fixture\nDB_USER=readonly\nDB_PASSWORD=offline\n')
            connection=MagicMock();cursor=connection.cursor.return_value
            cursor.fetchall.return_value=[(71,900,'https://www.realestate.com.au/property-900'),
                (18,123,'https://www.domain.com.au/example-123'),(19,124,'https://unrelated.example/124')]
            with patch('ops.prepare_school_enrichment.mysql.connector.connect',return_value=connection):
                result=prepare.published_source_priority(env)
            self.assertEqual(result,{('realestate','900'):71,('domain','123'):18})
            connection.start_transaction.assert_called_once_with(readonly=True,consistent_snapshot=True)
            self.assertEqual(cursor.execute.call_args.args,('SELECT id,house_id,url FROM properties',))
            connection.commit.assert_not_called();connection.rollback.assert_called_once();connection.close.assert_called_once()

    def test_restart_after_partial_publication_keeps_the_original_frozen_selection_and_receipts(self):
        from src.models import PropertyData,PropertySource
        from src.services.sqlite_store import SQLiteStore
        prepare=self.module('ops.prepare_school_enrichment')
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);release=root/'release';source=release/'ops/prepare_school_enrichment.py'
            source.parent.mkdir(parents=True);source.write_text('frozen fixture release')
            save_frozen(release/'DEPLOYMENT.json',{'files_sha256':{'ops/prepare_school_enrichment.py':hashlib.sha256(source.read_bytes()).hexdigest()}})
            props=[]
            for index in range(51):
                identity='123' if index==0 else str(199+index)
                props.append(PropertyData(source=PropertySource.DOMAIN,house_id=identity,state='NSW',postcode='2042',
                    address_line1=str(index+2)+'/18 Example Street',address_line2='newtown-nsw-2042',
                    bedroom_count=2,bathroom_count=1,property_type=2,price_per_week=600,price_text='$600 per week',
                    description_en='A sunny apartment with a balcony.',url='https://www.domain.com.au/example-'+identity,
                    thumbnail_url='https://cdn.example/house.jpg'))
            other=PropertyData.from_dict(props[0].to_dict());other.source=PropertySource.REALESTATE;other.house_id='900';other.url='https://www.realestate.com.au/property-900'
            props.append(other)
            store=SQLiteStore(root/'properties.db');store.save(props);store.close()
            identities=[[p.source.value,p.house_id] for p in props]
            capture=root/'output/school-daily-20261030'/(DAILY_RUN+'_result.json')
            full_report=report(run=DAILY_RUN)
            full_report.update(listings=len(identities),captured_source_ids=identities,
                school_cohorts={'UNSW':{'domain':[p.house_id for p in props if p.source==PropertySource.DOMAIN],'realestate':['900']}})
            save_frozen(capture,full_report)
            output=root/'out';env=root/'writer.env'
            priority=MagicMock(return_value={})
            with patch.object(prepare,'__file__',str(source)),patch.object(prepare,'published_source_priority',priority):
                first=prepare.prepare(root/'properties.db',capture,output,env)
                records=json.loads((output/'input.json').read_text())
                save_frozen(output/'queue/batch-0001/receipt.json',{'status':'processed',
                    'published_ids':[[p['source'],p['house_id']] for p in records[:50]],'held_ids':[]})
                before={p:p.read_bytes() for p in output.rglob('*.json')}
                priority.return_value={(p['source'],p['house_id']):71+i for i,p in enumerate(records[:50])}
                resumed=prepare.prepare(root/'properties.db',capture,output,env)
                self.assertEqual(priority.call_count,1)
                self.assertEqual(resumed['eligible_addresses'],first['eligible_addresses'])
                self.assertTrue(resumed['resumed_frozen'])
                for p,value in before.items():self.assertEqual(p.read_bytes(),value,str(p))
                with (output/'input.json').open('a') as target:target.write(' ')
                with self.assertRaises(ValueError):prepare.prepare(root/'properties.db',capture,output,env)
                self.assertEqual(priority.call_count,1)


class EnrichmentStateContext:
    def __init__(self,path):self.path=path
    def __enter__(self):
        self.state=EnrichmentState(self.path,'offline-daily');return self.state
    def __exit__(self,*args):self.state.close()


if __name__=='__main__':unittest.main()

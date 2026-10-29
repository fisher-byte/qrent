import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.services.enrichment_state import APILimitReached, EnrichmentState
from src.config import ScoringConfig
from src.models import PropertyData,PropertySource
from src.services.scoring import ScoringService
from enrichment_runner import enrich


class ModelBudgetTests(unittest.TestCase):
    def test_score_only_never_makes_route_requests(self):
        prop=PropertyData(source=PropertySource.DOMAIN,house_id='123',address_line1='1 Example Street',
            address_line2='newtown-nsw-2042',state='NSW',price_per_week=600,price_text='$600 per week',
            description_en='Actual description',url='https://www.domain.com.au/example-123',
            thumbnail_url='https://cdn.example/a.jpg')
        def score(p,**kwargs):
            p.scores=[14]*8;p.average_score=14;p.keywords='light';p.description_cn='采光'
        with patch('enrichment_runner.ScoringConfig',return_value=ScoringConfig(api_key='test')), \
             patch.object(ScoringService,'process_property',side_effect=score), \
             patch('enrichment_runner.CommutePool') as maps:
            result=enrich([prop],self.state,use_api=True,score_only=True)
        maps.return_value.calculate.assert_not_called()
        self.assertEqual(result['score_complete_records'],1)

    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.path=Path(self.folder.name)/'state.db'
        self.state=EnrichmentState(self.path,'model-native-test')

    def tearDown(self):
        self.state.close();self.folder.cleanup()

    def test_model_50_yuan_limit_is_independent_from_the_operational_dollar_limit(self):
        self.state.record_expense('operational-budget-used',4)
        self.state.reserve('model','qwen-plus',100,cny_ceiling=49.9)
        with self.assertRaises(APILimitReached):
            self.state.reserve('model','qwen-plus',100,cny_ceiling=.100001)
        self.assertEqual(self.state.model_budget_summary()['ceiling_cny'],50)
        self.assertEqual(self.state.budget_summary()['remaining_usd'],0)

    def test_environment_cannot_raise_model_budget_above_fifty_yuan(self):
        self.state.close()
        with patch.dict(os.environ,{'QRENT_MODEL_MONTHLY_CNY_LIMIT':'1000'}):
            self.state=EnrichmentState(self.path,'clamped-model-budget')
        self.assertEqual(self.state.model_budget_summary()['ceiling_cny'],50)

    def test_failed_model_call_keeps_native_reservation_after_restart(self):
        call=self.state.reserve('model','qwen-plus',100,cny_ceiling=5)
        self.state.finish(call,'ReadTimeout')
        self.state.close();self.state=EnrichmentState(self.path,'restarted')
        self.assertEqual(self.state.model_budget_summary()['spent_or_reserved_cny'],5)
        self.assertEqual(self.state.budget_summary()['spent_or_reserved_usd'],0)
        self.assertEqual(self.state.db.execute('SELECT status FROM calls WHERE id=?',(call,)).fetchone()[0],'ReadTimeout')

    def test_actual_native_cost_releases_unused_reserve_without_currency_conversion(self):
        call=self.state.reserve('model','qwen-plus',100,cny_ceiling=1)
        self.state.finish(call,200,{'input_tokens':100,'output_tokens':20},actual_cny=.23)
        self.assertEqual(self.state.model_budget_summary()['spent_or_reserved_cny'],.23)
        self.assertEqual(self.state.budget_summary()['spent_or_reserved_usd'],0)

    def test_legacy_model_reserve_migrates_once_without_erasing_the_usd_history(self):
        self.state.db.execute('INSERT INTO budget_events VALUES (?,?,?,?)',
            ('historical-model-safety-reserve-20261029',self.state.budget_period(),50000,'historical reserve, not bill'))
        self.state.db.commit();self.state.close()
        self.state=EnrichmentState(self.path,'migrated')
        self.assertEqual(self.state.model_budget_summary()['spent_or_reserved_cny'],.25)
        self.assertEqual(self.state.budget_summary()['spent_or_reserved_usd'],0)
        self.state.close();self.state=EnrichmentState(self.path,'migrated-twice')
        self.assertEqual(self.state.model_budget_summary()['spent_or_reserved_cny'],.25)
        self.assertEqual(self.state.db.execute('SELECT microusd FROM budget_events').fetchone()[0],50000)

    def test_model_reservation_rejects_mixed_currency(self):
        with self.assertRaises(ValueError):
            self.state.reserve('model','qwen-plus',100,usd_ceiling=.1)


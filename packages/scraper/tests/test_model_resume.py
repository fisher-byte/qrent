from pathlib import Path
import tempfile
import unittest
import requests
from unittest.mock import MagicMock,patch

from src.config import ScoringConfig
from src.services.enrichment_state import EnrichmentState,APILimitReached
from src.services.scoring import ScoringService


class ModelResumeTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.state=EnrichmentState(Path(self.folder.name)/'state.db','first')

    def tearDown(self):
        self.state.close();self.folder.cleanup()

    def service(self,limit):
        return ScoringService(ScoringConfig(api_key='test'),
            call_guard=lambda *args:self.state.reserve('model','qwen-plus',limit,cny_ceiling=.1),
            call_observer=self.state.finish,response_cache=self.state)

    def test_resume_reuses_finished_evaluation_while_still_producing_eight_scores(self):
        response=MagicMock(status_code=200,usage={'input_tokens':100,'output_tokens':20})
        response.output.choices=[{'message':{'content':'\n'.join(['总评分：14']*4)}}]
        with patch('src.services.scoring.dashscope.Generation.call',return_value=response) as call, \
                patch('src.services.scoring.time.sleep'):
            with self.assertRaises(APILimitReached):self.service(1).score_property('Sunny apartment.')
            self.state.run_id='resume'
            average,scores=self.service(10).score_property('Sunny apartment.')
        self.assertEqual((average,scores),(14,[14]*8))
        self.assertEqual(call.call_count,2)

    def test_uncertain_request_is_preserved_and_not_automatically_repeated(self):
        with patch('src.services.scoring.dashscope.Generation.call',side_effect=TimeoutError('network')) as call, \
                patch('src.services.scoring.time.sleep'):
            with self.assertRaises(APILimitReached):self.service(10)._call_model('system','description')
            with self.assertRaises(APILimitReached):self.service(10)._call_model('system','description')
        self.assertEqual(call.call_count,1)
        self.assertEqual(self.state.db.execute('SELECT COUNT(*) FROM calls').fetchone()[0],1)

    def test_sdk_transport_does_not_resend_an_uncertain_post(self):
        with patch('requests.sessions.Session.send',
                   side_effect=requests.exceptions.ConnectionError('connection dropped')) as send:
            with self.assertRaises(APILimitReached):self.service(10)._call_model('system','unique SDK transport test')
        self.assertEqual(send.call_count,1)
        self.assertEqual(self.state.db.execute('SELECT COUNT(*) FROM calls').fetchone()[0],1)

    def test_provider_rejection_blocks_different_prompts_and_models_after_restart(self):
        response=MagicMock(status_code=400,usage={},code='Arrearage')
        path=Path(self.folder.name)/'state.db'
        with patch('src.services.scoring.dashscope.Generation.call',return_value=response) as call:
            with self.assertRaises(APILimitReached):self.service(10)._call_model('system','first listing')
            self.state.close()
            self.state=EnrichmentState(path,'restart')
            other=ScoringService(ScoringConfig(api_key='test',model_name='qwen-flash'),
                call_guard=lambda *args:self.state.reserve('model','qwen-flash',10,cny_ceiling=.1),
                call_observer=self.state.finish,response_cache=self.state)
            with self.assertRaises(APILimitReached):other._call_model('system','different listing')
        self.assertEqual(call.call_count,1)
        self.assertEqual(self.state.db.execute('SELECT status FROM calls').fetchall(),[('400',)])
        self.assertEqual(self.state.model_budget_summary()['spent_or_reserved_cny'],.1)

    def test_provider_hold_still_allows_successful_cached_responses(self):
        success=MagicMock(status_code=200,usage={'input_tokens':100,'output_tokens':20})
        success.output.choices=[{'message':{'content':'verified previous result'}}]
        rejected=MagicMock(status_code=400,usage={},code='Arrearage')
        with patch('src.services.scoring.dashscope.Generation.call',side_effect=[success,rejected]) as call:
            self.assertEqual(self.service(10)._call_model('system','completed listing'),'verified previous result')
            with self.assertRaises(APILimitReached):self.service(10)._call_model('system','new listing')
            self.assertEqual(self.service(10)._call_model('system','completed listing'),'verified previous result')
        self.assertEqual(call.call_count,2)

    def test_wrong_language_keyword_response_gets_one_cached_repair(self):
        def response(text):
            result=MagicMock(status_code=200,usage={'input_tokens':100,'output_tokens':20})
            result.output.choices=[{'message':{'content':text}}]
            return result
        service=self.service(10)
        with patch('src.services.scoring.dashscope.Generation.call',
                   side_effect=[response('private terrace, modern kitchen'),response('独立露台，现代厨房')]) as call:
            self.assertEqual(service.extract_keywords_cn('A private terrace and a modern kitchen.'),'独立露台，现代厨房')
            self.assertEqual(service.extract_keywords_cn('A private terrace and a modern kitchen.'),'独立露台，现代厨房')
        self.assertEqual(call.call_count,2)


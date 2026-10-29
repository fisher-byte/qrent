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

    def test_only_missing_score_groups_are_filled_once_and_original_groups_survive_restart(self):
        def response(scores):
            value=MagicMock(status_code=200,usage={'input_tokens':100,'output_tokens':30})
            value.output.choices=[{'message':{'content':'\n'.join('总评分:'+str(x) for x in scores)}}]
            return value
        wires=[]
        replies=iter([response([14]),response([15,15,15]),response([16]*4)])
        def send(**kwargs):wires.append(kwargs);return next(replies)
        with patch('src.services.scoring.dashscope.Generation.call',side_effect=send),patch('src.services.scoring.time.sleep'):
            average,scores=self.service(10).score_property('Original sunny apartment description.')
            self.state.run_id='resumed-after-fill'
            repeated=self.service(10).score_property('Original sunny apartment description.')
        self.assertEqual(scores,[14,15,15,15,16,16,16,16])
        self.assertEqual((average,scores),repeated)
        self.assertEqual(len(wires),3)
        self.assertIn('3组',wires[1]['messages'][0]['content'])
        self.assertIn('不重新输出',wires[1]['messages'][1]['content'])

    def test_wrong_number_of_fill_groups_stays_incomplete_without_repeating_the_paid_response(self):
        def response(count):
            value=MagicMock(status_code=200,usage={})
            value.output.choices=[{'message':{'content':'\n'.join(['总评分:14']*count)}}]
            return value
        with patch('src.services.scoring.dashscope.Generation.call',side_effect=[response(1),response(2),response(4)]) as call,patch('src.services.scoring.time.sleep'):
            self.assertEqual(self.service(10).score_property('Partial response with wrong fill count.'),(None,[]))
            self.assertEqual(self.service(10).score_property('Partial response with wrong fill count.'),(None,[]))
        self.assertEqual(call.call_count,3)

    def test_chinese_fallback_translates_existing_english_keywords_and_preserves_scores(self):
        from src.models import PropertyData,PropertySource
        def response(text):
            value=MagicMock(status_code=200,usage={'input_tokens':100,'output_tokens':20})
            value.output.choices=[{'message':{'content':text}}]
            return value
        prop=PropertyData(source=PropertySource.DOMAIN,house_id='123',description_en='A sunny apartment with a balcony.',
            average_score=14,scores=[14]*8,keywords='sunny balcony, built-in wardrobe')
        with patch('src.services.scoring.dashscope.Generation.call',side_effect=[
                response('sunny balcony, built-in wardrobe'),response('sunny balcony'),response('阳光阳台，内置衣柜')]) as call:
            self.service(10).process_property(prop)
            self.service(10).process_property(prop)
        self.assertEqual((prop.average_score,prop.scores,prop.keywords),(14,[14]*8,'sunny balcony, built-in wardrobe'))
        self.assertEqual(prop.description_cn,'阳光阳台，内置衣柜')
        self.assertEqual(call.call_count,3)
        self.assertIn('sunny balcony, built-in wardrobe',call.call_args.kwargs['messages'][1]['content'])


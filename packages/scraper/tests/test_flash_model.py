import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import requests

from enrichment_runner import enrich, model_cost_cny
from src.config import ScoringConfig
from src.models import PropertyData, PropertySource
from src.services.enrichment_state import APILimitReached, EnrichmentState
from src.services.scoring import ScoringService, KEYWORDS_CN_SYSTEM_PROMPT
from src.services.publication import chinese_keywords_valid


PINNED_FLASH='qwen3.7-flash-2026-07-15'


def api_response(request, content):
    response=requests.Response()
    response.status_code=200
    response.headers['Content-Type']='application/json'
    response.request=request
    response.url=request.url
    response._content=json.dumps({'request_id':'offline-fixture','output':{'choices':[
        {'finish_reason':'stop','message':{'role':'assistant','content':content}}]},
        'usage':{'input_tokens':100,'output_tokens':20}}).encode()
    return response


class FlashModelTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.state=EnrichmentState(Path(self.folder.name)/'state.db','flash-validation')

    def tearDown(self):
        self.state.close();self.folder.cleanup()

    def test_pinned_flash_uses_multimodal_endpoint_text_only_without_thinking_or_search(self):
        wires=[]
        def send(session, request, **kwargs):
            wires.append((request.url,json.loads(request.body),dict(request.headers)))
            return api_response(request,[{'text':'\n'.join(['总评分：14']*4)}])
        service=ScoringService(ScoringConfig(api_key='test',model_name=PINNED_FLASH),
            call_guard=lambda *args:self.state.reserve('model',PINNED_FLASH,4,cny_ceiling=.01),
            call_observer=self.state.finish,response_cache=self.state)
        with patch('requests.sessions.Session.send',autospec=True,side_effect=send), \
                patch('src.services.scoring.time.sleep'):
            average,scores=service.score_property('Sunny apartment with a balcony.')
        self.assertEqual((average,scores),(14,[14]*8))
        self.assertEqual(len(wires),2)
        for url,body,headers in wires:
            self.assertTrue(url.endswith('/services/aigc/multimodal-generation/generation'))
            self.assertEqual(body['model'],PINNED_FLASH)
            self.assertEqual(body['parameters']['enable_thinking'],False)
            self.assertEqual(body['parameters']['enable_search'],False)
            self.assertEqual(body['parameters']['max_tokens'],150)
            self.assertEqual(headers['Authorization'],'Bearer test')
            self.assertTrue(all(set(part)=={'text'} for m in body['input']['messages'] for part in m['content']))
        self.assertEqual(self.state.db.execute('SELECT status,input_tokens,output_tokens FROM calls').fetchall(),
                         [('200',100,20),('200',100,20)])

    def test_pinned_flash_price_does_not_silently_enter_a_higher_input_tier(self):
        self.assertAlmostEqual(model_cost_cny(PINNED_FLASH,1000,150),.00032)
        with self.assertRaises(APILimitReached):model_cost_cny(PINNED_FLASH,32001,150)

    def test_an_english_feature_list_with_one_chinese_suffix_is_not_chinese_keywords(self):
        self.assertFalse(chinese_keywords_valid('23层, park views, large balcony, open kitchen, dishwasher, secure parking'))
        self.assertTrue(chinese_keywords_valid('采光充足, 大阳台, Miele厨电, 安全停车位'))

    def test_chinese_repair_reuses_scores_english_and_the_existing_original_response(self):
        service=ScoringService(ScoringConfig(api_key='test',model_name=PINNED_FLASH),
            call_guard=lambda *args:self.state.reserve('model',PINNED_FLASH,5,cny_ceiling=.01),
            call_observer=self.state.finish,response_cache=self.state)
        prop=PropertyData(house_id='123',source=PropertySource.DOMAIN,
            description_en='An apartment on level 23 with park views, balcony and Miele appliances.',
            average_score=14,scores=[14]*8,keywords='park views, balcony, Miele appliances')
        replies=iter(['23层, park views, balcony, open kitchen, dishwasher, secure parking',
                      '23层公寓, 公园景观, 阳台, Miele厨电'])
        sent=[]
        def send(session,request,**kwargs):
            sent.append(json.loads(request.body))
            return api_response(request,[{'text':next(replies)}])
        with patch('requests.sessions.Session.send',autospec=True,side_effect=send),patch('src.services.scoring.time.sleep'):
            service._call_model(KEYWORDS_CN_SYSTEM_PROMPT,prop.description_en)
            service.process_property(prop)
        self.assertEqual(prop.description_cn,'23层公寓, 公园景观, 阳台, Miele厨电')
        self.assertEqual((prop.average_score,prop.scores,prop.keywords),(14,[14]*8,'park views, balcony, Miele appliances'))
        self.assertEqual(len(sent),2)
        self.assertEqual(self.state.db.execute('SELECT COUNT(*) FROM calls').fetchone()[0],2)

    def test_completed_previous_model_fields_are_reused_without_a_new_request(self):
        prop=PropertyData(house_id='123',source=PropertySource.DOMAIN,
            address_line1='1 Example Street',address_line2='newtown-nsw-2042',state='NSW',postcode='2042',
            price_per_week=600,price_text='$600 per week',description_en='A sunny apartment with a balcony.',
            url='https://www.domain.com.au/sydney-123',thumbnail_url='https://cdn.example/property.jpg')
        results=iter(['\n'.join(['总评分：14']*4),'\n'.join(['总评分：14']*4),
                      'natural light, balcony','采光充足，阳台'])
        def send(session,request,**kwargs):return api_response(request,next(results))
        with patch('enrichment_runner.ScoringConfig',return_value=ScoringConfig(api_key='old-test-key')), \
                patch('requests.sessions.Session.send',autospec=True,side_effect=send), \
                patch('src.services.scoring.time.sleep'):
            first=enrich([prop],self.state,use_api=True,score_only=True,max_maps_calls=0)
        self.assertEqual(first['score_complete_records'],1)
        prop.average_score=None;prop.scores=[];prop.keywords=None;prop.description_cn=None
        with patch('enrichment_runner.ScoringConfig',
                   return_value=ScoringConfig(api_key='new-test-key',model_name=PINNED_FLASH)), \
                patch('requests.sessions.Session.send',side_effect=AssertionError('No new HTTP allowed')):
            second=enrich([prop],self.state,use_api=True,score_only=True,max_maps_calls=0)
        self.assertEqual(second['score_complete_records'],1)
        self.assertEqual((prop.average_score,prop.scores,prop.description_cn),(14,[14]*8,'采光充足，阳台'))
        self.assertEqual(second['records'][0]['ai_cache_key'],first['records'][0]['ai_cache_key'])
        self.assertEqual(second['records'][0]['ai_model'],'qwen-plus')
        self.assertEqual(self.state.db.execute('SELECT COUNT(*) FROM calls').fetchone()[0],4)


if __name__=='__main__':unittest.main()

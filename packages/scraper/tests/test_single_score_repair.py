import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import requests

from src.config import ScoringConfig
from src.services.enrichment_state import APILimitReached, EnrichmentState
from src.services.scoring import ScoringService


MODEL='qwen3.7-flash-2026-07-15'


class SingleScoreRepairTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.state=EnrichmentState(Path(self.folder.name)/'state.db','initial')

    def tearDown(self):
        self.state.close();self.folder.cleanup()

    def scorer(self,limit):
        return ScoringService(ScoringConfig(api_key='offline-fixture-key',model_name=MODEL),
            call_guard=lambda *args:self.state.reserve('model',MODEL,limit,cny_ceiling=.01),
            call_observer=self.state.finish,response_cache=self.state)

    @staticmethod
    def transport(replies):
        remaining=iter(replies)
        def send(session,request,**kwargs):
            response=requests.Response();response.status_code=200
            response.headers['Content-Type']='application/json'
            response.request=request;response.url=request.url
            response._content=json.dumps({'output':{'choices':[{'finish_reason':'stop',
                'message':{'role':'assistant','content':[{'text':next(remaining)}]}}]},
                'usage':{'input_tokens':100,'output_tokens':20}}).encode()
            return response
        return send

    def test_partial_multi_group_repair_preserves_both_received_groups_and_replays_without_http(self):
        replies=['总评分:14','总评分:16','总评分:18','总评分:12',
                 '总评分:16\n总评分:14\n总评分:12\n总评分:18']
        with patch('requests.sessions.Session.send',autospec=True,side_effect=self.transport(replies)), \
                patch('src.services.scoring.time.sleep'):
            actual=self.scorer(5).score_property('Same bright apartment with an equipped kitchen.')
        self.assertEqual(actual,(15,[14,16,18,12,16,14,12,18]))
        self.assertEqual(self.state.db.execute('SELECT COUNT(*) FROM calls').fetchone()[0],5)
        self.state.run_id='replay'
        with patch('requests.sessions.Session.send',side_effect=AssertionError('Cached scores must not call HTTP')), \
                patch('src.services.scoring.time.sleep'):
            self.assertEqual(self.scorer(0).score_property('Same bright apartment with an equipped kitchen.'),actual)
        self.assertEqual(self.state.db.execute('SELECT COUNT(*) FROM calls').fetchone()[0],5)

    def test_a_budget_stop_mid_repair_resumes_only_the_two_missing_requests(self):
        description='Same apartment after a bounded first attempt.'
        with patch('requests.sessions.Session.send',autospec=True,
                   side_effect=self.transport(['总评分:14','总评分:16','总评分:18'])), \
                patch('src.services.scoring.time.sleep'):
            with self.assertRaises(APILimitReached):self.scorer(3).score_property(description)
        self.assertEqual(self.state.db.execute('SELECT COUNT(*) FROM calls').fetchone()[0],3)
        self.state.run_id='resume'
        with patch('requests.sessions.Session.send',autospec=True,
                   side_effect=self.transport(['总评分:12','总评分:16\n总评分:14\n总评分:12\n总评分:18'])), \
                patch('src.services.scoring.time.sleep'):
            self.assertEqual(self.scorer(2).score_property(description),(15,[14,16,18,12,16,14,12,18]))
        self.assertEqual(self.state.db.execute('SELECT status FROM calls').fetchall(),[('200',)]*5)

    def test_an_invalid_single_group_stops_the_repair_and_keeps_the_record_incomplete(self):
        replies=['总评分:14','总评分:16','No score supplied.',
                 '总评分:16\n总评分:14\n总评分:12\n总评分:18']
        with patch('requests.sessions.Session.send',autospec=True,side_effect=self.transport(replies)), \
                patch('src.services.scoring.time.sleep'):
            self.assertEqual(self.scorer(4).score_property('Apartment with one unscored group.'),(None,[]))
        self.assertEqual(self.state.db.execute('SELECT COUNT(*) FROM calls').fetchone()[0],4)


if __name__=='__main__':unittest.main()

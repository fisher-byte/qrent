from pathlib import Path
import tempfile
import unittest
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
            self.assertIsNone(self.service(10)._call_model('system','description'))
            with self.assertRaises(APILimitReached):self.service(10)._call_model('system','description')
        self.assertEqual(call.call_count,1)
        self.assertEqual(self.state.db.execute('SELECT COUNT(*) FROM calls').fetchone()[0],1)


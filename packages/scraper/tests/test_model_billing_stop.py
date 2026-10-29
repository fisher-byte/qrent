import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

from enrichment_runner import main
from src.config import ScoringConfig
from src.models import PropertyData, PropertySource


class ModelBillingStopTests(unittest.TestCase):
    def test_first_billing_rejection_stops_batch_and_preserves_every_input(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)
            props=[PropertyData(house_id=str(i),source=PropertySource.DOMAIN,
                address_line1=f'{i} Example Street',address_line2='newtown-nsw-2042',
                state='NSW',postcode='2042',price_per_week=600,price_text='$600 per week',
                description_en=f'A sunny apartment with a balcony. Listing {i}.',
                thumbnail_url='https://cdn.example/property.jpg',
                url=f'https://www.domain.com.au/sydney-{i}') for i in (1,2,3)]
            source=folder/'input.json';source.write_text(json.dumps([p.to_dict() for p in props],default=str))
            output=folder/'out';state=folder/'state.db'
            argv=['enrichment_runner','--input',str(source),'--output',str(output),
                  '--state',str(state),'--enrich','--score-only','--limit','3',
                  '--max-model-calls','15','--max-maps-calls','0']
            response=MagicMock(status_code=400,usage={},code='Arrearage')
            with patch('sys.argv',argv),patch('enrichment_runner.ScoringConfig',
                    return_value=ScoringConfig(api_key='test')), \
                    patch('src.services.scoring.dashscope.Generation.call',return_value=response) as call, \
                    patch('src.services.scoring.time.sleep'),redirect_stdout(io.StringIO()):
                exit_code=main()
            self.assertEqual(call.call_count,1)
            self.assertEqual(exit_code,3)  # Queue accepts ordinary partial=2; billing failures must stop it.
            result=json.loads((output/'result.json').read_text())
            self.assertEqual(result['status'],'model_provider_unavailable')
            self.assertEqual(result['score_complete_records'],0)
            self.assertEqual(len(result['records']),3)
            self.assertEqual(len(json.loads((output/'properties.json').read_text())),3)
            with sqlite3.connect(state) as db:
                self.assertEqual(db.execute('SELECT status FROM calls').fetchall(),[('400',)])
                self.assertGreater(db.execute('SELECT SUM(microcny) FROM model_budget_events').fetchone()[0],0)


if __name__=='__main__':unittest.main()

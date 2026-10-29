import json
from pathlib import Path
import tempfile
import unittest

from test_recovery import listing
from src.models import PropertySource
from src.services.sqlite_store import SQLiteStore
from ops.select_school_inventory import audit


class SchoolInventoryTests(unittest.TestCase):
    def test_capture_provenance_keeps_adjacent_area_and_separates_equal_source_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);store=SQLiteStore(root/'properties.db')
            store.save([listing(state='NSW',postcode='2015',suburb='Alexandria',address_line2='alexandria-nsw-2015'),
                        listing(PropertySource.REALESTATE)])
            store.close()
            capture=root/'capture-result.json'
            capture.write_text(json.dumps({'status':'success','captured_source_ids':[['domain','123']],
                'school_cohorts':{'UNSW':{'domain':['123']}}}))
            report=audit(root/'properties.db',root,root/'selected',capture_result=capture)
            self.assertEqual(report['scope_basis'],'query_provenance')
            self.assertEqual(report['input_source_ids'],1)
            self.assertEqual(report['selected_source_ids'],1)
            self.assertEqual(report['by_school']['UNSW']['source_ids'],1)
            self.assertFalse(report['capture_complete_inventory'])
            self.assertTrue(report['capture_window_complete'])


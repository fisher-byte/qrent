import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from ops import apply_release


class DeploymentEnvironmentTests(unittest.TestCase):
    def test_legacy_dotenv_import_cannot_restore_live_keys_or_change_the_test_model(self):
        self.assertTrue(callable(getattr(apply_release,'offline_test_environment',None)))
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)
            (folder/'.env').write_text('QWEN_MODEL=qwen3.7-flash-2026-07-15\n'
                'PROPERTY_RATING_API_KEY=live-placeholder\nDISTANCE_MATRIX_API_KEY=live-gift-placeholder\n'
                'CAMOUFOX_ASSET_PREFLIGHT=true\n')
            environment=apply_release.offline_test_environment(folder/'offline.env')
            child=subprocess.run([sys.executable,'-c',
                'import os,json; from dotenv import load_dotenv; load_dotenv(".env"); '
                'print(json.dumps({k:os.getenv(k) for k in ["QWEN_MODEL","PROPERTY_RATING_API_KEY","DISTANCE_MATRIX_API_KEY","CAMOUFOX_ASSET_PREFLIGHT"]}))'],
                cwd=folder,env=environment,capture_output=True,text=True,check=True)
            self.assertEqual(json.loads(child.stdout),{'QWEN_MODEL':'qwen-plus',
                'PROPERTY_RATING_API_KEY':'','DISTANCE_MATRIX_API_KEY':'','CAMOUFOX_ASSET_PREFLIGHT':'false'})


if __name__=='__main__':unittest.main()

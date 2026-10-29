from contextlib import contextmanager
import importlib
import io
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

import requests


class GeoIPAssetTests(unittest.TestCase):
    def helper(self):
        try:return importlib.import_module('src.utils.geoip_asset').ensure_geoip_asset
        except ModuleNotFoundError:self.fail('Bounded ordinary-egress GeoIP preparation is missing')

    def geo(self,path,needs_update):
        @contextmanager
        def reader(filename):
            if Path(filename).read_bytes() not in (b'old-valid-db',b'new-valid-db'):
                raise ValueError('Invalid database')
            yield SimpleNamespace(metadata=lambda:SimpleNamespace(node_count=1,build_epoch=1780000000))
        return SimpleNamespace(load_geoip_config=lambda:{'extract':True,'urls':{'combined':[
            'https://github.com/daijro/geoip-all-in-one/releases/latest/download/geoip-aio-all.mmdb.zip']}},
            get_mmdb_path=lambda version,config:path,needs_update=lambda config:needs_update,
            maxminddb=SimpleNamespace(open_database=reader))

    def response(self):
        data=io.BytesIO()
        with zipfile.ZipFile(data,'w') as archive:archive.writestr('data/geo.mmdb',b'new-valid-db')
        response=requests.Response();response.status_code=200
        response._content=data.getvalue();response._content_consumed=True
        response.headers['Content-Length']=str(len(response._content))
        return response

    def test_existing_fresh_database_needs_no_network(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'geo.mmdb';path.write_bytes(b'old-valid-db')
            with patch('requests.Session.get',side_effect=AssertionError('No network allowed')):
                result=self.helper()(self.geo(path,False))
            self.assertEqual(result['status'],'cached')
            self.assertEqual(path.read_bytes(),b'old-valid-db')

    def test_refresh_uses_the_ordinary_proxy_and_atomically_installs_only_the_database(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'geo.mmdb';seen=[]
            def send(session,url,**kwargs):
                seen.append((session.trust_env,dict(session.proxies),kwargs))
                return self.response()
            with patch('requests.Session.get',autospec=True,side_effect=send):
                result=self.helper()(self.geo(path,True))
            self.assertEqual(path.read_bytes(),b'new-valid-db')
            self.assertEqual(result['status'],'updated')
            self.assertEqual(len(seen),1)
            self.assertFalse(seen[0][0])
            self.assertEqual(seen[0][1],{'http':'socks5h://127.0.0.1:18082','https':'socks5h://127.0.0.1:18082'})
            self.assertEqual(seen[0][2]['timeout'],(15,45))
            self.assertEqual(sorted(x.name for x in Path(temp).iterdir()),['geo.mmdb'])

    def test_failed_refresh_retains_a_valid_existing_database(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'geo.mmdb';path.write_bytes(b'old-valid-db')
            with patch('requests.Session.get',side_effect=requests.ReadTimeout):
                result=self.helper()(self.geo(path,True))
            self.assertEqual(result['status'],'cached_after_refresh_failure')
            self.assertEqual(path.read_bytes(),b'old-valid-db')

    def test_missing_database_failure_does_not_repeat_for_each_region(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'geo.mmdb';seen=[]
            def fail(*args,**kwargs):seen.append(1);raise requests.ReadTimeout()
            with patch('requests.Session.get',side_effect=fail):
                for _ in range(2):
                    with self.assertRaises(RuntimeError):self.helper()(self.geo(path,True))
            self.assertEqual(len(seen),1)
            self.assertFalse(path.exists())

    def test_deadline_interrupts_a_stream_before_its_next_chunk_and_retains_old_data(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'geo.mmdb';path.write_bytes(b'old-valid-db')
            response=self.response()
            def slow_chunks(size):
                yield b'abcd'
                time.sleep(.4)
                yield b'late'
            response.iter_content=slow_chunks
            started=time.monotonic()
            with patch('requests.Session.get',return_value=response):
                result=self.helper()(self.geo(path,True),max_seconds=.03)
            self.assertLess(time.monotonic()-started,.25)
            self.assertEqual(result['status'],'cached_after_refresh_failure')
            self.assertEqual(result['download_bytes'],4)
            self.assertEqual(path.read_bytes(),b'old-valid-db')
            self.assertFalse(any(Path(temp).glob('.geoip-fetch-*')))


if __name__=='__main__':unittest.main()

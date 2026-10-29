"""Bound Camoufox's optional data download to ordinary egress, never residential traffic."""
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
from urllib.parse import urlsplit
import zipfile

import requests


def ensure_geoip_asset(geo,*,proxy='socks5h://127.0.0.1:18082',ip=None):
    if proxy!='socks5h://127.0.0.1:18082':
        raise ValueError('Use the verified ordinary GeoIP asset egress')
    config=geo.load_geoip_config()
    version='ipv6' if ip and ':' in ip else 'ipv4'
    path=Path(geo.get_mmdb_path(version,config))
    path.parent.mkdir(parents=True,exist_ok=True)
    def metadata(candidate):
        try:
            with geo.maxminddb.open_database(str(candidate)) as reader:
                value=reader.metadata()
                return value if value.node_count>0 and value.build_epoch>1500000000 else None
        except Exception:return None
    existing=metadata(path)
    if existing and not geo.needs_update(config):
        return {'status':'cached','build_epoch':existing.build_epoch,'download_bytes':0}
    marker=path.with_name(path.name+'.download-failed.json')
    try:recent=time.time()-json.loads(marker.read_text())['at']<3600
    except (OSError,ValueError,KeyError,TypeError):recent=False
    if recent:
        if existing:
            os.utime(path,None)
            return {'status':'cached_after_refresh_failure','build_epoch':existing.build_epoch,'download_bytes':0}
        raise RuntimeError('GeoIP asset unavailable; previous bounded attempt retained')
    urls=config['urls']['combined' if 'combined' in config['urls'] else version]
    urls=[urls] if isinstance(urls,str) else urls
    # A single bounded source attempt per capture; the next day may try again.
    url=urls[0]
    if urlsplit(url).scheme!='https':raise ValueError('GeoIP assets require HTTPS')
    try:
        downloaded=0;started=time.monotonic()
        with tempfile.TemporaryDirectory(prefix='.geoip-fetch-',dir=path.parent) as temp:
            archive=Path(temp)/'download';candidate=Path(temp)/'new.mmdb'
            with requests.Session() as http:
                http.trust_env=False
                http.proxies.update({'http':proxy,'https':proxy})
                with http.get(url,stream=True,timeout=(15,45)) as response:
                    response.raise_for_status()
                    if int(response.headers.get('Content-Length',0))>128*1024*1024:
                        raise RuntimeError('GeoIP download exceeds its size bound')
                    with archive.open('wb') as sink:
                        for part in response.iter_content(1024*1024):
                            downloaded+=len(part)
                            if downloaded>128*1024*1024 or time.monotonic()-started>240:
                                raise RuntimeError('GeoIP download bound reached')
                            sink.write(part)
            if config.get('extract'):
                with zipfile.ZipFile(archive) as package:
                    members=[x for x in package.infolist() if x.filename.endswith('.mmdb')]
                    if len(members)!=1 or members[0].file_size>512*1024*1024:
                        raise RuntimeError('Invalid GeoIP asset archive')
                    with package.open(members[0]) as source,candidate.open('wb') as sink:
                        shutil.copyfileobj(source,sink,1024*1024)
            else:archive.replace(candidate)
            checked=metadata(candidate)
            if not checked:raise RuntimeError('Invalid GeoIP database')
            candidate.chmod(0o600);candidate.replace(path)
        return {'status':'updated','build_epoch':checked.build_epoch,'download_bytes':downloaded}
    except Exception as exc:
        marker.write_text(json.dumps({'at':time.time(),'reason':type(exc).__name__}));marker.chmod(0o600)
        if existing:
            # Camoufox defines mtime as the last refresh check, not build time.
            # Preserve the real build epoch and usable data after an outage.
            os.utime(path,None)
            return {'status':'cached_after_refresh_failure','build_epoch':existing.build_epoch,'download_bytes':0}
        raise RuntimeError('GeoIP asset unavailable after a bounded ordinary-egress attempt') from None

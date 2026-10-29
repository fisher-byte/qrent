"""Local cache and conservative call ledger. Contains no provider credentials."""
import hashlib
import json
import sqlite3
import time
import os
import fcntl
from decimal import Decimal, ROUND_CEILING
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


class APILimitReached(RuntimeError):
    pass


class EnrichmentState:
    def __init__(self, path, run_id):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = os.open(str(path) + '.lock', os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(self.lock)
            raise APILimitReached('Another enrichment process owns this cache; duplicate run refused')
        self.db = sqlite3.connect(path)
        path.chmod(0o600)
        self.run_id = run_id
        self.cap_microusd = min(4_000_000, self.microusd(os.getenv('QRENT_MONTHLY_USD_LIMIT', '4')))
        self.model_cap_microcny = max(0,min(50_000_000,self.microusd(os.getenv('QRENT_MODEL_MONTHLY_CNY_LIMIT','50'))))
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, value TEXT NOT NULL, expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS calls (id INTEGER PRIMARY KEY, run TEXT NOT NULL, service TEXT NOT NULL,
                quota_group TEXT NOT NULL, period TEXT NOT NULL, status TEXT NOT NULL,
                input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS budget_events (event TEXT PRIMARY KEY, period TEXT NOT NULL,
                microusd INTEGER NOT NULL, note TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS model_budget_events (event TEXT PRIMARY KEY, period TEXT NOT NULL,
                microcny INTEGER NOT NULL, note TEXT NOT NULL, legacy_usd_event TEXT UNIQUE);
        ''')
        # Preserve every original USD row and call. The old model guard used
        # USD 0.20 per CNY as a conservative ceiling; translate its reserves
        # exactly once, without treating that ceiling as a current FX rate.
        with self.db:
            self.db.execute('''INSERT OR IGNORE INTO model_budget_events
                SELECT 'legacy-usd:'||b.event,b.period,b.microusd*5,
                       'Historical conservative model reserve, not an actual supplier bill',b.event
                FROM budget_events b WHERE b.event='historical-model-safety-reserve-20261029'
                OR b.event IN (SELECT 'request:'||id FROM calls WHERE service='model')''')

    def _usd_used(self,period):
        return self.db.execute('''SELECT COALESCE(SUM(b.microusd),0) FROM budget_events b
            WHERE b.period=? AND NOT EXISTS (SELECT 1 FROM model_budget_events m WHERE m.legacy_usd_event=b.event)''',
            (period,)).fetchone()[0]

    @staticmethod
    def microusd(value):
        return int((Decimal(str(value)) * 1000000).to_integral_value(rounding=ROUND_CEILING))

    @staticmethod
    def budget_period():
        return datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m')

    def record_expense(self, event, usd, note='', period=None):
        period = period or self.budget_period()
        amount = self.microusd(usd)
        if amount < 0:
            raise ValueError('Expense cannot be negative')
        try:
            self.db.execute('BEGIN IMMEDIATE')
            old = self.db.execute('SELECT microusd FROM budget_events WHERE event=?', (event,)).fetchone()
            if old:
                if old[0] != amount:
                    raise ValueError('Expense already exists with a different amount')
            else:
                total = self._usd_used(period)
                if total + amount > self.cap_microusd:
                    raise APILimitReached('USD 4 monthly budget reached')
                self.db.execute('INSERT INTO budget_events VALUES (?,?,?,?)', (event, period, amount, note))
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    @staticmethod
    def key(*parts):
        return hashlib.sha256(json.dumps(parts, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

    def get(self, key):
        row = self.db.execute('SELECT value FROM cache WHERE key=? AND expires>?', (key, time.time())).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key, value, ttl=None):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO cache VALUES (?,?,?)',
                            (key, json.dumps(value, ensure_ascii=False), float('inf') if ttl is None else time.time() + ttl))

    def reserve(self, service, group, run_limit, monthly_limit=None, usd_ceiling=0, *, cny_ceiling=0):
        if service=='model' and usd_ceiling or service!='model' and cny_ceiling:
            raise ValueError('Model requests use cny_ceiling; other services use usd_ceiling')
        # Count failed/uncertain requests conservatively, since timeouts may
        # already have reached the supplier. Keys sharing billing share limits.
        zone = (ZoneInfo('Australia/Sydney') if service == 'nsw' else
                ZoneInfo('America/Los_Angeles') if service == 'maps' else timezone.utc)
        # All NSW endpoints share a daily quota. The existing period column
        # retains old monthly Google/model records unchanged.
        period = datetime.now(zone).strftime('%Y-%m-%d' if service == 'nsw' else '%Y-%m')
        try:
            self.db.execute('BEGIN IMMEDIATE')
            run_count = self.db.execute('SELECT COUNT(*) FROM calls WHERE run=? AND service=?',
                                        (self.run_id, service)).fetchone()[0]
            group_count = self.db.execute('SELECT COUNT(*) FROM calls WHERE service=? AND quota_group=? AND period=?',
                                          (service, group, period)).fetchone()[0]
            if run_count >= run_limit or monthly_limit is not None and group_count >= monthly_limit:
                raise APILimitReached(service + ' request limit reached')
            budget_period = self.budget_period()
            amount = self.microusd(cny_ceiling if service=='model' else usd_ceiling)
            if amount < 0:
                raise ValueError('Request reservation cannot be negative')
            if service=='model':
                total=self.db.execute('SELECT COALESCE(SUM(microcny),0) FROM model_budget_events WHERE period=?',
                                      (budget_period,)).fetchone()[0]
                if total+amount>self.model_cap_microcny:
                    raise APILimitReached('CNY 50 monthly model budget reached')
            elif self._usd_used(budget_period)+amount>self.cap_microusd:
                raise APILimitReached('USD 4 monthly operational budget reached')
            cursor = self.db.execute('INSERT INTO calls (run,service,quota_group,period,status) VALUES (?,?,?,?,?)',
                                     (self.run_id, service, group, period, 'reserved'))
            if service=='model':
                self.db.execute('INSERT INTO model_budget_events VALUES (?,?,?,?,NULL)',
                    ('request:'+str(cursor.lastrowid),budget_period,amount,'Native CNY model request reservation'))
            else:
                self.db.execute('INSERT INTO budget_events VALUES (?,?,?,?)',
                                ('request:' + str(cursor.lastrowid), budget_period, amount, 'conservative request reservation'))
            self.db.commit()
            return cursor.lastrowid
        except Exception:
            self.db.rollback()
            raise

    def finish(self, request_id, status, usage=None, actual_usd=None, *, actual_cny=None):
        usage = usage or {}
        with self.db:
            service=self.db.execute('SELECT service FROM calls WHERE id=?',(request_id,)).fetchone()
            if not service:
                raise ValueError('Unknown request')
            if service[0]=='model' and actual_usd is not None or service[0]!='model' and actual_cny is not None:
                raise ValueError('Request settlement currency does not match its reservation')
            self.db.execute('UPDATE calls SET status=?,input_tokens=?,output_tokens=? WHERE id=?',
                            (str(status), usage.get('input_tokens', 0), usage.get('output_tokens', 0), request_id))
            if actual_usd is not None:
                amount = self.microusd(actual_usd)
                old = self.db.execute('SELECT microusd FROM budget_events WHERE event=?', ('request:' + str(request_id),)).fetchone()
                if amount < 0 or old is None or amount > old[0]:
                    raise APILimitReached('Actual request cost exceeds its reserved bound; pricing requires review')
                self.db.execute('UPDATE budget_events SET microusd=?, note=? WHERE event=?',
                                (amount, 'token-based cost at conservative exchange ceiling', 'request:' + str(request_id)))
            if actual_cny is not None:
                amount=self.microusd(actual_cny)
                old=self.db.execute('SELECT microcny FROM model_budget_events WHERE event=?',('request:'+str(request_id),)).fetchone()
                if amount<0 or old is None or amount>old[0]:
                    raise APILimitReached('Actual model cost exceeds its native CNY reservation')
                self.db.execute('UPDATE model_budget_events SET microcny=?,note=? WHERE event=?',
                    (amount,'Token-based model cost in native CNY','request:'+str(request_id)))

    def budget_summary(self):
        used = self._usd_used(self.budget_period())
        return {'period': self.budget_period(), 'ceiling_usd': self.cap_microusd / 1000000,
                'spent_or_reserved_usd': used / 1000000, 'remaining_usd': (self.cap_microusd - used) / 1000000}

    def model_budget_summary(self):
        used=self.db.execute('SELECT COALESCE(SUM(microcny),0) FROM model_budget_events WHERE period=?',
                             (self.budget_period(),)).fetchone()[0]
        return {'period':self.budget_period(),'ceiling_cny':self.model_cap_microcny/1000000,
                'spent_or_reserved_cny':used/1000000,'remaining_cny':(self.model_cap_microcny-used)/1000000}

    def summary(self):
        return [dict(zip(('service', 'requests', 'input_tokens', 'output_tokens'), row)) for row in self.db.execute(
            'SELECT service,COUNT(*),SUM(input_tokens),SUM(output_tokens) FROM calls WHERE run=? GROUP BY service', (self.run_id,))]

    def close(self):
        self.db.close()
        fcntl.flock(self.lock, fcntl.LOCK_UN)
        os.close(self.lock)

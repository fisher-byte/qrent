"""SQLite sink compatible with the recovered Qrent server schema.

Source: /root/qrent-scraper/pipeline_runner.py, recovered 2026-10-27.
Partial or failed crawls never deactivate listings.
"""
import json
import sqlite3
from datetime import datetime
from pathlib import Path

from ..models import PropertyData, PropertySource


FIELDS = ("house_id", "source", "price_per_week", "address_line1", "address_line2", "suburb", "state",
          "postcode", "bedroom_count", "bathroom_count", "parking_count", "property_type",
          "property_type_raw", "description_en", "description_cn", "keywords", "url", "thumbnail_url",
          "available_date", "published_at", "scraped_at")
KEEP_FIELDS = ("description_en", "description_cn", "keywords", "thumbnail_url", "available_date")


def retained_details(data, *previous):
    for name in KEEP_FIELDS:
        if not data.get(name):
            for old in previous:
                if old.get(name):
                    data[name] = old[name]
                    break


def read_payload(payload, house_id, source):
    try:
        data = json.loads(payload) if payload is not None else {}
    except (ValueError, TypeError):
        return {}
    if (not isinstance(data, dict) or data.get('house_id', house_id) != house_id
            or data.get('source', source) != source):
        return {}
    return data


class SQLiteStore:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA journal_mode=WAL")
        definitions = ", ".join(f"{name} " + ("INTEGER" if name in {
            "price_per_week", "bedroom_count", "bathroom_count", "parking_count", "property_type"}
            else "TEXT") for name in FIELDS)
        self.connection.execute(f"CREATE TABLE IF NOT EXISTS properties (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                                f"{definitions}, updated_at TEXT, is_active INTEGER DEFAULT 1, "
                                "UNIQUE(house_id, source))")
        self.connection.execute("CREATE TABLE IF NOT EXISTS property_enrichment (house_id TEXT, source TEXT, "
                                "payload TEXT NOT NULL, PRIMARY KEY(house_id, source))")
        self.connection.commit()

    def save(self, properties):
        stats = {"inserted": 0, "updated": 0}
        updates = []
        for name in FIELDS[2:]:
            expression = f"excluded.{name}"
            if name in KEEP_FIELDS:
                expression = f"COALESCE(NULLIF(excluded.{name}, ''), properties.{name})"
            updates.append(f"{name} = {expression}")
        sql = (f"INSERT INTO properties ({','.join(FIELDS)},updated_at,is_active) "
               f"VALUES ({','.join('?' for _ in range(len(FIELDS)+1))},1) "
               f"ON CONFLICT(house_id,source) DO UPDATE SET {','.join(updates)}, "
               "updated_at=excluded.updated_at,is_active=1")
        with self.connection:
            for prop in properties:
                data = prop.to_dict()
                existing = self.connection.execute(
                    f"SELECT {','.join(FIELDS)} FROM properties WHERE house_id=? AND source=?",
                    (prop.house_id, prop.source.value)).fetchone()
                previous = self.connection.execute(
                    "SELECT payload FROM property_enrichment WHERE house_id=? AND source=?",
                    (prop.house_id, prop.source.value)).fetchone()
                old = read_payload(previous[0] if previous else None, prop.house_id, prop.source.value)
                retained_details(data, old, dict(zip(FIELDS, existing)) if existing else {})
                for name in ("average_score", "scores"):
                    if not data.get(name) and old.get(name):
                        data[name] = old[name]
                old_commutes = old.get("commute_times") or {}
                data["commute_times"] = {**(old_commutes if isinstance(old_commutes, dict) else {}),
                                         **data.get("commute_times", {})}
                for name in FIELDS:
                    if isinstance(data[name], datetime):
                        data[name] = data[name].isoformat()
                self.connection.execute(sql, [data[name] for name in FIELDS] + [datetime.now().isoformat()])
                self.connection.execute("INSERT OR REPLACE INTO property_enrichment VALUES (?,?,?)",
                                        (prop.house_id, prop.source.value, json.dumps(data, default=str)))
                stats["updated" if existing else "inserted"] += 1
        return stats

    def recent(self, days=7):
        result = []
        cutoff = datetime.now().timestamp() - days * 86400
        columns = ','.join('p.' + name for name in FIELDS)
        allowed = set(FIELDS) | {'average_score', 'scores', 'commute_times', 'price_text'}
        for row in self.connection.execute(
                f"SELECT {columns},e.payload FROM properties p LEFT JOIN property_enrichment e "
                "ON p.house_id=e.house_id AND p.source=e.source"):
            base = dict(zip(FIELDS, row[:-1]))
            old = read_payload(row[-1], base['house_id'], base['source'])
            data = {**base, **{name: value for name, value in old.items() if name in allowed}}
            data['house_id'], data['source'] = base['house_id'], base['source']
            retained_details(data, base)
            for name in ("scraped_at", "published_at", "available_date"):
                if data.get(name):
                    try:
                        data[name] = datetime.fromisoformat(data[name])
                    except (ValueError, TypeError):
                        if name == "scraped_at":
                            data[name] = None
            try:
                if isinstance(data.get("scraped_at"), datetime) and data["scraped_at"].timestamp() >= cutoff:
                    result.append(PropertyData.from_dict(data))
            except (ValueError, TypeError, OverflowError, OSError):
                continue
        return result

    def close(self):
        self.connection.close()

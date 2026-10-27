import json
from datetime import datetime, timedelta
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.models import PropertyData, PropertySource
from src.services.sqlite_store import SQLiteStore


DETAIL_FIELDS = ('description_en', 'description_cn', 'keywords', 'thumbnail_url', 'available_date')


def details(label='old'):
    return {'description_en': label + ' body', 'description_cn': label + ' 正文',
            'keywords': label + ' balcony', 'thumbnail_url': 'https://cdn.example/' + label + '.jpg',
            'available_date': datetime(2026, 11, 1 if label == 'old' else 2)}


class SQLiteStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = SQLiteStore(Path(self.temporary.name) / 'properties.db')
        self.addCleanup(self.store.close)

    def prop(self, **changes):
        return PropertyData(**{'house_id': '123', 'source': PropertySource.DOMAIN,
            'address_line1': '10 Example Street', 'suburb': 'Sydney', 'state': 'NSW',
            'postcode': '2000', 'price_per_week': 600, **changes})

    def alter_payload(self, kind, house_id='123'):
        connection = self.store.connection
        if kind == 'missing':
            connection.execute('DELETE FROM property_enrichment WHERE house_id=? AND source=?', (house_id, 'domain'))
        else:
            data = json.loads(connection.execute('SELECT payload FROM property_enrichment WHERE house_id=? AND source=?',
                                                (house_id, 'domain')).fetchone()[0])
            data.update({name: None for name in DETAIL_FIELDS})
            payload = '{broken' if kind == 'malformed' else json.dumps(data)
            connection.execute('UPDATE property_enrichment SET payload=? WHERE house_id=? AND source=?',
                               (payload, house_id, 'domain'))
        connection.commit()

    def assert_details(self, expected, *, house_id='123'):
        connection = self.store.connection
        row = connection.execute('SELECT ' + ','.join(DETAIL_FIELDS) + ' FROM properties WHERE house_id=? AND source=?',
                                 (house_id, 'domain')).fetchone()
        payload = json.loads(connection.execute('SELECT payload FROM property_enrichment WHERE house_id=? AND source=?',
                                               (house_id, 'domain')).fetchone()[0])
        recent = next(prop for prop in self.store.recent() if prop.house_id == house_id and prop.source == PropertySource.DOMAIN)
        for name, sql_value in zip(DETAIL_FIELDS, row):
            value = expected[name]
            if isinstance(value, datetime):
                self.assertEqual(datetime.fromisoformat(sql_value), value)
                self.assertEqual(datetime.fromisoformat(payload[name]), value)
            else:
                self.assertEqual(sql_value, value)
                self.assertEqual(payload[name], value)
            self.assertEqual(getattr(recent, name), value)

    def test_partial_save_merges_healthy_missing_and_empty_payloads_before_both_writes(self):
        for kind in ('healthy', 'missing', 'empty', 'malformed'):
            with self.subTest(kind=kind):
                self.store.save([self.prop(**details())])
                if kind != 'healthy':
                    self.alter_payload(kind)
                self.store.save([self.prop(price_per_week=610)])
                self.assert_details(details())

    def test_old_json_precedes_old_sql_and_new_nonempty_values_precede_both(self):
        self.store.save([self.prop(**details())])
        self.store.connection.execute('UPDATE properties SET description_en=?', ('SQL fallback',))
        self.store.connection.commit()
        self.store.save([self.prop()])
        self.assert_details(details())
        self.store.save([self.prop(**details('new'))])
        self.assert_details(details('new'))

    def test_recent_reads_legacy_or_inconsistent_details_without_writing_repairs(self):
        for kind in ('missing', 'empty', 'malformed'):
            with self.subTest(kind=kind):
                self.store.save([self.prop(**details())])
                self.alter_payload(kind)
                before = [self.store.connection.execute('SELECT * FROM ' + table).fetchall()
                          for table in ('properties', 'property_enrichment')]
                recent = self.store.recent()
                self.assertEqual(len(recent), 1)
                for name, value in details().items():
                    self.assertEqual(getattr(recent[0], name), value)
                after = [self.store.connection.execute('SELECT * FROM ' + table).fetchall()
                         for table in ('properties', 'property_enrichment')]
                self.assertEqual(after, before)

    def test_recent_preserves_age_and_source_identity_and_skips_invalid_history(self):
        self.store.save([self.prop(**details()), self.prop(house_id='old', scraped_at=datetime.now()-timedelta(days=8)),
                         self.prop(house_id='bad-date'), self.prop(house_id='bad-source')])
        self.alter_payload('missing')
        self.store.connection.execute("UPDATE properties SET scraped_at='not-a-date' WHERE house_id='bad-date'")
        self.store.connection.execute("UPDATE properties SET source='unknown' WHERE house_id='bad-source'")
        self.store.connection.execute("DELETE FROM property_enrichment WHERE house_id IN ('old','bad-date','bad-source')")
        self.store.connection.commit()
        self.store.save([self.prop(source=PropertySource.REALESTATE, description_en='REA body')])
        recent = self.store.recent()
        self.assertEqual({(prop.house_id, prop.source) for prop in recent},
                         {('123', PropertySource.DOMAIN), ('123', PropertySource.REALESTATE)})
        self.assertEqual(next(prop.description_en for prop in recent if prop.source == PropertySource.DOMAIN), 'old body')
        self.assertEqual(next(prop.description_en for prop in recent if prop.source == PropertySource.REALESTATE), 'REA body')

    def test_score_and_commute_merge_behavior_is_unchanged(self):
        self.store.save([self.prop(**details(), average_score=15, scores=[1, 2, 3], commute_times={'uni': 12, 'office': 20})])
        self.store.save([self.prop()])
        recent = self.store.recent()[0]
        self.assertEqual((recent.average_score, recent.scores, recent.commute_times), (15, [1, 2, 3], {'uni':12, 'office':20}))
        self.store.save([self.prop(average_score=10, scores=[9], commute_times={'uni':9})])
        recent = self.store.recent()[0]
        self.assertEqual((recent.average_score, recent.scores, recent.commute_times), (10, [9], {'uni':9, 'office':20}))


if __name__ == '__main__':
    unittest.main()

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# The legacy importer loads dotenv at import time; these pure parser tests must
# never read a real private environment file or connect to a database.
with patch('dotenv.load_dotenv', return_value=False):
    from src.services import database_importer
    from src.utils import helpers


class DateIntegrityTests(unittest.TestCase):
    def parsers(self):
        return (helpers.safe_datetime, database_importer.safe_datetime)

    def test_future_iso_dates_keep_their_date_time_and_timezone(self):
        cases = (
            ('2026-11-18T04:05:06.123456', datetime(2026, 11, 18, 4, 5, 6, 123456)),
            ('2026-11-18T04:05:06Z', datetime(2026, 11, 18, 4, 5, 6, tzinfo=timezone.utc)),
            ('2026-11-18T04:05:06+11:00',
             datetime(2026, 11, 18, 4, 5, 6, tzinfo=timezone(timedelta(hours=11)))),
            ('2026-11-18 04:05:06-03:30',
             datetime(2026, 11, 18, 4, 5, 6, tzinfo=timezone(-timedelta(hours=3, minutes=30)))),
        )
        for parse in self.parsers():
            for text, expected in cases:
                with self.subTest(parser=parse.__module__, text=text):
                    self.assertEqual(parse(text), expected)

    def test_missing_or_invalid_dates_never_become_today(self):
        for parse in self.parsers():
            for value in (None, '', '  ', float('nan'), pd.NA, pd.NaT,
                          'unknown', '2026-02-30', 'not a date'):
                with self.subTest(parser=parse.__module__, value=repr(value)):
                    self.assertIsNone(parse(value))

    def test_explicit_default_and_aware_datetime_are_preserved(self):
        fallback = datetime(2027, 1, 2, tzinfo=timezone.utc)
        value = datetime(2026, 11, 18, 9, tzinfo=timezone(timedelta(hours=11)))
        for parse in self.parsers():
            with self.subTest(parser=parse.__module__):
                self.assertIs(parse(None, fallback), fallback)
                self.assertIs(parse('invalid', fallback), fallback)
                self.assertIs(parse(value), value)

    def test_legacy_australian_dates_remain_supported(self):
        for parse in self.parsers():
            for value in ('18/11/2026', '18-11-2026', '2026/11/18', '2026-11-18'):
                with self.subTest(parser=parse.__module__, value=value):
                    self.assertEqual(parse(value), datetime(2026, 11, 18))

    def test_import_without_date_columns_sends_null_dates_to_mysql(self):
        connection, cursor = MagicMock(), MagicMock()
        connection.cursor.return_value = cursor
        cursor.lastrowid = 9
        cursor.fetchone.return_value = (8,)
        cursor.fetchall.side_effect = lambda: (
            [(7, 'newtown', 'NSW', 2042)]
            if 'FROM regions' in cursor.execute.call_args.args[0] else [])
        frame = pd.DataFrame([{'houseId': 456, 'pricePerWeek': 530,
                               'addressLine1': '1 Example Street',
                               'addressLine2': 'newtown-nsw-2042'}])
        with patch.object(database_importer.mysql.connector, 'connect', return_value=connection):
            self.assertEqual(database_importer.import_dataframe(frame, 'UNSW'), (1, 0))
        sql, values = next(call.args for call in cursor.execute.call_args_list
                           if call.args[0].lstrip().startswith('INSERT INTO properties'))
        columns = sql.split('(', 1)[1].split(')', 1)[0].split(',')
        stored = dict(zip((name.strip() for name in columns), values))
        self.assertIsNone(stored['published_at'])
        self.assertIsNone(stored['available_date'])


class PriceIntegrityTests(unittest.TestCase):
    def test_single_whole_weekly_prices_are_preserved(self):
        cases = ((530, 530), ('530', 530), ('$530 per week', 530),
                 ('$530pw', 530), ('$530 p.w.', 530), ('$530 p/w', 530),
                 ('$530/week', 530), ('Weekly rent $530', 530),
                 ('$1,250.00 per week', 1250), ('$530.0', 530))
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(helpers.extract_price(text), expected)

    def test_ranges_are_unknown_in_the_scalar_price_field(self):
        for text in ('$800-$880 per week', '$230 - $280 pw', '$160 to $695 pw',
                     '$170–$240', '$550—$550 weekly', '$530 pw to $2,303 pcm'):
            with self.subTest(text=text):
                self.assertEqual(helpers.extract_price(text), 0)

    def test_explicit_weekly_component_of_a_week_month_quote_is_used(self):
        for text in ('$530 pw / $2,303 pcm', '$2,303 pcm ($530 pw)',
                     '$530.00 per week / $2,303.00 per calendar month'):
            with self.subTest(text=text):
                self.assertEqual(helpers.extract_price(text), 530)

    def test_explicit_nonweekly_quotes_are_not_weekly_prices(self):
        for text in ('$2,303 pcm', '$2,303 per month', 'Monthly rent $2,303',
                     '$2,303 p.c.m.', '$2,303 a month',
                     '$2,303/mth', '$2,303 monthly payment',
                     '$85 per day', '$85/day', '$85 daily',
                     '$85 a day', '$85 per night',
                     '$27,560 per annum', '$27,560 yearly', '$27,560 p.a.',
                     '$27,560 a year', 'Annual rent $27,560', '$27,560 per calendar year',
                     '$1,000 per fortnight', '$100 per hour'):
            with self.subTest(text=text):
                self.assertEqual(helpers.extract_price(text), 0)

    def test_ambiguous_multiple_quotes_are_not_concatenated_or_guessed(self):
        for text in ('$500 or $600', '$500 pw / $600 pw', '$500 pw + $100 bond',
                     '$530 pw / $100 per day', '$530 pw + $2,303 pcm',
                     '$500 pw / $2,000 pcm / $50 fee'):
            with self.subTest(text=text):
                self.assertEqual(helpers.extract_price(text), 0)

    def test_fractional_or_invalid_amounts_are_not_silently_truncated(self):
        for text in ('$530.50 per week', 530.5, '$530.001', '-$530 pw',
                     '1e3', '$1,20', None, '', 'Contact agent'):
            with self.subTest(text=text):
                self.assertEqual(helpers.extract_price(text), 0)


class AvailabilityIntegrityTests(unittest.TestCase):
    def test_explicit_now_or_immediately_is_current_availability(self):
        class Clock(datetime):
            @classmethod
            def now(cls):
                return cls(2026, 10, 28, 12)

        with patch.object(helpers, 'datetime', Clock):
            for text in ('Now', ' now ', 'Available now', 'Available: Now',
                         'Now available', 'Immediately', 'Available immediately',
                         'Available from immediately'):
                with self.subTest(text=text):
                    self.assertEqual(helpers.parse_available_date(text), datetime(2026, 10, 28, 12))

    def test_unknown_or_calls_to_action_do_not_mean_available_now(self):
        for text in ('unknown', 'Date unknown', 'Apply now', 'Enquire now',
                     'Call now', 'Available later - apply now',
                     'Not available now', 'No longer available now', None, ''):
            with self.subTest(text=text):
                self.assertIsNone(helpers.parse_available_date(text))

    def test_explicit_future_availability_is_preserved(self):
        for text, expected in (('Available 18 Nov 2026', datetime(2026, 11, 18)),
                               ('Available from Monday, 15 January 2024', datetime(2024, 1, 15)),
                               ('Available 23 Oct 2029', datetime(2029, 10, 23))):
            with self.subTest(text=text):
                self.assertEqual(helpers.parse_available_date(text), expected)


if __name__ == '__main__':
    unittest.main()

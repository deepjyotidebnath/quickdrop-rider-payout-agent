import os
import unittest
from datetime import date

from app import policy
from app.data import DataStore, norm_rider

DATA = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")


class PolicyTests(unittest.TestCase):
    def test_fare_examples_from_policy(self):
        self.assertEqual(policy.trip_fare(1.0, 1.0), 25)          # inside the first 2 km
        self.assertEqual(policy.trip_fare(6.0, 1.5), 74)          # (25+24)*1.5 = 73.5 -> half up
        self.assertEqual(policy.trip_fare(5.0, 1.5), 65)          # 64.5 -> half up
        self.assertEqual(policy.trip_fare(7.4, 1.0), 57)          # 25 + 6*5.4 = 57.4

    def test_metres_are_converted(self):
        self.assertEqual(policy.normalise_km(5000), 5.0)
        self.assertEqual(policy.normalise_km(13.0), 13.0)

    def test_ist_day_boundary(self):
        self.assertEqual(policy.ist_date("2026-09-18T20:00:00Z"), date(2026, 9, 19))   # 01:30 IST next day

    def test_window(self):
        today = date(2026, 9, 23)
        self.assertTrue(policy.in_window(date(2026, 9, 18), today))
        self.assertFalse(policy.in_window(date(2026, 9, 13), today))


class DataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = DataStore(DATA)

    def test_rider_ids_normalised(self):
        self.assertEqual(norm_rider("r19"), "R019")
        self.assertEqual(norm_rider("R7"), "R007")
        self.assertEqual(norm_rider(" R003 "), "R003")

    def test_duplicate_trip_rows_collapsed(self):
        self.assertEqual(len(self.d.trips), 3304)
        self.assertEqual(self.d.completed_count("R009", date(2026, 9, 17)), 11)   # 12 rows, one is a duplicate

    def test_incentive_uses_ist_days(self):
        self.assertEqual(self.d.completed_count("R024", date(2026, 9, 18)), 12)   # only 9 on the UTC date
        self.assertEqual(self.d.completed_count("R025", date(2026, 9, 19)), 11)   # 13 on the UTC date

    def test_metre_rows(self):
        t = self.d.trip("T312538")
        self.assertEqual(t.km, 5.0)
        self.assertTrue(t.km_was_metres)
        self.assertEqual(t.fare, 65)

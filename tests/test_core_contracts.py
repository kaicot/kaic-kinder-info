import math
import unittest
from datetime import datetime
from unittest.mock import patch

import kaic_kinder_core as core


class AdmissionTests(unittest.TestCase):
    def test_birth_month_formats(self):
        self.assertEqual(core.parse_birth_ym("2020-03"), (2020, 3))
        self.assertEqual(core.parse_birth_ym("2020.3"), (2020, 3))
        self.assertEqual(core.parse_birth_ym("202003"), (2020, 3))
        self.assertIsNone(core.parse_birth_ym("2020-13"))

    def test_age_and_school_year(self):
        self.assertEqual(core.age_class_for(2020, 2024), 3)
        self.assertEqual(core.current_school_year(datetime(2026, 2, 1)), 2025)
        self.assertEqual(core.current_school_year(datetime(2026, 3, 1)), 2026)
        self.assertEqual(core.next_admission_year(datetime(2026, 9, 1)), 2027)


class MetricTests(unittest.TestCase):
    def test_integer_and_capacity_metrics(self):
        row = {
            "ppcnt3": "12명", "ppcnt4": "13", "ppcnt5": "14",
            "mixppcnt": "6", "shppcnt": "0", "prmstfcnt": "50",
            "clcnt3": "1", "clcnt4": "1", "clcnt5": "1",
            "mixclcnt": "1", "shclcnt": "0",
        }
        self.assertEqual(core.to_int("1,234㎡"), 1234)
        self.assertEqual(core.total_pupils(row), 45)
        self.assertEqual(core.total_classes(row), 4)
        self.assertEqual(core.fill_rate(row), 90)
        self.assertEqual(core.per_class(row, 3), 12.0)

    def test_staff_and_operation_metrics(self):
        tenure = {
            "yy1_undr_thcnt": "2", "yy1_abv_yy2_undr_thcnt": "1",
            "yy2_abv_yy4_undr_thcnt": "1", "yy4_abv_yy6_undr_thcnt": "0",
            "yy6_abv_thcnt": "0",
        }
        self.assertEqual(core.tenure_stats(tenure), (4, 1.4, 50, 0))
        self.assertEqual(core.afterschool_participants({
            "inor_ptcn_kpcnt": "7", "pm_rrgn_ptcn_kpcnt": "5"
        }), 12)
        self.assertEqual(core.attendance_days({
            "ag3_lsn_dcnt": "180", "afsc_pros_lsn_dcnt": "230"
        }, 3), (180, 230))

    def test_distance_contract(self):
        km = core.haversine_km((37.5665, 126.9780), (37.5665, 126.9780))
        self.assertTrue(math.isclose(km, 0.0, abs_tol=1e-9))


class RegionalCollectionTests(unittest.TestCase):
    def test_each_endpoint_is_fetched_once_per_region(self):
        basics = [
            {"kindercode": "a", "kindername": "가유치원"},
            {"kindercode": "b", "kindername": "나유치원"},
        ]
        calls = []

        def fake_fetch(endpoint, sido, sgg, fresh=False):
            calls.append((endpoint, sido, sgg, fresh))
            if endpoint == "basicInfo2":
                return [dict(row) for row in basics]
            return [
                {"kindercode": "a", "kindername": "가유치원", "value": endpoint},
                {"kindercode": "b", "kindername": "나유치원", "value": endpoint},
            ]

        with patch("kinderinfo.fetch", side_effect=fake_fetch):
            records = core.collect_region("11", "11140", "서울 중구", fresh=True)

        expected = 1 + len(core._impl.PROFILE_SECTIONS)
        self.assertEqual(len(calls), expected)
        self.assertEqual(len(set(endpoint for endpoint, *_ in calls)), expected)
        self.assertTrue(all(call[3] for call in calls))
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0][0]["_sgg_name"], "서울 중구")
        self.assertEqual(records[1][1]["schoolBus"][0]["kindercode"], "b")


if __name__ == "__main__":
    unittest.main()

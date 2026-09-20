"""Regression cases from delayed September 2026 report runs; no network needed."""

import unittest
from datetime import datetime

from scripts.report_date import resolve_report_date


class ReportDateTests(unittest.TestCase):
    def test_normal_schedule(self):
        self.assertEqual(resolve_report_date("schedule", run_created_at="2026-09-17T15:34:40Z"), "2026-09-17")

    def test_observed_delay_past_beijing_midnight(self):
        self.assertEqual(resolve_report_date("schedule", run_created_at="2026-09-14T16:58:57Z"), "2026-09-14")

    def test_delay_past_utc_midnight(self):
        self.assertEqual(resolve_report_date("schedule", run_created_at="2026-09-15T00:20:00Z"), "2026-09-14")

    def test_exact_schedule_boundary(self):
        for stamp, expected in [("2026-09-15T11:29:59Z", "2026-09-14"), ("2026-09-15T11:30:00Z", "2026-09-15")]:
            with self.subTest(stamp=stamp):
                self.assertEqual(resolve_report_date("schedule", run_created_at=stamp), expected)

    def test_job_queue_does_not_change_report_date(self):
        self.assertEqual(resolve_report_date("schedule", run_created_at="2026-09-14T15:00:00Z", now=datetime.fromisoformat("2026-09-16T12:00:00+00:00")), "2026-09-14")

    def test_creation_time_with_offset(self):
        self.assertEqual(resolve_report_date("schedule", run_created_at="2026-09-15T00:58:57+08:00"), "2026-09-14")

    def test_schedule_requires_known_creation_time(self):
        with self.assertRaises(ValueError):
            resolve_report_date("schedule")

    def test_schedule_rejects_naive_creation_time(self):
        with self.assertRaises(ValueError):
            resolve_report_date("schedule", run_created_at="2026-09-14T15:00:00")

    def test_manual_before_close_uses_previous_day(self):
        self.assertEqual(resolve_report_date("workflow_dispatch", now=datetime.fromisoformat("2026-09-15T00:58:57+08:00")), "2026-09-14")

    def test_manual_after_close_uses_today(self):
        self.assertEqual(resolve_report_date("workflow_dispatch", now=datetime.fromisoformat("2026-09-15T15:00:00+08:00")), "2026-09-15")

    def test_explicit_date_for_backfill(self):
        self.assertEqual(resolve_report_date("workflow_dispatch", requested_date="2026-09-17", now=datetime.fromisoformat("2026-09-19T00:00:00+00:00")), "2026-09-17")

    def test_invalid_and_future_dates(self):
        for value in ["2026-02-30", "2026-9-1", "2026-09-21", "2026-09-17; exit 0"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                resolve_report_date("workflow_dispatch", requested_date=value, now=datetime.fromisoformat("2026-09-19T00:00:00+00:00"))


if __name__ == "__main__":
    unittest.main()

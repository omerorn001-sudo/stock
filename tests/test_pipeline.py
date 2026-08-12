from __future__ import annotations

import argparse
from datetime import date

from scripts.backfill_matrix import month_ranges
from src.pipeline import resolve_range


def test_backfill_matrix_splits_natural_months():
    assert month_ranges(date(2025, 12, 20), date(2026, 2, 3)) == [
        {"start": "2025-12-20", "end": "2025-12-31"},
        {"start": "2026-01-01", "end": "2026-01-31"},
        {"start": "2026-02-01", "end": "2026-02-03"},
    ]


def test_pipeline_resolves_explicit_range():
    args = argparse.Namespace(last_days=None, start=date(2026, 8, 1), end=date(2026, 8, 7))
    assert resolve_range(args) == (date(2026, 8, 1), date(2026, 8, 7))

"""Smoke tests for weekly calendar export (no Strava API)."""

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from export_training_calendar import build_calendar_rows, export_xlsx
from strava_data.activity_data_service import ActivityRecord


def _rec(
    rid: int,
    local: datetime,
    *,
    miles: float = 5.0,
    name: str = "Easy",
    desc: str = "ez",
) -> ActivityRecord:
    return ActivityRecord(
        id=rid,
        name=name,
        description=desc,
        sport_type="Run",
        type="Run",
        start_date=local.astimezone().isoformat(),
        start_date_local=local.isoformat(),
        timezone="America/New_York",
        distance_m=miles * 1609.344,
        distance_mi=miles,
        moving_time_s=30 * 60,
        elapsed_time_s=31 * 60,
        total_elevation_gain_m=100,
        total_elevation_gain_ft=328.084,
        average_speed_mps=2.0,
        average_pace_min_per_mi=6.0,
        max_speed_mps=3.0,
        average_heartrate=140.0,
        max_heartrate=155.0,
        strava_activity_url=f"https://www.strava.com/activities/{rid}",
    )


class ExportCalendarTests(unittest.TestCase):
    def test_build_calendar_rows_groups_by_week(self) -> None:
        mon = datetime(2024, 11, 25, 7, 0, 0)
        tue = mon + timedelta(days=1)
        records = [_rec(1, mon), _rec(2, tue)]
        rows = build_calendar_rows(records)
        self.assertTrue(any(r[0] == "Day of Week" for r in rows))
        self.assertTrue(any(r[0] == "Date" and "25-Nov" in r for r in rows))
        self.assertTrue(any(r[0] == "Miles" and "5.00" in r for r in rows))

    def test_export_xlsx_writes_file(self) -> None:
        mon = datetime(2024, 11, 25, 7, 0, 0)
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "out.xlsx"
            export_xlsx([_rec(1, mon)], out)
            self.assertTrue(out.is_file() and out.stat().st_size > 0)


if __name__ == "__main__":
    unittest.main()

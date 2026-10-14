"""Tests for Strava bulk-export → ActivityRecord conversion (no API)."""

import tempfile
import unittest
import zipfile
from io import StringIO
from pathlib import Path

import pandas as pd

from strava_data.bulk_export_service import BulkExportService, row_to_record


SAMPLE_CSV = """Activity ID,Activity Date,Activity Name,Activity Type,Activity Description,Elapsed Time,Distance,Max Heart Rate,Relative Effort,Commute,Filename,Elapsed Time,Moving Time,Distance,Max Speed,Average Speed,Elevation Gain,Average Cadence,Max Heart Rate,Average Heart Rate,Calories,Average Temperature,Relative Effort,Perceived Exertion,Weighted Average Power,Average Watts,Timezone,Weather Condition,Weather Temperature,Apparent Temperature,Humidity,Wind Speed
123,"Apr 16, 2026, 7:41:49 AM",GA,Run,With strides,2521,8.32,161,46,false,activities/123.fit.gz,2521.0,2401.0,8324.0,5.6,3.467,67.0,87.6,161.0,142.0,571.0,23.0,46,,327.0,328.0,(GMT-04:00) America/New_York,Clear,18.0,16.0,0.45,2.0
456,"Apr 14, 2026, 7:29:20 AM",Race Prep,Trail Run,3x5min,2662,10.03,,,false,activities/456.gpx,2662.0,2521.0,10028.2,6.14,3.978,32.0,,,,,,,,,(GMT-04:00) America/New_York,,,,,
789,"Apr 13, 2026, 6:43:33 AM",Spin,Virtual Ride,"",1800,20.0,,,false,activities/789.fit.gz,1800.0,1800.0,20000.0,8.0,11.11,0.0,,,,,,,,,(GMT-04:00) America/New_York,,,,,
"""


class BulkExportTests(unittest.TestCase):
    def _write_csv(self, directory: Path, name: str = "activities.csv") -> Path:
        path = directory / name
        path.write_text(SAMPLE_CSV, encoding="utf-8")
        return path

    def test_row_prefers_meter_distance_and_derives_pace(self) -> None:
        df = pd.read_csv(StringIO(SAMPLE_CSV))
        rec = row_to_record(df.iloc[0])
        assert rec is not None
        self.assertEqual(rec.id, 123)
        self.assertEqual(rec.name, "GA")
        self.assertEqual(rec.description, "With strides")
        self.assertEqual(rec.type, "Run")
        self.assertEqual(rec.sport_type, "Run")
        self.assertAlmostEqual(rec.distance_m or 0, 8324.0)
        self.assertAlmostEqual(rec.distance_mi or 0, 8324.0 / 1609.344)
        self.assertEqual(rec.moving_time_s, 2401.0)
        self.assertEqual(rec.suffer_score, 46)
        self.assertEqual(rec.timezone, "America/New_York")
        self.assertEqual(rec.utc_offset, -14400.0)
        self.assertEqual(rec.start_date_local, "2026-04-16T07:41:49")
        self.assertEqual(rec.start_date, "2026-04-16T11:41:49+00:00")
        self.assertEqual(rec.strava_activity_url, "https://www.strava.com/activities/123")
        self.assertEqual(rec.source_filename, "activities/123.fit.gz")
        self.assertIn("64 F", rec.weather_conditions or "")
        self.assertIn("45%", rec.weather_conditions or "")
        self.assertTrue(rec.fetched_detail)
        self.assertFalse(rec.manual)

    def test_trail_run_maps_to_run_parent_type(self) -> None:
        df = pd.read_csv(StringIO(SAMPLE_CSV))
        rec = row_to_record(df.iloc[1])
        assert rec is not None
        self.assertEqual(rec.sport_type, "TrailRun")
        self.assertEqual(rec.type, "Run")

    def test_virtual_ride_marked_trainer(self) -> None:
        df = pd.read_csv(StringIO(SAMPLE_CSV))
        rec = row_to_record(df.iloc[2])
        assert rec is not None
        self.assertEqual(rec.sport_type, "VirtualRide")
        self.assertEqual(rec.type, "Ride")
        self.assertTrue(rec.trainer)

    def test_runs_only_filter_includes_trail_run(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._write_csv(root)
            records = BulkExportService().load_records(root, activity_filter="Run")
            self.assertEqual([r.id for r in records], [123, 456])

    def test_writes_jsonl_and_reads_zip(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            csv_path = self._write_csv(root)
            zip_path = root / "export.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.write(csv_path, arcname="activities.csv")
            out = root / "out.jsonl"
            n = BulkExportService().collect_to_jsonl(zip_path, str(out), max_records=1)
            self.assertEqual(n, 1)
            self.assertTrue(out.is_file())
            line = out.read_text(encoding="utf-8").strip()
            self.assertIn('"id": 123', line)

    def test_export_without_timezone_column_treats_date_as_utc(self) -> None:
        csv = (
            "Activity ID,Activity Date,Activity Name,Activity Type,Distance,"
            "Weather Condition,Weather Temperature\n"
            '19949410908,"Aug 29, 2026, 1:10:13 AM",Late,Run,8.0,1.0,13.0\n'
        )
        df = pd.read_csv(StringIO(csv))
        rec = row_to_record(df.iloc[0], "America/New_York")
        assert rec is not None
        self.assertEqual(rec.start_date, "2026-08-29T01:10:13+00:00")
        self.assertEqual(rec.start_date_local, "2026-08-28T21:10:13")
        self.assertEqual(rec.timezone, "America/New_York")
        self.assertEqual(rec.utc_offset, -14400.0)
        self.assertEqual(rec.weather_conditions, "55 F")

    def test_export_without_timezone_falls_back_to_utc(self) -> None:
        csv = 'Activity ID,Activity Date,Activity Type\n1,"Aug 29, 2026, 1:10:13 AM",Run\n'
        rec = row_to_record(pd.read_csv(StringIO(csv)).iloc[0])
        assert rec is not None
        self.assertEqual(rec.start_date, "2026-08-29T01:10:13+00:00")
        self.assertEqual(rec.start_date_local, "2026-08-29T01:10:13")
        self.assertEqual(rec.timezone, "UTC")

    def test_missing_csv_raises(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(FileNotFoundError):
                BulkExportService().load_records(Path(td))


if __name__ == "__main__":
    unittest.main()

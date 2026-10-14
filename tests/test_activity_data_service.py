"""Tests for API activity normalization and incremental JSONL updates (no network)."""

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from stravalib.model import DetailedActivity

from strava_data.activity_data_service import ActivityDataService, ActivityRecord


def _activity(activity_id: int, start_utc: str, sport_type: str = "Run") -> DetailedActivity:
    return DetailedActivity.model_validate(
        {
            "id": activity_id,
            "name": f"Activity {activity_id}",
            "description": "Easy",
            "sport_type": sport_type,
            "type": sport_type,
            "start_date": start_utc,
            "start_date_local": start_utc.replace("Z", ""),
            "timezone": "(GMT-05:00) America/New_York",
            "utc_offset": -14400.0,
            "distance": 8046.72,
            "moving_time": 2400,
            "elapsed_time": 2500,
            "total_elevation_gain": 30.0,
            "average_speed": 3.35,
            "max_heartrate": 165,
        }
    )


class FakeClient:
    def __init__(
        self,
        activities: list[DetailedActivity],
        fail_on_detail: tuple[int, BaseException] | None = None,
    ) -> None:
        self.activities = activities
        self.after_calls: list[datetime | None] = []
        self.fail_on_detail = fail_on_detail

    def get_activities(self, before=None, after=None, limit=None):
        self.after_calls.append(after)
        items = sorted(
            (a for a in self.activities if after is None or a.start_date > after),
            key=lambda a: a.start_date,
        )
        return iter(items[:limit])

    def get_activity(self, activity_id, include_all_efforts=False):
        if self.fail_on_detail and self.fail_on_detail[0] == activity_id:
            raise self.fail_on_detail[1]
        return next(a for a in self.activities if a.id == activity_id)


def _service(
    activities: list[DetailedActivity],
    fail_on_detail: tuple[int, BaseException] | None = None,
) -> tuple[ActivityDataService, FakeClient]:
    client = FakeClient(activities, fail_on_detail)
    return ActivityDataService(auth=object(), client=client), client  # type: ignore[arg-type]


def _write_rows(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _read_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


class ActivityToRecordTests(unittest.TestCase):
    def test_stravalib_v2_types_are_normalized(self) -> None:
        svc, _ = _service([])
        rec = svc.activity_to_record(
            _activity(1, "2026-10-01T11:00:00Z"), fetch_detail=False
        )
        self.assertEqual(rec.moving_time_s, 2400.0)
        self.assertEqual(rec.elapsed_time_s, 2500.0)
        self.assertAlmostEqual(rec.distance_mi or 0, 5.0)
        self.assertAlmostEqual(rec.average_pace_min_per_mi or 0, 8.0)
        self.assertEqual(rec.timezone, "America/New_York")
        self.assertEqual(rec.sport_type, "Run")
        self.assertEqual(rec.start_date, "2026-10-01T11:00:00+00:00")
        self.assertEqual(rec.max_heartrate, 165.0)


class MergeTests(unittest.TestCase):
    def test_upsert_keeps_existing_values_when_new_is_null(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "activities.jsonl"
            _write_rows(
                path,
                [
                    {"id": 1, "start_date": "2026-01-01T10:00:00+00:00", "name": "Old",
                     "weather_conditions": "Clear / 50 F"},
                ],
            )
            records = [
                ActivityRecord(id=1, name="Renamed", description=None, sport_type="Run",
                               type="Run", start_date="2026-01-01T10:00:00+00:00",
                               start_date_local=None, timezone=None),
                ActivityRecord(id=2, name="New", description=None, sport_type="Run",
                               type="Run", start_date="2026-02-01T10:00:00+00:00",
                               start_date_local=None, timezone=None),
            ]
            added, updated = ActivityDataService.merge_records_jsonl(records, str(path))
            self.assertEqual((added, updated), (1, 1))
            rows = _read_rows(path)
            self.assertEqual([r["id"] for r in rows], [2, 1])
            self.assertEqual(rows[1]["name"], "Renamed")
            self.assertEqual(rows[1]["weather_conditions"], "Clear / 50 F")


class UpdateTests(unittest.TestCase):
    def test_update_fetches_only_after_newest_stored_activity(self) -> None:
        svc, client = _service(
            [
                _activity(10, "2026-04-16T11:41:49Z"),
                _activity(11, "2026-06-01T11:00:00Z"),
                _activity(12, "2026-06-02T11:00:00Z", sport_type="Ride"),
                _activity(13, "2026-10-12T11:00:00Z"),
            ]
        )
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "activities.jsonl"
            _write_rows(path, [{"id": 10, "start_date": "2026-04-16T11:41:49+00:00"}])
            added, updated = svc.update_jsonl(str(path), activity_filter="Run")
            self.assertEqual((added, updated), (2, 0))
            self.assertEqual(
                client.after_calls[0],
                datetime(2026, 4, 16, 11, 41, 49, tzinfo=timezone.utc),
            )
            self.assertEqual([r["id"] for r in _read_rows(path)], [13, 11, 10])

    def test_budget_stop_keeps_oldest_new_activities_for_next_update(self) -> None:
        svc, _ = _service(
            [
                _activity(11, "2026-06-01T11:00:00Z"),
                _activity(12, "2026-07-01T11:00:00Z"),
                _activity(13, "2026-08-01T11:00:00Z"),
            ]
        )
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "activities.jsonl"
            _write_rows(path, [{"id": 10, "start_date": "2026-04-16T11:41:49+00:00"}])
            # 1 list page + 2 detail fetches
            svc.update_jsonl(str(path), max_api_requests=3)
            self.assertEqual([r["id"] for r in _read_rows(path)], [12, 11, 10])
            svc.update_jsonl(str(path))
            self.assertEqual([r["id"] for r in _read_rows(path)], [13, 12, 11, 10])

    def test_ctrl_c_keeps_activities_fetched_so_far(self) -> None:
        svc, _ = _service(
            [
                _activity(11, "2026-06-01T11:00:00Z"),
                _activity(12, "2026-07-01T11:00:00Z"),
                _activity(13, "2026-08-01T11:00:00Z"),
            ],
            fail_on_detail=(13, KeyboardInterrupt()),
        )
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "activities.jsonl"
            _write_rows(path, [{"id": 10, "start_date": "2026-04-16T11:41:49+00:00"}])
            added, _ = svc.update_jsonl(str(path))
            self.assertEqual(added, 2)
            self.assertIn("interrupted", svc._sync_stop_reason or "")
            self.assertEqual([r["id"] for r in _read_rows(path)], [12, 11, 10])

    def test_checkpoints_survive_an_unexpected_error(self) -> None:
        svc, _ = _service(
            [
                _activity(11, "2026-06-01T11:00:00Z"),
                _activity(12, "2026-07-01T11:00:00Z"),
                _activity(13, "2026-08-01T11:00:00Z"),
            ],
            fail_on_detail=(13, ValueError("boom")),
        )
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "activities.jsonl"
            _write_rows(path, [{"id": 10, "start_date": "2026-04-16T11:41:49+00:00"}])
            with self.assertRaises(ValueError):
                svc.update_jsonl(str(path), checkpoint_every=1)
            self.assertEqual([r["id"] for r in _read_rows(path)], [12, 11, 10])

    def test_update_requires_existing_dates(self) -> None:
        svc, _ = _service([])
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):
                svc.update_jsonl(str(Path(td) / "missing.jsonl"))


class ShortRateLimitWaitTests(unittest.TestCase):
    def _wait(self, until_reset: float) -> float:
        svc, _ = _service([])
        with mock.patch.object(
            ActivityDataService, "_seconds_until_next_quarter_hour", return_value=until_reset
        ), mock.patch("strava_data.activity_data_service.time.sleep") as sleep, \
                mock.patch("builtins.print"):
            svc._wait_for_short_rate_limit("Fetch", None, 1)
        return sleep.call_args.args[0]

    def test_waits_until_window_reset(self) -> None:
        self.assertAlmostEqual(self._wait(300.0), 300.0, delta=1.0)

    def test_retries_shortly_when_window_just_rolled_over(self) -> None:
        self.assertAlmostEqual(
            self._wait(890.0), ActivityDataService.RESET_RETRY_SECONDS, delta=1.0
        )


if __name__ == "__main__":
    unittest.main()

"""Fetch Strava activities with pagination and normalize to JSON-serializable records."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

from stravalib.client import Client
from stravalib.model import Activity

from strava_data.auth_service import StravaAuthService


def _quantity_num(value: Any) -> float | None:
    if value is None:
        return None
    if hasattr(value, "num"):
        return float(value.num)
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _timedelta_seconds(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, timedelta):
        return value.total_seconds()
    return None


def _type_label(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "root"):
        return str(value.root)
    return str(value)


@dataclass
class ActivityRecord:
    """Normalized activity suitable for export, dashboards, and archival JSON."""

    id: int
    name: str | None
    description: str | None
    sport_type: str | None
    type: str | None
    start_date: str | None
    start_date_local: str | None
    timezone: str | None
    utc_offset: float | None = None
    distance_m: float | None = None
    distance_mi: float | None = None
    moving_time_s: float | None = None
    elapsed_time_s: float | None = None
    total_elevation_gain_m: float | None = None
    total_elevation_gain_ft: float | None = None
    average_speed_mps: float | None = None
    average_pace_min_per_mi: float | None = None
    max_speed_mps: float | None = None
    average_heartrate: float | None = None
    max_heartrate: float | None = None
    average_cadence: float | None = None
    average_temp: float | None = None
    suffer_score: int | None = None
    perceived_exertion: int | None = None
    calories: float | None = None
    kilojoules: float | None = None
    achievement_count: int | None = None
    kudos_count: int | None = None
    comment_count: int | None = None
    athlete_count: int | None = None
    photo_count: int | None = None
    average_watts: float | None = None
    weighted_average_watts: float | None = None
    device_watts: bool | None = None
    pr_count: int | None = None
    total_photo_count: int | None = None
    has_kudoed: bool | None = None
    location_city: str | None = None
    location_state: str | None = None
    location_country: str | None = None
    commute: bool | None = None
    trainer: bool | None = None
    manual: bool | None = None
    private: bool | None = None
    gear_id: str | None = None
    strava_activity_url: str = ""
    map_summary_polyline: str | None = None
    fetched_detail: bool = False
    weather_conditions: str | None = None

    def to_json_dict(self) -> dict[str, Any]:
        return asdict(self)


class ActivityDataService:
    """Collects activities from Strava (full history) and writes portable JSONL."""

    METERS_PER_MILE = 1609.344
    FEET_PER_METER = 3.28084

    def __init__(
        self,
        auth: StravaAuthService | None = None,
        client: Client | None = None,
    ) -> None:
        self._auth = auth or StravaAuthService()
        self._client = client

    @property
    def client(self) -> Client:
        if self._client is None:
            self._client = self._auth.ensure_token_valid()
        return self._client

    def iter_all_activity_summaries(self, batch_size: int = 200) -> Iterator[Activity]:
        """Oldest-to-newest iterator over every activity in the token's history."""
        before: datetime | None = None
        while True:
            batch: list[Activity] = []
            iterator = self.client.get_activities(before=before, limit=batch_size)
            for activity in iterator:
                batch.append(activity)
            if not batch:
                break
            batch.reverse()
            for activity in batch:
                yield activity
            if len(batch) < batch_size:
                break
            oldest = batch[0].start_date
            if oldest is None:
                break
            before = oldest - timedelta(seconds=1)

    def activity_to_record(
        self,
        activity: Activity,
        *,
        include_map_polyline: bool = False,
        fetch_detail: bool = True,
    ) -> ActivityRecord:
        detail = activity
        got_detail = False
        if fetch_detail and activity.id is not None:
            detail = self.client.get_activity(activity.id, include_all_efforts=False)
            got_detail = True

        distance_m = _quantity_num(detail.distance)
        distance_mi = (
            distance_m / self.METERS_PER_MILE if distance_m is not None else None
        )
        moving_s = _timedelta_seconds(detail.moving_time)
        pace_min_per_mi: float | None = None
        if distance_mi and moving_s and distance_mi > 0:
            pace_min_per_mi = (moving_s / 60.0) / distance_mi

        elev_m = _quantity_num(detail.total_elevation_gain)
        elev_ft = elev_m * self.FEET_PER_METER if elev_m is not None else None

        poly = None
        if include_map_polyline and detail.map and detail.map.summary_polyline:
            poly = detail.map.summary_polyline

        start_date = detail.start_date
        start_local = detail.start_date_local
        start_date_s = (
            start_date.astimezone(timezone.utc).isoformat()
            if isinstance(start_date, datetime)
            else None
        )
        start_local_s = start_local.isoformat() if isinstance(start_local, datetime) else None

        aid = int(detail.id) if detail.id is not None else 0
        url = f"https://www.strava.com/activities/{aid}"

        return ActivityRecord(
            id=aid,
            name=detail.name,
            description=detail.description,
            sport_type=_type_label(detail.sport_type),
            type=_type_label(detail.type),
            start_date=start_date_s,
            start_date_local=start_local_s,
            timezone=detail.timezone,
            utc_offset=detail.utc_offset,
            distance_m=distance_m,
            distance_mi=distance_mi,
            moving_time_s=moving_s,
            elapsed_time_s=_timedelta_seconds(detail.elapsed_time),
            total_elevation_gain_m=elev_m,
            total_elevation_gain_ft=elev_ft,
            average_speed_mps=_quantity_num(detail.average_speed),
            average_pace_min_per_mi=pace_min_per_mi,
            max_speed_mps=_quantity_num(detail.max_speed),
            average_heartrate=detail.average_heartrate,
            max_heartrate=float(detail.max_heartrate)
            if detail.max_heartrate is not None
            else None,
            average_cadence=detail.average_cadence,
            average_temp=float(detail.average_temp)
            if detail.average_temp is not None
            else None,
            suffer_score=detail.suffer_score,
            perceived_exertion=detail.perceived_exertion,
            calories=detail.calories,
            kilojoules=detail.kilojoules,
            achievement_count=detail.achievement_count,
            kudos_count=detail.kudos_count,
            comment_count=detail.comment_count,
            athlete_count=detail.athlete_count,
            photo_count=detail.photo_count,
            average_watts=detail.average_watts,
            weighted_average_watts=float(detail.weighted_average_watts)
            if detail.weighted_average_watts is not None
            else None,
            device_watts=detail.device_watts,
            pr_count=detail.pr_count,
            total_photo_count=detail.total_photo_count,
            has_kudoed=detail.has_kudoed,
            location_city=detail.location_city,
            location_state=detail.location_state,
            location_country=detail.location_country,
            commute=detail.commute,
            trainer=detail.trainer,
            manual=detail.manual,
            private=detail.private,
            gear_id=detail.gear_id,
            strava_activity_url=url,
            map_summary_polyline=poly,
            fetched_detail=got_detail,
            weather_conditions=None,
        )

    def save_records_jsonl(
        self,
        records: list[ActivityRecord],
        path: str,
    ) -> None:
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec.to_json_dict(), ensure_ascii=False) + "\n")

    def collect_all_records(
        self,
        *,
        include_map_polyline: bool = False,
        fetch_detail: bool = True,
        activity_filter: str | None = None,
    ) -> list[ActivityRecord]:
        """
        Pull the complete activity history.

        ``activity_filter`` may be ``\"Run\"`` to only persist runs (checks ``type`` / ``sport_type``).
        """
        records: list[ActivityRecord] = []
        for summary in self.iter_all_activity_summaries():
            if activity_filter:
                st = _type_label(summary.sport_type)
                tp = _type_label(summary.type)
                if st != activity_filter and tp != activity_filter:
                    continue
            records.append(
                self.activity_to_record(
                    summary,
                    include_map_polyline=include_map_polyline,
                    fetch_detail=fetch_detail,
                )
            )
        return records

    def collect_all_to_jsonl(
        self,
        output_path: str,
        *,
        include_map_polyline: bool = False,
        fetch_detail: bool = True,
        activity_filter: str | None = None,
    ) -> int:
        """Pull the complete activity history and write one JSON object per line."""
        records = self.collect_all_records(
            include_map_polyline=include_map_polyline,
            fetch_detail=fetch_detail,
            activity_filter=activity_filter,
        )
        self.save_records_jsonl(records, output_path)
        return len(records)

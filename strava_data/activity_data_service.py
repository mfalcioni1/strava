"""Fetch Strava activities with pagination and normalize to JSON-serializable records."""

from __future__ import annotations

import json
import os
import random
import time
from enum import Enum
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterator, TypeVar

import requests
from stravalib.client import Client
from stravalib.exc import ApplicationInactive, Fault, RateLimitExceeded, RateLimitTimeout
from stravalib.model import DetailedActivity, SummaryActivity

from strava_data.auth_service import StravaAuthService

T = TypeVar("T")

# Strava caps list endpoints at 200 rows per request; larger "limits" imply extra pages
# (multiple HTTP calls) inside one stravalib iterator materialization.
STRAVA_MAX_ACTIVITY_PAGE = 200


class ApiQuotaExceeded(Exception):
    """Raised when Strava signals rate limiting or the caller's request budget is exhausted."""

    pass


@dataclass(frozen=True)
class _RateLimitWindow:
    short_limit: int | None
    daily_limit: int | None
    short_usage: int | None
    daily_usage: int | None

    @property
    def short_exhausted(self) -> bool:
        return (
            self.short_limit is not None
            and self.short_usage is not None
            and self.short_usage >= self.short_limit
        )

    @property
    def daily_exhausted(self) -> bool:
        return (
            self.daily_limit is not None
            and self.daily_usage is not None
            and self.daily_usage >= self.daily_limit
        )


def _quantity_num(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _timedelta_seconds(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, timedelta):
        return value.total_seconds()
    # stravalib 2.x Duration is an int subclass holding seconds
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _timezone_name(value: Any) -> str | None:
    """IANA name (``America/New_York``) rather than Strava's ``(GMT-05:00) America/New_York``."""
    if value is None:
        return None
    if hasattr(value, "timezone"):
        try:
            tz = value.timezone()
        except Exception:
            tz = None
        if tz is not None:
            return str(tz)
    return str(value)


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
    source_filename: str | None = None

    def to_json_dict(self) -> dict[str, Any]:
        return asdict(self)


class ActivityDataService:
    """Collects activities from Strava (full history) and writes portable JSONL."""

    METERS_PER_MILE = 1609.344
    FEET_PER_METER = 3.28084
    MAX_RETRIES = 5
    RETRY_BASE_SECONDS = 2.0
    RETRY_MAX_SECONDS = 120.0
    SHORT_WINDOW_SECONDS = 15 * 60
    RESET_LAG_SECONDS = 120.0
    RESET_RETRY_SECONDS = 30.0

    def __init__(
        self,
        auth: StravaAuthService | None = None,
        client: Client | None = None,
    ) -> None:
        self._auth = auth or StravaAuthService()
        self._client = client
        self._max_api_requests: int | None = None
        self._api_requests_used: int = 0
        self._sync_stop_reason: str | None = None

    def _is_retryable_error(self, exc: Exception) -> bool:
        if isinstance(
            exc,
            (
                RateLimitExceeded,
                RateLimitTimeout,
                requests.exceptions.ConnectionError,
                requests.exceptions.Timeout,
                requests.exceptions.ChunkedEncodingError,
            ),
        ):
            return True
        return isinstance(exc, requests.exceptions.RequestException)

    def _can_make_request(self) -> bool:
        if self._max_api_requests is None:
            return True
        return self._api_requests_used < self._max_api_requests

    def _wait_for_retry(self, operation: str, exc: Exception, attempt: int) -> None:
        retry_after = getattr(exc, "timeout", None)
        backoff = min(
            self.RETRY_MAX_SECONDS,
            self.RETRY_BASE_SECONDS * (2 ** (attempt - 1)),
        )
        wait_s = max(float(retry_after), backoff) if retry_after else backoff
        # Small jitter helps avoid synchronized retries if multiple requests fail together.
        wait_s += random.uniform(0.0, 0.5)
        print(
            f"{operation} failed ({type(exc).__name__}); "
            f"retrying in {wait_s:.1f}s [{attempt}/{self.MAX_RETRIES}]"
        )
        time.sleep(wait_s)

    @staticmethod
    def _parse_rate_limit_pair(value: str | None) -> tuple[int | None, int | None]:
        if not value:
            return (None, None)
        parts = [part.strip() for part in value.split(",", maxsplit=1)]
        if len(parts) != 2:
            return (None, None)
        try:
            return (int(parts[0]), int(parts[1]))
        except ValueError:
            return (None, None)

    @classmethod
    def _extract_rate_limit_windows(cls, response: Any) -> list[_RateLimitWindow]:
        headers = getattr(response, "headers", None) or {}
        windows: list[_RateLimitWindow] = []
        for prefix in ("X-ReadRateLimit", "X-RateLimit"):
            short_limit, daily_limit = cls._parse_rate_limit_pair(
                headers.get(f"{prefix}-Limit")
            )
            short_usage, daily_usage = cls._parse_rate_limit_pair(
                headers.get(f"{prefix}-Usage")
            )
            if any(
                value is not None
                for value in (short_limit, daily_limit, short_usage, daily_usage)
            ):
                windows.append(
                    _RateLimitWindow(
                        short_limit=short_limit,
                        daily_limit=daily_limit,
                        short_usage=short_usage,
                        daily_usage=daily_usage,
                    )
                )
        return windows

    @staticmethod
    def _seconds_until_next_quarter_hour(now: datetime | None = None) -> float:
        now = now or datetime.now(timezone.utc)
        next_minute = ((now.minute // 15) + 1) * 15
        if next_minute >= 60:
            reset_at = now.replace(minute=0, second=0, microsecond=0) + timedelta(
                hours=1
            )
        else:
            reset_at = now.replace(
                minute=next_minute,
                second=0,
                microsecond=0,
            )
        return max((reset_at - now).total_seconds(), 1.0)

    def _wait_for_short_rate_limit(self, operation: str, response: Any, attempt: int) -> None:
        windows = self._extract_rate_limit_windows(response)
        read_window = next(
            (
                window
                for window in windows
                if window.short_exhausted or window.daily_exhausted
            ),
            None,
        )
        until_reset = self._seconds_until_next_quarter_hour()
        if until_reset > self.SHORT_WINDOW_SECONDS - self.RESET_LAG_SECONDS:
            # Strava can report the previous window's usage for a short while after the
            # quarter-hour boundary; a full-window wait here would idle ~15 minutes.
            wait_s = self.RESET_RETRY_SECONDS
        else:
            wait_s = until_reset
        wait_s += random.uniform(0.0, 1.0)
        detail = ""
        if read_window and read_window.short_limit is not None and read_window.short_usage is not None:
            detail = f" ({read_window.short_usage}/{read_window.short_limit} short-term requests used)"
        print(
            f"{operation} hit Strava's 15-minute rate limit{detail}; "
            f"waiting {wait_s:.1f}s for the next window [{attempt}/{self.MAX_RETRIES}]"
        )
        time.sleep(wait_s)

    def _call_with_retries(self, operation: str, fn: Callable[[], T]) -> T:
        if not self._can_make_request():
            raise ApiQuotaExceeded(
                "Request budget exhausted (max_api_requests); partial results were saved."
            )
        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                result = fn()
            except ApplicationInactive as exc:
                raise RuntimeError(
                    "Strava reports this API application as inactive. Since June 30, 2026 "
                    "Standard Tier API access requires a Strava subscription; check "
                    "https://www.strava.com/settings/api"
                ) from exc
            except Fault as exc:
                resp = getattr(exc, "response", None)
                if resp is not None and resp.status_code == 429:
                    windows = self._extract_rate_limit_windows(resp)
                    if any(window.daily_exhausted for window in windows):
                        raise ApiQuotaExceeded(
                            "Strava daily rate limit exhausted; partial results were saved."
                        ) from exc
                    if attempt >= self.MAX_RETRIES:
                        raise ApiQuotaExceeded(
                            "Strava short-term rate limit kept triggering; partial results were saved."
                        ) from exc
                    self._wait_for_short_rate_limit(operation, resp, attempt)
                    continue
                raise
            except ApiQuotaExceeded:
                raise
            except Exception as exc:
                if not self._is_retryable_error(exc) or attempt >= self.MAX_RETRIES:
                    raise
                self._wait_for_retry(operation, exc, attempt)
                continue
            self._api_requests_used += 1
            return result
        raise RuntimeError(f"Unreachable retry loop for operation: {operation}")

    def _fetch_activity_batch(
        self,
        before: datetime | None,
        batch_size: int,
        after: datetime | None = None,
    ) -> list[SummaryActivity]:
        page_limit = min(batch_size, STRAVA_MAX_ACTIVITY_PAGE)

        def _fetch() -> list[SummaryActivity]:
            iterator = self.client.get_activities(
                before=before, after=after, limit=page_limit
            )
            return list(iterator)

        return self._call_with_retries("Fetch activity summaries", _fetch)

    @staticmethod
    def _json_default(value: Any) -> Any:
        """Best-effort serializer for non-primitive values returned by stravalib."""
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc).isoformat()
            return value.astimezone(timezone.utc).isoformat()
        if isinstance(value, timedelta):
            return value.total_seconds()
        if isinstance(value, Enum):
            return value.value
        if hasattr(value, "root"):
            return str(value.root)
        return str(value)

    @property
    def client(self) -> Client:
        if self._client is None:
            self._client = self._auth.ensure_token_valid()
        return self._client

    def iter_all_activity_summaries(
        self, batch_size: int = 200
    ) -> Iterator[SummaryActivity]:
        """Oldest-to-newest iterator over every activity in the token's history."""
        before: datetime | None = None
        page_limit = min(batch_size, STRAVA_MAX_ACTIVITY_PAGE)
        while True:
            if not self._can_make_request():
                self._sync_stop_reason = (
                    self._sync_stop_reason or "request budget exhausted (list fetch)"
                )
                return
            try:
                batch = self._fetch_activity_batch(before, batch_size)
            except ApiQuotaExceeded as exc:
                self._sync_stop_reason = self._sync_stop_reason or str(exc)
                return
            if not batch:
                break
            batch.reverse()
            for activity in batch:
                yield activity
            if len(batch) < page_limit:
                break
            oldest = batch[0].start_date
            if oldest is None:
                break
            before = oldest - timedelta(seconds=1)

    def iter_recent_activity_summaries(
        self, batch_size: int = 200
    ) -> Iterator[SummaryActivity]:
        """Newest-first iterator (see :meth:`Client.get_activities`); stops when history ends."""
        before: datetime | None = None
        page_limit = min(batch_size, STRAVA_MAX_ACTIVITY_PAGE)
        while True:
            if not self._can_make_request():
                self._sync_stop_reason = (
                    self._sync_stop_reason or "request budget exhausted (list fetch)"
                )
                return
            try:
                batch = self._fetch_activity_batch(before, batch_size)
            except ApiQuotaExceeded as exc:
                self._sync_stop_reason = self._sync_stop_reason or str(exc)
                return
            if not batch:
                break
            for activity in batch:
                yield activity
            if len(batch) < page_limit:
                break
            oldest = batch[-1].start_date
            if oldest is None:
                break
            before = oldest - timedelta(seconds=1)

    def iter_activity_summaries_after(
        self, after: datetime, batch_size: int = 200
    ) -> Iterator[SummaryActivity]:
        """
        Oldest-to-newest iterator over activities that started after ``after``.

        Ascending order means a run cut short by the request budget still leaves a
        contiguous block, so the next incremental update resumes without gaps.
        """
        cursor = after if after.tzinfo else after.replace(tzinfo=timezone.utc)
        page_limit = min(batch_size, STRAVA_MAX_ACTIVITY_PAGE)
        while True:
            if not self._can_make_request():
                self._sync_stop_reason = (
                    self._sync_stop_reason or "request budget exhausted (list fetch)"
                )
                return
            try:
                batch = self._fetch_activity_batch(None, batch_size, after=cursor)
            except ApiQuotaExceeded as exc:
                self._sync_stop_reason = self._sync_stop_reason or str(exc)
                return
            if not batch:
                break
            batch.sort(key=lambda a: a.start_date or cursor)
            for activity in batch:
                yield activity
            if len(batch) < page_limit:
                break
            newest = batch[-1].start_date
            if newest is None or newest <= cursor:
                break
            cursor = newest

    def activity_to_record(
        self,
        activity: SummaryActivity | DetailedActivity,
        *,
        include_map_polyline: bool = False,
        fetch_detail: bool = True,
    ) -> ActivityRecord:
        detail: SummaryActivity | DetailedActivity = activity
        got_detail = False
        if fetch_detail and activity.id is not None:
            detail = self._call_with_retries(
                f"Fetch activity detail {activity.id}",
                lambda: self.client.get_activity(
                    activity.id, include_all_efforts=False
                ),
            )
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
            timezone=_timezone_name(detail.timezone),
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

    @staticmethod
    def save_records_jsonl(
        records: list[ActivityRecord],
        path: str,
    ) -> None:
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            for rec in records:
                f.write(
                    json.dumps(
                        rec.to_json_dict(),
                        ensure_ascii=False,
                        default=ActivityDataService._json_default,
                    )
                    + "\n"
                )

    def collect_all_records(
        self,
        *,
        include_map_polyline: bool = False,
        fetch_detail: bool = True,
        activity_filter: str | None = None,
        max_records: int | None = None,
        max_api_requests: int | None = None,
        recent_only: bool = False,
        after: datetime | None = None,
    ) -> list[ActivityRecord]:
        """
        Pull the complete activity history.

        ``activity_filter`` may be ``\"Run\"`` to only persist runs (checks ``type`` / ``sport_type``).

        ``recent_only`` uses newest-first ordering (best match for "latest N activities").

        ``after`` restricts the pull to activities that started after that instant
        (oldest-first); ``recent_only`` is ignored when it is set.

        ``max_api_requests`` caps successful HTTP calls (list pages + per-activity detail fetches).
        """
        return list(
            self.iter_records(
                include_map_polyline=include_map_polyline,
                fetch_detail=fetch_detail,
                activity_filter=activity_filter,
                max_records=max_records,
                max_api_requests=max_api_requests,
                recent_only=recent_only,
                after=after,
            )
        )

    def iter_records(
        self,
        *,
        include_map_polyline: bool = False,
        fetch_detail: bool = True,
        activity_filter: str | None = None,
        max_records: int | None = None,
        max_api_requests: int | None = None,
        recent_only: bool = False,
        after: datetime | None = None,
    ) -> Iterator[ActivityRecord]:
        """Streaming form of :meth:`collect_all_records` (same arguments)."""
        self._sync_stop_reason = None
        self._max_api_requests = max_api_requests
        self._api_requests_used = 0
        produced = 0
        if after is not None:
            iterator = self.iter_activity_summaries_after(after)
        elif recent_only:
            iterator = self.iter_recent_activity_summaries()
        else:
            iterator = self.iter_all_activity_summaries()
        try:
            for summary in iterator:
                if activity_filter:
                    st = _type_label(summary.sport_type)
                    tp = _type_label(summary.type)
                    if st != activity_filter and tp != activity_filter:
                        continue
                if fetch_detail and summary.id is not None:
                    if not self._can_make_request():
                        self._sync_stop_reason = (
                            self._sync_stop_reason
                            or "request budget exhausted before detail fetch"
                        )
                        break
                try:
                    record = self.activity_to_record(
                        summary,
                        include_map_polyline=include_map_polyline,
                        fetch_detail=fetch_detail,
                    )
                except ApiQuotaExceeded as exc:
                    self._sync_stop_reason = self._sync_stop_reason or str(exc)
                    break
                yield record
                produced += 1
                if max_records is not None and produced >= max_records:
                    break
        finally:
            self._max_api_requests = None
            if isinstance(self._auth, StravaAuthService) and self._client is not None:
                self._auth.save_tokens()

    def collect_all_to_jsonl(
        self,
        output_path: str,
        *,
        include_map_polyline: bool = False,
        fetch_detail: bool = True,
        activity_filter: str | None = None,
        max_records: int | None = None,
        max_api_requests: int | None = None,
        recent_only: bool = False,
    ) -> int:
        """Pull activities and write one JSON object per line (partial write on quota stop)."""
        records = self.collect_all_records(
            include_map_polyline=include_map_polyline,
            fetch_detail=fetch_detail,
            activity_filter=activity_filter,
            max_records=max_records,
            max_api_requests=max_api_requests,
            recent_only=recent_only,
        )
        self.save_records_jsonl(records, output_path)
        return len(records)

    @staticmethod
    def load_jsonl_rows(path: str) -> list[dict[str, Any]]:
        if not os.path.isfile(path):
            return []
        with open(path, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    @staticmethod
    def latest_start_date(rows: list[dict[str, Any]]) -> datetime | None:
        latest: datetime | None = None
        for row in rows:
            raw = row.get("start_date")
            if not raw:
                continue
            try:
                dt = datetime.fromisoformat(raw)
            except ValueError:
                continue
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if latest is None or dt > latest:
                latest = dt
        return latest

    @classmethod
    def merge_records_jsonl(
        cls,
        records: list[ActivityRecord],
        path: str,
    ) -> tuple[int, int]:
        """
        Upsert ``records`` into the JSONL at ``path`` by activity id; returns ``(added, updated)``.

        A null in the new record never overwrites an existing value, so fields that only
        the bulk export provides (weather, source file) survive an API refresh.
        Output is newest-first and written atomically.
        """
        by_id: dict[int, dict[str, Any]] = {}
        for row in cls.load_jsonl_rows(path):
            if row.get("id") is not None:
                by_id[int(row["id"])] = row

        added = updated = 0
        for rec in records:
            new_row = json.loads(
                json.dumps(rec.to_json_dict(), ensure_ascii=False, default=cls._json_default)
            )
            old_row = by_id.get(rec.id)
            if old_row is None:
                added += 1
                by_id[rec.id] = new_row
            else:
                updated += 1
                by_id[rec.id] = {
                    **old_row,
                    **{k: v for k, v in new_row.items() if v is not None},
                }

        rows = sorted(
            by_id.values(),
            key=lambda r: r.get("start_date") or "",
            reverse=True,
        )
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp_path = f"{path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        os.replace(tmp_path, path)
        return added, updated

    def update_jsonl(
        self,
        output_path: str,
        *,
        since: datetime | None = None,
        include_map_polyline: bool = False,
        fetch_detail: bool = True,
        activity_filter: str | None = None,
        max_records: int | None = None,
        max_api_requests: int | None = None,
        checkpoint_every: int = 10,
    ) -> tuple[int, int]:
        """
        Fetch activities that started after ``since`` and merge them into ``output_path``.

        ``since`` defaults to the newest ``start_date`` already in the file, so repeated
        calls only spend API requests on activities that are new since the last update.

        Records are merged every ``checkpoint_every`` activities, and whatever was fetched
        is saved if the run stops early (request budget, rate limits, errors, or Ctrl+C).
        A Ctrl+C is reported through the sync stop reason instead of being re-raised.
        """
        if since is None:
            since = self.latest_start_date(self.load_jsonl_rows(output_path))
            if since is None:
                raise ValueError(
                    f"{output_path} has no activities with a start_date to update from; "
                    "pass an explicit since date or run a full sync first."
                )
        added = updated = 0
        pending: list[ActivityRecord] = []

        def flush() -> None:
            nonlocal added, updated
            if not pending:
                return
            new, changed = self.merge_records_jsonl(pending, output_path)
            added += new
            updated += changed
            pending.clear()

        records = self.iter_records(
            include_map_polyline=include_map_polyline,
            fetch_detail=fetch_detail,
            activity_filter=activity_filter,
            max_records=max_records,
            max_api_requests=max_api_requests,
            after=since,
        )
        try:
            for record in records:
                pending.append(record)
                if len(pending) >= checkpoint_every:
                    flush()
        except KeyboardInterrupt:
            self._sync_stop_reason = "interrupted (Ctrl+C); re-run --update to resume"
        finally:
            flush()
        return added, updated

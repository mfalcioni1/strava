"""Normalize a Strava bulk export (activities.csv) into ActivityRecord JSONL."""

from __future__ import annotations

import re
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from strava_data.activity_data_service import ActivityDataService, ActivityRecord


# Official bulk CSV repeats several headers; pandas suffixes the later copies with .1
_METERS_DISTANCE_COL = "Distance.1"
_SECONDS_ELAPSED_COL = "Elapsed Time.1"
_MAX_HR_COL = "Max Heart Rate.1"
_RELATIVE_EFFORT_COL = "Relative Effort.1"
_COMMUTE_COL = "Commute.1"

_DATE_FORMATS = (
    "%b %d, %Y, %I:%M:%S %p",
    "%b %d, %Y, %H:%M:%S",
    "%d %b %Y, %H:%M:%S",
    "%d %b %Y %H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
)

_TZ_RE = re.compile(
    r"^(?:\(GMT(?P<sign>[+-])(?P<hh>\d{2}):(?P<mm>\d{2})\)\s*)?(?P<name>.+)?$"
)

# sport_type (no spaces) → legacy ActivityType used by the API filter
_SPORT_PARENT_TYPE = {
    "TrailRun": "Run",
    "VirtualRun": "Run",
    "VirtualRide": "Ride",
    "MountainBikeRide": "Ride",
    "GravelRide": "Ride",
    "EBikeRide": "Ride",
    "EMountainBikeRide": "Ride",
    "VirtualRow": "Rowing",
    "HighIntensityIntervalTraining": "Workout",
    "Pickleball": "Workout",
    "Pilates": "Workout",
    "Racquetball": "Workout",
    "Badminton": "Workout",
    "TableTennis": "Workout",
    "Tennis": "Workout",
    "Squash": "Workout",
}

_TRAINER_SPORTS = {"VirtualRide", "VirtualRun", "VirtualRow"}


class BulkExportService:
    """Reads Strava's official bulk archive and writes the same JSONL as the API sync."""

    def load_records(
        self,
        source: str | Path,
        *,
        activity_filter: str | None = None,
        max_records: int | None = None,
    ) -> list[ActivityRecord]:
        csv_path, cleanup = self._resolve_csv(Path(source))
        try:
            df = pd.read_csv(csv_path)
        finally:
            if cleanup is not None:
                cleanup.unlink(missing_ok=True)

        records: list[ActivityRecord] = []
        for _, row in df.iterrows():
            rec = row_to_record(row)
            if rec is None:
                continue
            if activity_filter and not _matches_filter(rec, activity_filter):
                continue
            records.append(rec)
            if max_records is not None and len(records) >= max_records:
                break
        return records

    def collect_to_jsonl(
        self,
        source: str | Path,
        output_path: str,
        *,
        activity_filter: str | None = None,
        max_records: int | None = None,
    ) -> int:
        records = self.load_records(
            source,
            activity_filter=activity_filter,
            max_records=max_records,
        )
        ActivityDataService.save_records_jsonl(records, output_path)
        return len(records)

    def _resolve_csv(self, source: Path) -> tuple[Path, Path | None]:
        if not source.exists():
            raise FileNotFoundError(self._missing_csv_message(source))

        if source.is_file() and source.suffix.lower() == ".zip":
            return self._csv_from_zip(source)

        if source.is_file():
            return source, None

        csv_path = _find_activities_csv(source)
        if csv_path is not None:
            return csv_path, None

        zips = sorted(source.glob("*.zip"))
        if len(zips) == 1:
            return self._csv_from_zip(zips[0])

        raise FileNotFoundError(self._missing_csv_message(source))

    def _csv_from_zip(self, zip_path: Path) -> tuple[Path, Path]:
        with zipfile.ZipFile(zip_path) as zf:
            names = [
                name
                for name in zf.namelist()
                if Path(name).name.lower() == "activities.csv"
                and not name.endswith("/")
            ]
            if not names:
                raise FileNotFoundError(
                    f"No activities.csv inside {zip_path}. "
                    "The Strava bulk export zip should include it at the top level."
                )
            names.sort(key=lambda n: (n.count("/"), len(n)))
            extract_to = zip_path.parent / ".activities_csv_extract.csv"
            extract_to.write_bytes(zf.read(names[0]))
            return extract_to, extract_to

    @staticmethod
    def _missing_csv_message(source: Path) -> str:
        return (
            f"No activities.csv found at {source}. "
            "Strava's bulk export zip includes activities.csv next to the "
            "activities/ GPS folder. Copy that CSV into data/ (or pass its path) "
            "and re-run."
        )


def row_to_record(row: pd.Series) -> ActivityRecord | None:
    activity_id = _int(_value(row, "Activity ID", "id"))
    if activity_id is None:
        return None

    sport_label = _str(_value(row, "Activity Type", "Sport Type", "Type"))
    sport_type, activity_type = _sport_and_type(sport_label)

    distance_m = _distance_m(row)
    moving_s = _float(_value(row, "Moving Time"))
    elapsed_s = _float(_value(row, _SECONDS_ELAPSED_COL, "Elapsed Time"))
    distance_mi = (
        distance_m / ActivityDataService.METERS_PER_MILE
        if distance_m is not None
        else None
    )
    pace = None
    if distance_mi and moving_s and distance_mi > 0:
        pace = (moving_s / 60.0) / distance_mi

    elev_m = _float(_value(row, "Elevation Gain"))
    elev_ft = (
        elev_m * ActivityDataService.FEET_PER_METER if elev_m is not None else None
    )

    local_dt, tz_name, utc_offset, start_utc = _parse_start(row)
    if local_dt is not None and start_utc is None and tz_name:
        start_utc, utc_offset = _utc_from_zoneinfo(local_dt, tz_name, utc_offset)
    filename = _str(_value(row, "Filename"))
    commute = _bool(_value(row, _COMMUTE_COL, "Commute"))

    return ActivityRecord(
        id=activity_id,
        name=_str(_value(row, "Activity Name", "Name")),
        description=_str(_value(row, "Activity Description", "Description")),
        sport_type=sport_type,
        type=activity_type,
        start_date=start_utc,
        start_date_local=local_dt.isoformat() if local_dt else None,
        timezone=tz_name,
        utc_offset=utc_offset,
        distance_m=distance_m,
        distance_mi=distance_mi,
        moving_time_s=moving_s,
        elapsed_time_s=elapsed_s,
        total_elevation_gain_m=elev_m,
        total_elevation_gain_ft=elev_ft,
        average_speed_mps=_float(_value(row, "Average Speed")),
        average_pace_min_per_mi=pace,
        max_speed_mps=_float(_value(row, "Max Speed")),
        average_heartrate=_float(_value(row, "Average Heart Rate")),
        max_heartrate=_float(_value(row, _MAX_HR_COL, "Max Heart Rate")),
        average_cadence=_float(_value(row, "Average Cadence")),
        average_temp=_float(
            _value(row, "Average Temperature", "Weather Temperature")
        ),
        suffer_score=_int(_value(row, _RELATIVE_EFFORT_COL, "Relative Effort")),
        perceived_exertion=_int(_value(row, "Perceived Exertion")),
        calories=_float(_value(row, "Calories")),
        kilojoules=_float(_value(row, "Total Work", "Kilojoules")),
        average_watts=_float(_value(row, "Average Watts")),
        weighted_average_watts=_float(_value(row, "Weighted Average Power")),
        commute=commute,
        trainer=sport_type in _TRAINER_SPORTS if sport_type else None,
        manual=not bool(filename),
        gear_id=_str(_value(row, "Activity Gear", "Gear")),
        strava_activity_url=f"https://www.strava.com/activities/{activity_id}",
        fetched_detail=True,
        weather_conditions=_weather_label(row),
        source_filename=filename,
    )


def find_default_source(data_dir: str | Path = "data") -> Path:
    root = Path(data_dir)
    csv_path = _find_activities_csv(root)
    if csv_path is not None:
        return csv_path
    zips = sorted(root.glob("*.zip"))
    if len(zips) == 1:
        return zips[0]
    return root


def _find_activities_csv(root: Path) -> Path | None:
    direct = root / "activities.csv"
    if direct.is_file():
        return direct
    matches = [
        path
        for path in root.rglob("activities.csv")
        if path.is_file() and path.name.lower() == "activities.csv"
    ]
    if not matches:
        return None
    matches.sort(key=lambda p: (len(p.parts), str(p)))
    return matches[0]


def _matches_filter(rec: ActivityRecord, activity_filter: str) -> bool:
    wanted = activity_filter.lower()
    return (rec.type or "").lower() == wanted or (rec.sport_type or "").lower() == wanted


def _distance_m(row: pd.Series) -> float | None:
    meters = _float(_value(row, _METERS_DISTANCE_COL))
    if meters is not None:
        return meters
    raw = _float(_value(row, "Distance"))
    if raw is None:
        return None
    # The first Distance column in the official export is kilometres.
    if raw < 1000:
        return raw * 1000.0
    return raw


def _parse_start(
    row: pd.Series,
) -> tuple[datetime | None, str | None, float | None, str | None]:
    raw_date = _str(_value(row, "Activity Date", "Start Date"))
    local_dt = _parse_datetime(raw_date)
    tz_raw = _str(_value(row, "Timezone"))
    tz_name, utc_offset = _parse_timezone(tz_raw)
    start_utc = None
    if local_dt is not None and utc_offset is not None:
        aware = local_dt.replace(tzinfo=timezone(timedelta(seconds=utc_offset)))
        start_utc = aware.astimezone(timezone.utc).isoformat()
    elif local_dt is not None and local_dt.tzinfo is not None:
        start_utc = local_dt.astimezone(timezone.utc).isoformat()
        local_dt = local_dt.replace(tzinfo=None)
    return local_dt, tz_name, utc_offset, start_utc


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _parse_timezone(value: str | None) -> tuple[str | None, float | None]:
    if not value:
        return None, None
    match = _TZ_RE.match(value.strip())
    if not match:
        return value.strip(), None
    name = (match.group("name") or "").strip() or None
    if name and name.lower() in {"gmt", "utc"}:
        name = None
    utc_offset = None
    if match.group("hh") is not None:
        sign = 1 if match.group("sign") == "+" else -1
        utc_offset = sign * (
            int(match.group("hh")) * 3600 + int(match.group("mm")) * 60
        )
    return name, utc_offset


def _utc_from_zoneinfo(
    local_dt: datetime, tz_name: str, utc_offset: float | None
) -> tuple[str | None, float | None]:
    try:
        from zoneinfo import ZoneInfo

        aware = local_dt.replace(tzinfo=ZoneInfo(tz_name))
    except Exception:
        return None, utc_offset
    start_utc = aware.astimezone(timezone.utc).isoformat()
    offset = aware.utcoffset()
    if offset is not None:
        utc_offset = offset.total_seconds()
    return start_utc, utc_offset


def _sport_and_type(label: str | None) -> tuple[str | None, str | None]:
    if not label:
        return None, None
    sport = "".join(part[:1].upper() + part[1:] for part in re.split(r"[^A-Za-z0-9]+", label) if part)
    parent = _SPORT_PARENT_TYPE.get(sport, sport)
    return sport, parent


def _weather_label(row: pd.Series) -> str | None:
    bits: list[str] = []
    condition = _str(_value(row, "Weather Condition"))
    if condition:
        bits.append(condition)
    temp_c = _float(_value(row, "Weather Temperature"))
    if temp_c is not None:
        bits.append(f"{_c_to_f(temp_c):.0f} F")
    humidity = _float(_value(row, "Humidity"))
    if humidity is not None:
        pct = humidity * 100.0 if humidity <= 1.0 else humidity
        bits.append(f"{pct:.0f}%")
    wind_ms = _float(_value(row, "Wind Speed"))
    if wind_ms is not None:
        bits.append(f"{wind_ms * 2.23694:.0f} mph")
    feels_c = _float(_value(row, "Apparent Temperature"))
    if feels_c is not None:
        bits.append(f"feels {_c_to_f(feels_c):.0f} F")
    return " / ".join(bits) if bits else None


def _c_to_f(temp_c: float) -> float:
    return (temp_c * 9.0 / 5.0) + 32.0


def _value(row: pd.Series, *names: str) -> Any:
    lookup = {str(idx).lower(): idx for idx in row.index}
    for name in names:
        key = lookup.get(name.lower())
        if key is None:
            continue
        val = row[key]
        if val is None or (isinstance(val, float) and pd.isna(val)):
            continue
        if isinstance(val, str) and not val.strip():
            continue
        return val
    return None


def _str(value: Any) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    return text or None


def _float(value: Any) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    number = _float(value)
    if number is None:
        return None
    return int(round(number))


def _bool(value: Any) -> bool | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no"}:
        return False
    return None

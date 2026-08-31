#!/usr/bin/env python3
"""
Build a weekly training calendar spreadsheet (Excel) from archived activity JSONL.

Strava does not expose a combined weather string (temp/humidity/wind/feels like) on the
public activity API; those cells are left blank unless you enrich ``weather_conditions``
in the JSONL yourself.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from strava_data.activity_data_service import ActivityRecord


WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _parse_local_datetime(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(s)


def _pace_mmss(min_per_mi: float | None) -> str:
    if min_per_mi is None or min_per_mi <= 0:
        return ""
    total_sec = int(round(min_per_mi * 60.0))
    m, s = divmod(total_sec, 60)
    return f"{m:d}:{s:02d}/mi"


def _relative_effort(rec: ActivityRecord) -> str:
    if rec.suffer_score is not None:
        return str(rec.suffer_score)
    if rec.perceived_exertion is not None:
        return str(rec.perceived_exertion)
    return ""


def _merge_day_cells(values: list[str]) -> str:
    parts = [v.strip() for v in values if v and str(v).strip()]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return "\n".join(parts)


def _weather_label(rec: ActivityRecord) -> str:
    if rec.weather_conditions and rec.weather_conditions.strip():
        return rec.weather_conditions.strip()
    if rec.average_temp is not None:
        temp_f = (rec.average_temp * 9.0 / 5.0) + 32.0
        return f"{temp_f:.0f} F"
    return ""


@dataclass
class DayBucket:
    miles: list[str] = field(default_factory=list)
    titles: list[str] = field(default_factory=list)
    descriptions: list[str] = field(default_factory=list)
    hrs: list[str] = field(default_factory=list)
    paces: list[str] = field(default_factory=list)
    elevs: list[str] = field(default_factory=list)
    weather: list[str] = field(default_factory=list)
    efforts: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)

    def add(self, rec: ActivityRecord) -> None:
        if rec.distance_mi is not None:
            self.miles.append(f"{rec.distance_mi:.2f}")
        if rec.name:
            self.titles.append(rec.name)
        if rec.description:
            self.descriptions.append(rec.description)
        if rec.average_heartrate is not None:
            self.hrs.append(f"{rec.average_heartrate:.0f}")
        self.paces.append(_pace_mmss(rec.average_pace_min_per_mi))
        if rec.total_elevation_gain_ft is not None:
            self.elevs.append(f"{rec.total_elevation_gain_ft:.0f}")
        weather = _weather_label(rec)
        if weather:
            self.weather.append(weather)
        eff = _relative_effort(rec)
        if eff:
            self.efforts.append(eff)
        if rec.strava_activity_url:
            self.links.append(rec.strava_activity_url)

    def row_values(self) -> dict[str, str]:
        return {
            "Miles": _merge_day_cells(self.miles),
            "Title": _merge_day_cells(self.titles),
            "Description": _merge_day_cells(self.descriptions),
            "Average HR": _merge_day_cells(self.hrs),
            "Average Pace": _merge_day_cells(self.paces),
            "Elevation (ft)": _merge_day_cells(self.elevs),
            "Temp / Humidity / Wind / Feels like": _merge_day_cells(self.weather),
            "Relative effort (Strava suffer / exertion)": _merge_day_cells(self.efforts),
            "Strava activity link": _merge_day_cells(self.links),
        }


def load_records(path: Path) -> list[ActivityRecord]:
    rows: list[ActivityRecord] = []
    allowed = {f.name for f in dataclasses.fields(ActivityRecord)}
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            data: dict[str, Any] = json.loads(line)
            filtered = {k: v for k, v in data.items() if k in allowed}
            rows.append(ActivityRecord(**filtered))
    return rows


def week_key_monday(local_dt: datetime) -> date:
    d = local_dt.date()
    return d - timedelta(days=d.weekday())


def build_calendar_rows(
    records: list[ActivityRecord],
    *,
    activity_types: set[str] | None = None,
) -> list[list[Any]]:
    """
    Rows for a single sheet: repeating 11-row blocks per week (Mon-first columns).
    """
    buckets: dict[tuple[date, int], DayBucket] = defaultdict(DayBucket)
    week_starts: set[date] = set()

    for rec in records:
        if activity_types is not None:
            st = (rec.sport_type or "").lower()
            tp = (rec.type or "").lower()
            if not (st in activity_types or tp in activity_types):
                continue
        local = _parse_local_datetime(rec.start_date_local)
        if local is None:
            continue
        wk = week_key_monday(local)
        week_starts.add(wk)
        wd = local.weekday()
        buckets[(wk, wd)].add(rec)

    all_rows: list[list[Any]] = []
    for wk in sorted(week_starts):
        header = ["Day of Week", *WEEKDAYS]
        all_rows.append(header)
        date_row = ["Date"]
        for i in range(7):
            d = wk + timedelta(days=i)
            date_row.append(d.strftime("%d-%b"))
        all_rows.append(date_row)

        metric_specs = [
            "Miles",
            "Title",
            "Description",
            "Average HR",
            "Average Pace",
            "Elevation (ft)",
            "Temp / Humidity / Wind / Feels like",
            "Relative effort (Strava suffer / exertion)",
            "Strava activity link",
        ]
        for label in metric_specs:
            row = [label]
            for wd in range(7):
                b = buckets.get((wk, wd))
                if b is None:
                    row.append("")
                else:
                    row.append(b.row_values()[label])
            all_rows.append(row)
        all_rows.append([""] * 8)

    if all_rows and all_rows[-1] == [""] * 8:
        all_rows.pop()
    return all_rows


def export_xlsx(
    records: list[ActivityRecord],
    output: Path,
    sheet_name: str = "Training",
    *,
    activity_types: set[str] | None = None,
) -> None:
    rows = build_calendar_rows(records, activity_types=activity_types)
    df = pd.DataFrame(rows)
    output.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name=sheet_name, index=False, header=False)


def main() -> None:
    parser = argparse.ArgumentParser(description="Weekly training calendar export from JSONL.")
    parser.add_argument(
        "input_jsonl",
        nargs="?",
        default="data/activities.jsonl",
        help="Path to activities JSONL from ActivityDataService",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="data/training_calendar.xlsx",
        help="Output .xlsx path",
    )
    parser.add_argument(
        "--runs-only",
        action="store_true",
        help="Only include activities whose type or sport_type is Run",
    )
    args = parser.parse_args()
    in_path = Path(args.input_jsonl)
    if not in_path.is_file():
        raise SystemExit(f"Input not found: {in_path}")

    records = load_records(in_path)
    types = {"run"} if args.runs_only else None
    export_xlsx(records, Path(args.output), sheet_name="Training", activity_types=types)
    print(f"Wrote {args.output} ({len(records)} activities in source file)")


if __name__ == "__main__":
    main()

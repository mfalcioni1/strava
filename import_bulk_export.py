#!/usr/bin/env python3
"""Convert a Strava bulk export into the same JSONL archive as the API sync."""

import argparse
from pathlib import Path
from zoneinfo import ZoneInfo

from tzlocal import get_localzone_name

from strava_data.bulk_export_service import BulkExportService, find_default_source


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Read Strava bulk-export activities.csv (or the export zip) and write "
            "normalized ActivityRecord JSONL."
        )
    )
    parser.add_argument(
        "source",
        nargs="?",
        default=None,
        help="Path to activities.csv, the export directory, or the export zip "
        "(default: data/activities.csv if present, else data/)",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="data/activities.jsonl",
        help="Output JSONL path",
    )
    parser.add_argument(
        "--runs-only",
        action="store_true",
        help="Only persist activities whose type or sport_type is Run",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Stop after persisting N matching activities",
    )
    parser.add_argument(
        "--timezone",
        default=None,
        help="IANA timezone (e.g. America/New_York) for local start times. The export "
        "records start times in UTC without a per-activity timezone "
        "(default: this computer's timezone)",
    )
    args = parser.parse_args()
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit must be a positive integer")
    tz_name = args.timezone or get_localzone_name()
    try:
        ZoneInfo(tz_name)
    except Exception:
        parser.error(f"unknown timezone {tz_name!r}; use an IANA name like America/New_York")

    source = Path(args.source) if args.source else find_default_source()
    svc = BulkExportService(default_timezone=tz_name)
    try:
        n = svc.collect_to_jsonl(
            source,
            args.output,
            activity_filter="Run" if args.runs_only else None,
            max_records=args.limit,
        )
    except FileNotFoundError as exc:
        raise SystemExit(str(exc)) from exc
    print(f"Wrote {n} activities to {args.output} (local times in {tz_name})")


if __name__ == "__main__":
    main()

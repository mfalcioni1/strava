#!/usr/bin/env python3
"""Convert a Strava bulk export into the same JSONL archive as the API sync."""

import argparse
from pathlib import Path

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
    args = parser.parse_args()
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit must be a positive integer")

    source = Path(args.source) if args.source else find_default_source()
    svc = BulkExportService()
    try:
        n = svc.collect_to_jsonl(
            source,
            args.output,
            activity_filter="Run" if args.runs_only else None,
            max_records=args.limit,
        )
    except FileNotFoundError as exc:
        raise SystemExit(str(exc)) from exc
    print(f"Wrote {n} activities to {args.output}")


if __name__ == "__main__":
    main()

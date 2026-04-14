#!/usr/bin/env python3
"""Download full Strava activity history into ``data/activities.jsonl``."""

import argparse

from strava_data import ActivityDataService


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync Strava activities to JSONL on disk.")
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
        "--no-detail",
        action="store_true",
        help="Skip per-activity detail fetch (faster; omits description and some fields)",
    )
    parser.add_argument(
        "--include-map-polyline",
        action="store_true",
        help="Store summary map polylines (larger files)",
    )
    args = parser.parse_args()

    svc = ActivityDataService()
    n = svc.collect_all_to_jsonl(
        args.output,
        include_map_polyline=args.include_map_polyline,
        fetch_detail=not args.no_detail,
        activity_filter="Run" if args.runs_only else None,
    )
    print(f"Wrote {n} activities to {args.output}")


if __name__ == "__main__":
    main()

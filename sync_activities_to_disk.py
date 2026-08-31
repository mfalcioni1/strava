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
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Stop after persisting N matching activities",
    )
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="Quick test mode: defaults to 5 records, no detail, and activities_smoke.jsonl output",
    )
    parser.add_argument(
        "--max-requests",
        type=int,
        default=None,
        help="Max successful Strava HTTP calls (list pages + detail fetches); stops early and saves partial output",
    )
    parser.add_argument(
        "--recent-only",
        action="store_true",
        help="Fetch newest activities first (recommended with --max-requests)",
    )
    args = parser.parse_args()
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit must be a positive integer")
    if args.max_requests is not None and args.max_requests <= 0:
        parser.error("--max-requests must be a positive integer")

    output = args.output
    fetch_detail = not args.no_detail
    limit = args.limit
    if args.smoke_test:
        if limit is None:
            limit = 5
        fetch_detail = False
        if args.output == parser.get_default("output"):
            output = "data/activities_smoke.jsonl"

    svc = ActivityDataService()
    n = svc.collect_all_to_jsonl(
        output,
        include_map_polyline=args.include_map_polyline,
        fetch_detail=fetch_detail,
        activity_filter="Run" if args.runs_only else None,
        max_records=limit,
        max_api_requests=args.max_requests,
        recent_only=args.recent_only,
    )
    tail = f" — {svc._sync_stop_reason}" if svc._sync_stop_reason else ""
    print(f"Wrote {n} activities to {output}{tail}")


if __name__ == "__main__":
    main()

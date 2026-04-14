import os

import pandas as pd

from strava_data import ActivityDataService, StravaAuthService


class StravaManager:
    """Backwards-compatible helper: fetch runs and write the legacy CSV report."""

    def __init__(self) -> None:
        self.env_path = os.path.join(os.path.dirname(__file__), ".env")
        self.data_path = os.path.join(
            os.path.dirname(__file__), "data/running_activities_header.csv"
        )
        self._auth = StravaAuthService(self.env_path)
        self._service = ActivityDataService(self._auth)

    def get_authorization(self) -> str:
        return self._auth.get_authorization()

    def ensure_token_valid(self) -> None:
        self._auth.ensure_token_valid()

    def fetch_running_activities(self) -> None:
        self._auth.ensure_token_valid()
        records = self._service.collect_all_records(activity_filter="Run")
        rows: list[dict] = []
        for rec in records:
            rows.append(
                {
                    "id": rec.id,
                    "name": rec.name,
                    "distance": rec.distance_m,
                    "moving_time": rec.moving_time_s,
                    "elapsed_time": rec.elapsed_time_s,
                    "total_elevation_gain": rec.total_elevation_gain_m,
                    "type": rec.type,
                    "start_date": rec.start_date,
                    "start_date_local": rec.start_date_local,
                    "timezone": rec.timezone,
                    "utc_offset": rec.utc_offset,
                    "location_city": rec.location_city,
                    "location_state": rec.location_state,
                    "location_country": rec.location_country,
                    "achievement_count": rec.achievement_count,
                    "kudos_count": rec.kudos_count,
                    "comment_count": rec.comment_count,
                    "athlete_count": rec.athlete_count,
                    "photo_count": rec.photo_count,
                    "map": rec.map_summary_polyline,
                    "average_speed": rec.average_speed_mps,
                    "max_speed": rec.max_speed_mps,
                    "average_cadence": rec.average_cadence,
                    "average_temp": rec.average_temp,
                    "average_watts": rec.average_watts,
                    "weighted_average_watts": rec.weighted_average_watts,
                    "kilojoules": rec.kilojoules,
                    "device_watts": rec.device_watts,
                    "has_heartrate": rec.average_heartrate is not None,
                    "average_heartrate": rec.average_heartrate,
                    "max_heartrate": rec.max_heartrate,
                    "pr_count": rec.pr_count,
                    "total_photo_count": rec.total_photo_count,
                    "has_kudoed": rec.has_kudoed,
                    "description": rec.description,
                    "strava_activity_url": rec.strava_activity_url,
                }
            )
        os.makedirs(os.path.dirname(self.data_path), exist_ok=True)
        df = pd.DataFrame(rows)
        df.to_csv(self.data_path, index=False)
        print(f"Activities fetched and saved to {self.data_path}")


def main() -> None:
    manager = StravaManager()
    manager.fetch_running_activities()


if __name__ == "__main__":
    main()

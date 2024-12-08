import os
from dotenv import load_dotenv, set_key, unset_key
from stravalib.client import Client
from datetime import datetime
import pandas as pd

class StravaManager:
    def __init__(self):
        self.env_path = os.path.join(os.path.dirname(__file__), '.env')
        self.data_path = os.path.join(os.path.dirname(__file__), 'data/running_activities_header.csv')
        load_dotenv(dotenv_path=self.env_path)
        
        self.client = Client()
        self.client_id = os.getenv('STRAVA_CLIENT_ID')
        self.client_secret = os.getenv('STRAVA_CLIENT_SECRET')
        
    def get_authorization(self):
        """Handle the initial authorization process"""
        url = self.client.authorization_url(
            client_id=self.client_id,
            redirect_uri='http://127.0.0.1:5000/authorization',
            scope=['read_all', 'profile:read_all', 'activity:read_all']
        )
        
        print("Go to the following URL to authenticate:")
        print(url)
        
        auth_code = input("Enter the authorization code from the URL: ")
        set_key(self.env_path, 'STRAVA_AUTH_CODE', auth_code)
        return auth_code

    def ensure_token_valid(self):
        """Ensure we have a valid access token"""
        access_token = os.getenv('STRAVA_ACCESS_TOKEN')
        refresh_token = os.getenv('STRAVA_REFRESH_TOKEN')
        expires_at = os.getenv('STRAVA_EXPIRES_AT')

        if not refresh_token:
            # Initial authorization
            auth_code = self.get_authorization()
            token_response = self.client.exchange_code_for_token(
                client_id=self.client_id,
                client_secret=self.client_secret,
                code=auth_code
            )
        elif expires_at and datetime.now().timestamp() > float(expires_at):
            # Refresh expired token
            token_response = self.client.refresh_access_token(
                client_id=self.client_id,
                client_secret=self.client_secret,
                refresh_token=refresh_token
            )
        else:
            # Token is still valid
            self.client.access_token = access_token
            return

        # Update tokens in .env
        set_key(self.env_path, 'STRAVA_ACCESS_TOKEN', token_response['access_token'])
        set_key(self.env_path, 'STRAVA_REFRESH_TOKEN', token_response['refresh_token'])
        set_key(self.env_path, 'STRAVA_EXPIRES_AT', str(token_response['expires_at']))
        self.client.access_token = token_response['access_token']

    def fetch_running_activities(self):
        """Fetch and save running activities"""
        self.ensure_token_valid()
        
        activities = self.client.get_activities()
        running_activities = []
        for activity in activities:
            if activity.type == 'Run':
                running_activities.append({
                'id': activity.id,
                'name': activity.name,
                'distance': activity.distance.num,
                'moving_time': activity.moving_time.total_seconds(),
                'elapsed_time': activity.elapsed_time.total_seconds(),
                'total_elevation_gain': activity.total_elevation_gain.num,
                'type': activity.type,
                'start_date': activity.start_date,
                'start_date_local': activity.start_date_local,
                'timezone': activity.timezone,
                'utc_offset': activity.utc_offset,
                'location_city': activity.location_city,
                'location_state': activity.location_state,
                'location_country': activity.location_country,
                'achievement_count': activity.achievement_count,
                'kudos_count': activity.kudos_count,
                'comment_count': activity.comment_count,
                'athlete_count': activity.athlete_count,
                'photo_count': activity.photo_count,
                'map': activity.map.summary_polyline,
                'average_speed': activity.average_speed.num,
                'max_speed': activity.max_speed.num,
                'average_cadence': activity.average_cadence,
                'average_temp': activity.average_temp,
                'average_watts': activity.average_watts,
                'weighted_average_watts': activity.weighted_average_watts,
                'kilojoules': activity.kilojoules,
                'device_watts': activity.device_watts,
                'has_heartrate': activity.has_heartrate,
                'average_heartrate': activity.average_heartrate,
                'max_heartrate': activity.max_heartrate,
                'pr_count': activity.pr_count,
                'total_photo_count': activity.total_photo_count,
                'has_kudoed': activity.has_kudoed,
            })
        
        df = pd.DataFrame(running_activities)
        df.to_csv(self.data_path, index=False)
        print("Activities fetched and saved to running_activities_header.csv")

def main():
    manager = StravaManager()
    manager.fetch_running_activities()

if __name__ == "__main__":
    main()
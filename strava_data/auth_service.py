import os
from datetime import datetime

from dotenv import load_dotenv, set_key
from stravalib.client import Client


class StravaAuthService:
    """Keeps a stravalib Client authenticated using tokens in ``.env``."""

    def __init__(self, env_path: str | None = None) -> None:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.env_path = env_path or os.path.join(root, ".env")
        load_dotenv(dotenv_path=self.env_path)

        self.client = Client()
        self.client_id = os.getenv("STRAVA_CLIENT_ID")
        self.client_secret = os.getenv("STRAVA_CLIENT_SECRET")

    def get_authorization(self) -> str:
        url = self.client.authorization_url(
            client_id=self.client_id,
            redirect_uri="http://127.0.0.1:5000/authorization",
            scope=["read_all", "profile:read_all", "activity:read_all"],
        )
        print("Go to the following URL to authenticate:")
        print(url)
        auth_code = input("Enter the authorization code from the URL: ")
        set_key(self.env_path, "STRAVA_AUTH_CODE", auth_code)
        return auth_code

    def ensure_token_valid(self) -> Client:
        access_token = os.getenv("STRAVA_ACCESS_TOKEN")
        refresh_token = os.getenv("STRAVA_REFRESH_TOKEN")
        expires_at = os.getenv("STRAVA_EXPIRES_AT")

        if not refresh_token:
            auth_code = self.get_authorization()
            token_response = self.client.exchange_code_for_token(
                client_id=self.client_id,
                client_secret=self.client_secret,
                code=auth_code,
            )
        elif expires_at and datetime.now().timestamp() > float(expires_at):
            token_response = self.client.refresh_access_token(
                client_id=self.client_id,
                client_secret=self.client_secret,
                refresh_token=refresh_token,
            )
        else:
            self.client.access_token = access_token
            return self.client

        set_key(self.env_path, "STRAVA_ACCESS_TOKEN", token_response["access_token"])
        set_key(self.env_path, "STRAVA_REFRESH_TOKEN", token_response["refresh_token"])
        set_key(self.env_path, "STRAVA_EXPIRES_AT", str(token_response["expires_at"]))
        self.client.access_token = token_response["access_token"]
        return self.client

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

        # Avoid stravalib's DefaultRateLimiter, which can sleep until the next UTC day
        # when the long-term quota is hit (appears as a hang). We handle limits in
        # ActivityDataService (budget + graceful partial writes).
        self.client = Client(rate_limit_requests=False)
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
        return input("Enter the authorization code from the URL: ")

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
            self._configure_client(access_token, refresh_token, expires_at)
            return self.client

        self._configure_client(
            token_response["access_token"],
            token_response["refresh_token"],
            token_response["expires_at"],
        )
        self.save_tokens()
        return self.client

    def _configure_client(
        self,
        access_token: str | None,
        refresh_token: str | None,
        expires_at: str | int | float | None,
    ) -> None:
        # With the refresh token and expiry set, stravalib refreshes an access token that
        # expires mid-run (tokens last 6 hours) instead of failing with 401s.
        self.client.access_token = access_token
        self.client.refresh_token = refresh_token
        self.client.token_expires = int(float(expires_at)) if expires_at else None

    def save_tokens(self) -> None:
        """Write the client's current tokens to ``.env`` if they changed (e.g. auto-refresh)."""
        values = {
            "STRAVA_ACCESS_TOKEN": self.client.access_token,
            "STRAVA_REFRESH_TOKEN": self.client.refresh_token,
            "STRAVA_EXPIRES_AT": (
                str(self.client.token_expires) if self.client.token_expires else None
            ),
        }
        for key, value in values.items():
            if value and os.getenv(key) != value:
                set_key(self.env_path, key, value)
                os.environ[key] = value

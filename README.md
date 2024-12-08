# Access Strava
One-time set-up. First create [a Strava App](https://medium.com/analytics-vidhya/accessing-user-data-via-the-strava-api-using-stravalib-d5bee7fdde17), and add the client ID and client secret to the `.env.sample` file and rename it to `.env`.

```py
from stravalib import Client
import os
from dotenv import load_dotenv

load_dotenv()

client_id = os.getenv('STRAVA_CLIENT_ID')
client_secret = os.getenv('STRAVA_CLIENT_SECRET')

client = Client()

url = client.authorization_url(client_id=client_id, 
                               redirect_uri='http://127.0.0.1:5000/authorization', 
                               scope=['read_all','profile:read_all','activity:read_all'])

print(url)
```
Running this generates a clickable URL that will redirect and show the access code. Add the access code to the `.env` file.
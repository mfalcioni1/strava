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

## Sync activities to disk

Use the sync utility to export activity records as JSONL:

`python sync_activities_to_disk.py --runs-only`

For a quick smoke test (no detail fetch, only 5 records by default):

`python sync_activities_to_disk.py --smoke-test`

You can also control sample size explicitly:

`python sync_activities_to_disk.py --runs-only --limit 10 --no-detail`

To stay within a daily API request budget (each list page and each detail fetch counts as one call), fetch **newest activities first** and cap calls — partial results are still written if the limit is hit:

`python sync_activities_to_disk.py --recent-only --max-requests 1000 --runs-only`

## Import a Strava bulk export

The official archive (Settings → My Account → Download or Delete Your Account) includes `activities.csv` plus an `activities/` folder of GPX/FIT/TCX files. Place them under `data/` like this:

```
data/activities.csv
data/activities/1234567890.fit.gz
```

Then convert the CSV into the same JSONL records the API sync writes (descriptions, relative effort, and weather when present):

`python import_bulk_export.py`

`python import_bulk_export.py --runs-only`

`python import_bulk_export.py path/to/export.zip -o data/activities.jsonl`

After that, the calendar export is unchanged:

`python export_training_calendar.py --runs-only`

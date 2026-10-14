# Access Strava
One-time set-up. First create [a Strava App](https://medium.com/analytics-vidhya/accessing-user-data-via-the-strava-api-using-stravalib-d5bee7fdde17), and add the client ID and client secret to the `.env.sample` file and rename it to `.env`.

Since June 30, 2026, Standard Tier API access requires an active **Strava subscription**. Check your tier and athlete capacity at [strava.com/settings/api](https://www.strava.com/settings/api). If the subscription lapses, API calls fail with an "application inactive" error. The bulk export below still works without one.

Install dependencies (Python 3.11+ for `stravalib` 2.x):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

On the first API run (no `STRAVA_REFRESH_TOKEN` in `.env` yet), the sync prints an authorization URL. Open it, approve access, and paste the `code` value from the redirect URL (`http://127.0.0.1:5000/authorization?...&code=...`) at the prompt. The access and refresh tokens are then saved to `.env` and refreshed automatically on later runs.

## Sync activities to disk

Use the sync utility to export activity records as JSONL:

`python sync_activities_to_disk.py --runs-only`

**Without `--update`, the sync overwrites the output file** with whatever it fetched in that run.

For a quick smoke test (no detail fetch, only 5 records by default):

`python sync_activities_to_disk.py --smoke-test`

You can also control sample size explicitly:

`python sync_activities_to_disk.py --runs-only --limit 10 --no-detail`

To stay within a daily API request budget (each list page and each detail fetch counts as one call), fetch **newest activities first** and cap calls — partial results are still written if the limit is hit:

`python sync_activities_to_disk.py --recent-only --max-requests 1000 --runs-only`

## Keep the archive current

`--update` merges into the existing file instead of overwriting it, and only fetches activities that started after the newest one already stored:

`python sync_activities_to_disk.py --update --runs-only --max-requests 900`

New activities are fetched oldest-first and saved every 10 activities, so if the request cap or rate limit is hit, or you press Ctrl+C, just re-run the same command later (the daily limit resets at midnight UTC) and it resumes where it stopped. Records are matched by activity id; a value missing from the API (for example bulk-export weather) never overwrites one already in the file.

To re-pull a window (edited activities, late uploads), pass an explicit start date:

`python sync_activities_to_disk.py --update --since 2026-04-01 --runs-only`

## Import a Strava bulk export

The official archive (Settings → My Account → Download or Delete Your Account) includes `activities.csv` plus an `activities/` folder of GPX/FIT/TCX files. It is free and covers your full history. Place them under `data/` like this:

```
data/activities.csv
data/activities/1234567890.fit.gz
```

Then convert the CSV into the same JSONL records the API sync writes (descriptions, relative effort, and weather when present):

`python import_bulk_export.py`

`python import_bulk_export.py --runs-only`

`python import_bulk_export.py path/to/export.zip -o data/activities.jsonl`

The archive records start times in UTC with no per-activity timezone, so local times (used for calendar days) are converted with your computer's timezone. Pass `--timezone America/Chicago` (any IANA name) to override. Activities recorded while traveling keep the correct date in nearly all cases but show home-zone clock times.

The import rewrites the output file from the archive. Afterwards, `sync_activities_to_disk.py --update` can top it up from the API.

After that, the calendar export is unchanged:

`python export_training_calendar.py --runs-only`

## Tests

`python -m unittest discover -s tests`

## Strava API changes to track

From the [Strava API changelog](https://developers.strava.com/docs/changelog/):

- **Jun 30, 2026**: Standard Tier API access requires a Strava subscription.
- **Jun 1, 2026**: optional self-upgrade in the API settings dashboard to 10 athletes and higher limits (read: 200 / 15 min, 2,000 / day; default is 100 / 1,000).
- **Sep 1, 2026**: Club activities/members/admins endpoints removed and segment explore restricted (not used here).
- **Jan 4, 2027**: new base URL `https://api-v3.strava.com` becomes available.
- **Jun 1, 2027**: tokens must be sent in request headers, the old base URL is retired, and `/oauth/deauthorize` is replaced by `/oauth/revoke`. `stravalib` 2.6 already sends tokens in headers; upgrade it once a release targets the new base URL.

# MBR Dashboard

Monthly Business Review (MBR) Dashboard — pulls live data from HubSpot CRM
and renders it as a standalone Windows desktop app (pywebview + Flask),
or as a local web app for development.

Used by internal Sales leadership across **SMB**, **AM** (Account Management),
and **ENT** (Enterprise) teams.

## Features

- Per-month overview with KPI cards (Revenue, Closed Won, Target, Attainment %,
  Open Pipeline, Avg Deal Size, Avg Deal Age, Opp→Win %, MQL Assigned, Deals Lost)
- Team-level breakdown (SMB / AM / ENT) with click-through to rep detail
- Rep-level views with:
  - Activity Goals vs Actuals (calls, emails, talk time, % achieved)
  - MQL → Revenue Conversion Funnel (all reps in team, current rep highlighted)
  - Rolling 90-Day Funnel (per rep + team total)
  - Trailing 3-Month Revenue + MQL trend (with goal overlay + achievement % for AM reps)
  - Closed Won by Country breakdown
  - Lost Reasons categorized (Budget / Lost to competitor / Project Cancelled etc.)
  - Deal-level audit insights with explicit deal names
  - Deals tabs (Closed Won / Open Pipeline / Deals Lost) with country + lost reason
- Drilldowns for Closed Won, Deals Lost, Open Pipeline, Avg Deal Age
- AM-specific Account Coverage + Deal Type Breakdown panels
- SMB Unqualified MQLs with expandable rows showing reason text per contact
- ENT shown separately as a quarterly panel
- Sync button for fresh fetch from HubSpot
- Month dropdown — current + previous months

## Quick start (Windows .exe)

1. Build the exe (one-time):

   ```
   build_exe.bat
   ```

2. Edit `Release\.env` and set your HubSpot Private App token:

   ```
   HUBSPOT_TOKEN=pat-na1-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
   ```

3. Double-click `Release\MBR_Dashboard.exe` — opens in its own native window,
   no browser, no console.

## Quick start (dev / from source)

```bash
pip install -r requirements.txt
cp .env.example .env       # then edit HUBSPOT_TOKEN
python app.py              # opens at http://localhost:5000
```

## Tech stack

- **Backend:** Python 3.x, Flask, requests
- **Frontend:** Vanilla HTML/CSS/JS (no framework)
- **Desktop wrapper:** pywebview (Edge Chromium WebView2 on Windows)
- **Packaging:** PyInstaller (--onefile --windowed)
- **Data source:** HubSpot CRM REST API (`api.hubapi.com`)

## Configuration

Edit `.env` (next to the .exe in `Release\` or next to `app.py` for dev mode):

| Variable | Required | Default | Description |
|---|---|---|---|
| `HUBSPOT_TOKEN` | yes | — | HubSpot Private App access token |
| `MBR_PORT` | no | `5000` | Embedded Flask server port |
| `MBR_HOST` | no | `127.0.0.1` | Bind address (keep loopback for security) |

### Required HubSpot scopes

When creating the Private App in HubSpot, enable these read scopes:

- `crm.objects.deals.read`
- `crm.objects.companies.read`
- `crm.objects.contacts.read`
- `crm.schemas.deals.read`
- `crm.lists.read`
- `settings.users.teams.read`
- `sales-email-read` *(optional — needed to show email content in the
  closed-lost deal insights timeline)*

## Configuration in code (`app.py`)

Team rosters + per-rep monthly targets live in the `TARGETS` dict near the
top of `app.py`. Activity goals (calls / emails / talk time) are in
`REP_ACTIVITY_DATA`. AM-specific revenue goals for the trend chart are in
`REP_MONTHLY_REVENUE_GOALS`.

## Security notes

- All processing is on the user's local machine over loopback (`127.0.0.1`)
- No data is sent anywhere except outbound to HubSpot's API
- The HubSpot token sits in a local `.env` file — keep it out of version
  control (`.gitignore` already excludes `.env`)
- The .exe is unsigned — Windows SmartScreen may warn the first time;
  click "More info → Run anyway"

## Project structure

```
MBR_Dashboard tool/
├── app.py              # Flask app + HubSpot client + aggregation
├── launcher.py         # pywebview wrapper for the standalone exe
├── build_exe.bat       # One-click PyInstaller build
├── requirements.txt
├── .env.example        # Template — copy to .env and fill in
├── templates/
│   └── index.html      # Single-page dashboard
├── static/
│   ├── style.css
│   └── script.js
└── Release/            # Built artifacts (not in git)
    ├── MBR_Dashboard.exe
    ├── .env
    └── README.txt
```

## License

Internal use only.

MBR Dashboard
=============

A standalone Windows tool that fetches live data from HubSpot and renders a
Monthly Business Review dashboard for the SMB, AM, and ENT teams.

What's in this folder
---------------------
  MBR_Dashboard.exe   Double-click to launch the dashboard.
  .env                Configuration — open with Notepad to change settings.
  README.txt          You're reading it.

First-time setup
----------------
1. Open ".env" in Notepad.
2. Paste your HubSpot Private App token after  HUBSPOT_TOKEN=
   (the token must have these scopes:
     crm.objects.deals.read
     crm.objects.contacts.read
     crm.objects.companies.read
     crm.objects.owners.read
     crm.schemas.deals.read
     settings.users.teams.read )
3. Save and close the file.

Running the dashboard
---------------------
1. Double-click  MBR_Dashboard.exe
2. A black console window opens — leave it open while you use the dashboard.
3. Your default browser launches automatically at  http://localhost:5000
4. Use the "Sync" button in the top-right to refresh live data from HubSpot.
   First load takes about 2 minutes (HubSpot batches are slow). After that,
   switching months and clicking around is near-instant.

To stop the dashboard
---------------------
Close the black console window (or press Ctrl+C inside it).

If port 5000 is taken
---------------------
Open ".env" and change  MBR_PORT=5000  to a different number such as 5050,
then relaunch.

Troubleshooting
---------------
- "MBR_Dashboard.exe" blocked by Windows Defender SmartScreen:
    Click "More info" -> "Run anyway". The file is unsigned because it's an
    internal tool.
- Browser shows "site can't be reached":
    Wait ~10 seconds after launch for the server to come up, then refresh.
- KPIs show all zeros / "ERR HubSpot API error 401":
    Your token in .env is missing or has expired. Generate a fresh one in
    HubSpot (Settings -> Integrations -> Private Apps) and paste it back.

Distribution
------------
Send the entire folder (.exe + .env + README.txt) as a zip. Recipients only
need Windows 10+; no Python install is required.

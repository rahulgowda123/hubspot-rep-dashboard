"""
MBR Dashboard - HubSpot Live Data
Fetches real-time data from HubSpot Sales Pipeline and computes KPIs
for SMB, AM, and ENT teams.
"""
from flask import Flask, jsonify, render_template, request
import requests
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import os
import re
import json
import html as html_lib

# Shared HTTP session — reuses TCP connections + HTTP keep-alive across
# all HubSpot calls. Dramatically reduces per-request overhead.
SESSION = requests.Session()
SESSION.headers.update({"Content-Type": "application/json"})
# Bigger connection pool so parallel workers don't wait on each other.
_adapter = requests.adapters.HTTPAdapter(pool_connections=32, pool_maxsize=32)
SESSION.mount("https://", _adapter)
SESSION.mount("http://", _adapter)

# Concurrency for parallel batch reads. HubSpot's daily rate limit is
# 250k/day (~170 req/s max) but per-second burst is capped around 100/10s
# for search; 6-8 concurrent workers keeps us well under that.
BATCH_WORKERS = 8

def _load_dotenv_inline():
    """Best-effort .env loader so `python app.py` (dev mode) finds the token
    without needing python-dotenv installed. The PyInstaller launcher also
    loads .env before importing app, this is the dev-mode fallback."""
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(env_path):
        return
    try:
        with open(env_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key, val = key.strip(), val.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val
    except Exception:
        pass


_load_dotenv_inline()
ACCESS_TOKEN = os.environ.get("HUBSPOT_TOKEN", "").strip()
if not ACCESS_TOKEN:
    print("[warn] HUBSPOT_TOKEN is not set. "
          "Add it to the .env file sitting next to the .exe / app.py.")

HEADERS = {
    "Authorization": f"Bearer {ACCESS_TOKEN}",
    "Content-Type": "application/json",
}

# Individual rep targets in USD (per month).
# Keys must be a substring of the HubSpot owner's display name (case-insensitive).
TARGETS = {
    "SMB": {
        "Vicky": 50000,
        "Yogesh Vig": 35000,    # disambiguates from "Yogesh Talurmath"
        "Kritika": 35000,
        "Rutuja": 25000,
        "Lennis": 50000,        # moved from ENT
        "Divyansh": 10000,
        "Aparajit": 10000,      # HubSpot owner: "Aparajit Jha"
        "Sutheerth": 0,         # New rep — no monthly target yet
    },
    "AM": {
        "Joy": 40000,
        "Arundhati": 35000,
        "Deepak R": 25000,      # disambiguates "Deepak R J" from archived Deepaks
    },
    # ENT runs on a quarterly target (not monthly).
    "ENT": {
        "Anthony": 1000000,
    },
}

# Stage labels excluded from the Open Pipeline KPI (per spec)
OPEN_PIPELINE_EXCLUDED = {"closed won", "closed lost", "unresponsive", "on hold"}

RAW_CACHE = {"data": None, "timestamp": 0}
MONTH_CACHE = {}                # {month_key: {"data": ..., "timestamp": ...}}
CACHE_TTL_SECONDS = 300

# Resolve template/static directories — picked up from env when frozen (.exe),
# falls back to Flask's default ("templates" / "static") otherwise.
_tpl_dir = os.environ.get("MBR_TEMPLATE_FOLDER") or "templates"
_static_dir = os.environ.get("MBR_STATIC_FOLDER") or "static"
app = Flask(__name__, template_folder=_tpl_dir, static_folder=_static_dir)


# ----- HubSpot helpers --------------------------------------------------------

def hs_get(url, params=None):
    r = SESSION.get(url, headers=HEADERS, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def hs_post(url, payload):
    r = SESSION.post(url, headers=HEADERS, json=payload, timeout=30)
    r.raise_for_status()
    return r.json()


def _parallel_batch_read(url, ids, properties, workers=BATCH_WORKERS):
    """Run HubSpot batch/read in parallel chunks of 100. Returns the merged
    `results` list. Silently skips chunks that error so a single 400/429
    doesn't blow up the whole fetch."""
    if not ids:
        return []
    ids = list({str(x) for x in ids})
    chunks = [ids[i:i + 100] for i in range(0, len(ids), 100)]

    def _one(chunk):
        try:
            return hs_post(url, {"properties": properties,
                                 "inputs": [{"id": c} for c in chunk]}).get("results", [])
        except requests.HTTPError:
            return []

    out = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for res in ex.map(_one, chunks):
            out.extend(res)
    return out


def _parallel_batch_assoc_read(url, ids, workers=BATCH_WORKERS):
    """Same as _parallel_batch_read but for v4 batch association reads
    (no `properties` payload — just inputs)."""
    if not ids:
        return []
    ids = list({str(x) for x in ids})
    chunks = [ids[i:i + 100] for i in range(0, len(ids), 100)]

    def _one(chunk):
        try:
            return hs_post(url, {"inputs": [{"id": c} for c in chunk]}).get("results", [])
        except requests.HTTPError:
            return []

    out = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for res in ex.map(_one, chunks):
            out.extend(res)
    return out


def fetch_pipelines():
    return hs_get("https://api.hubapi.com/crm/v3/pipelines/deals").get("results", [])


# ==== Static MBR config (manual observations from review) ===================

# Rep auditing notes — manual audit observations to surface in the rep view.
REP_AUDIT_NOTES = {
    # SMB
    "Vicky": [
        "3 client calls without video",
        "Acknowledgment received",
    ],
    "Yogesh": [
        "7 client calls without video",
        "Remained in a meeting for ~1 hour despite the prospect not joining",
        "Follow-up task gaps noted in the last QBR continue to persist in this MBR",
        "Acknowledged and responded to the audit report",
    ],
    "Deepak": [
        "Gaps in follow-up task management",
        "No acknowledgment or response received on the audit report",
    ],
    "Rutuja": [
        "No follow-up tasks created",
        "Notes are not updated",
    ],
    # AM
    "Joy": [
        "No next follow-up task scheduled",
        "Last contacted is beyond 5 days",
    ],
    "Vivin": [
        "Forecast category is not updated",
        "No next follow-up task scheduled",
    ],
    "Arundhati": [
        "No next follow-up task scheduled",
        "Last contacted is beyond 5 days",
        "No acknowledgment on the audit reports",
    ],
}

# Call / Email / Talk-time goals & actuals for the MBR period.
REP_ACTIVITY_DATA = {
    # SMB — May 2026 activity goals vs actuals
    "Rutuja":     {"call_goal": 1140, "call_actual": 1328, "email_goal": 760, "email_actual": 1161, "talk_goal": 1920, "talk_actual": 1344},
    "Kritika":    {"call_goal":  900, "call_actual":  854, "email_goal": 600, "email_actual":  725, "talk_goal": 1560, "talk_actual": 1122},
    "Vicky":      {"call_goal":  960, "call_actual":  663, "email_goal": 640, "email_actual":  768, "talk_goal": 1680, "talk_actual":  672},
    "Yogesh Vig": {"call_goal": 1080, "call_actual":  894, "email_goal": 720, "email_actual":  511, "talk_goal": 1920, "talk_actual":  852},
    "Aparajit":   {"call_goal":  720, "call_actual":  766, "email_goal": 480, "email_actual":  499, "talk_goal":  960, "talk_actual":  192},
    "Divyansh":   {"call_goal":  660, "call_actual":  827, "email_goal": 440, "email_actual":  219, "talk_goal":  840, "talk_actual":  118},
    "Lennis":     {"call_goal": 1020, "call_actual":  319, "email_goal": 680, "email_actual":  640, "talk_goal": 1560, "talk_actual": 1078},
    "Sutheerth":  {"call_goal":  300, "call_actual":  244, "email_goal": 200, "email_actual":   48, "talk_goal":  360, "talk_actual":   72},
    # AM — days_worked removed per request
    "Deepak R":   {"call_goal":  850, "call_actual":  558, "email_goal": 450, "email_actual":  365, "talk_goal": 1920, "talk_actual": 1158},
    "Arundhati":  {"call_goal":  975, "call_actual":  793, "email_goal": 825, "email_actual":  478, "talk_goal": 2520, "talk_actual": 1971.7},
    "Joy":        {"call_goal":  655, "call_actual":   87, "email_goal": 565, "email_actual":  189, "talk_goal": 1680, "talk_actual": 1040},
}

# AM revenue goals per month — used to overlay goal + achieved % on the
# trailing-3-month revenue trend chart in each rep's view.
REP_MONTHLY_REVENUE_GOALS = {
    "Arundhati": {"2026-04": 30000, "2026-05": 35000},
    "Joy":       {"2026-04": 30000, "2026-05": 40000},
}

# Closed-lost reason categorizer. Order matters — first match wins.
# Empty / blank reasons go to "(unspecified)".
LOST_REASON_CATEGORIES = (
    # Order matters — earlier categories win when multiple keywords are present.
    ("Project Cancelled",           [
        "project cancel", "cancel the project", "cancelled the project",
        "cancellation", "called off", "scrapped", "shelved",
        "put on hold", "deprioritized", "deprioritised", "paused",
        "no longer pursuing the project", "abandoned the project",
    ]),
    ("Budget Issue",                [
        "budget", "expensive", "pricing", "cost", "funding", "afford",
        "too high", "out of budget", "no budget", "price", "cheaper",
        "more cost effective", "cost-effective", "cost effective",
        "pricing falls outside", "outside our budget", "couldn't justify",
        "value add is not enough", "not enough value", "rfp", "quote was high",
        "max available funding", "funds", "spend", "below our threshold",
    ]),
    ("Lost to competitor",          [
        "competitor", "went with", "moved with", "decided to go with",
        "another vendor", "another solution", "another tool", "another provider",
        "highview", "chose", "selected", "going with", "signed with",
        "moved forward with", "preferred vendor", "alternative solution",
        "different vendor", "different provider", "with a third party",
    ]),
    ("Want Tool for themselves",    [
        "themselves", "ourselves", "manual", "internally", "in-house",
        "own internal", "handle it", "doing it ourselves", "manage it themselves",
        "manage this internally", "decided to manage", "handled it because",
        "their own team", "diy", "do it manually", "internal team",
        "we'll handle", "we will handle", "operate ourselves",
    ]),
    ("Product/Feature Limitation",  [
        "feature", "doesn't support", "missing", "limitation", "not supported",
        "lacking", "couldn't migrate", "unable to migrate", "compatibility",
        "didn't fit", "doesn't fit", "didn't meet", "doesn't meet",
        "requirements", "use case", "limitations", "scope of work",
    ]),
    ("Migration Time",              [
        "migration time", "takes too long", "timeline", "too long",
        "duration", "time-consuming", "slow", "tprm process took time",
        "soc2", "iso", "compliance took", "time to return",
    ]),
    ("Unresponsive",                [
        "no response", "stopped responding", "unresponsive", "ghosted",
        "didn't respond", "did not respond", "no reply", "no follow",
        "lost contact", "couldn't reach", "could not reach",
        "non responsive", "non-responsive",
    ]),
    ("Not interested",              [
        "not interested", "no longer", "decided not", "don't need",
        "won't proceed", "not pursuing", "no follow up", "no follow-up",
        "do not need", "wasn't a fit", "was not a fit", "not a priority",
        "we're all good", "we are all good", "if things change",
        "if needed in the future", "reach out if we need", "will reach out if",
        "no need at this time", "good for now",
    ]),
)


def classify_lost_reason(text):
    """Bucket a free-text closed-lost reason into one of the standard
    categories shown on the report. Anything with text but no keyword match
    falls into 'Not interested' (the broadest customer-disengagement bucket)
    so the report never shows an 'Other' pile."""
    s = (str(text) if text else "").strip()
    if not s:
        return "(unspecified)"
    sl = s.lower()
    for cat, kws in LOST_REASON_CATEGORIES:
        for kw in kws:
            if kw in sl:
                return cat
    # Light heuristic on remaining unclassified text
    if any(w in sl for w in ("future", "later", "next year", "next quarter", "revisit")):
        return "Project Cancelled"
    if any(w in sl for w in ("vendor", "tool", "solution")):
        return "Lost to competitor"
    return "Not interested"


# Backward-compat alias used elsewhere in the codebase
classify_lost_theme = classify_lost_reason


def group_lost_reasons(lost_deals):
    """Group a list of closed-lost deal records by category. Each entry has
    {reason, count, pct, items[]} ready for display."""
    groups = defaultdict(list)
    for d in lost_deals:
        raw = (d.get("lost_reason") or "").strip()
        cat = classify_lost_reason(raw)
        groups[cat].append({
            "description": raw or "(no reason recorded)",
            "owner": d.get("owner") or "-",
            "rep": d.get("rep") or "-",
            "deal_name": d.get("name") or "-",
            "amount": d.get("amount") or 0,
            "close_date": d.get("close_date") or "",
        })
    total = sum(len(v) for v in groups.values())
    return sorted([
        {
            "reason": cat,
            "count": len(items),
            "pct": (len(items) / total * 100) if total else 0.0,
            "items": sorted(items, key=lambda x: -(x.get("amount") or 0)),
        } for cat, items in groups.items()
    ], key=lambda x: -x["count"])


# Display labels for HubSpot's `dealtype` property values
DEAL_TYPE_LABELS = {
    "newbusiness": "New Business",
    "existingbusiness": "Existing Business",
    "additional server": "Additional Server",
    "cross-sell": "Cross-sell",
    "overage": "Overage",
    "upsell": "Upsell",
}

# Fixed column order for the AM deal-type breakdown (matches the report layout)
AM_DEAL_TYPE_COLUMNS = [
    "Additional Server",
    "Cross-sell",
    "Existing Business",
    "New Business",
    "Overage",
    "Upsell",
]


def normalize_deal_type(raw):
    """Map a HubSpot `dealtype` value to its display label."""
    if not raw:
        return ""
    s = str(raw).strip()
    return DEAL_TYPE_LABELS.get(s.lower(), s)


# HubSpot team-name → our team key. Substring match is case-insensitive.
HUBSPOT_TEAM_NAME_MAP = (
    ("smb", "SMB"),
    ("account management", "AM"),
    ("am team", "AM"),
    ("enterprise", "ENT"),
    ("ent team", "ENT"),
    ("large msp", "ENT"),
)


def fetch_hubspot_team_id_to_name():
    """Returns {hubspot_team_id: 'SMB'|'AM'|'ENT'} by mapping team labels."""
    try:
        data = hs_get("https://api.hubapi.com/settings/v3/users/teams")
    except requests.HTTPError:
        return {}
    out = {}
    for t in data.get("results", []):
        team_id = str(t.get("id") or "")
        name = (t.get("name") or "").strip().lower()
        if not team_id or not name:
            continue
        for needle, mapped in HUBSPOT_TEAM_NAME_MAP:
            if needle in name:
                out[team_id] = mapped
                break
    return out


def fetch_owners(archived=False):
    """Fetch HubSpot owners. Pass archived=True to retrieve deactivated owners."""
    url = "https://api.hubapi.com/crm/v3/owners"
    owners, after = [], None
    while True:
        params = {"limit": 100, "archived": "true" if archived else "false"}
        if after:
            params["after"] = after
        data = hs_get(url, params)
        owners.extend(data.get("results", []))
        nxt = data.get("paging", {}).get("next")
        if not nxt:
            break
        after = nxt.get("after")
    return owners


def fetch_deals_for_pipeline(pipeline_id):
    """Fetch all deals for the given pipeline. Tries to include country and
    closed-lost-reason properties; falls back gracefully if they don't exist."""
    base_props = [
        "dealname", "amount", "dealstage", "closedate",
        "createdate", "hubspot_owner_id", "pipeline",
        "closed_lost_reason",
        "notes_last_updated",
        "dealtype",
        "hs_discount_amount",
        "hs_acv",
    ]
    extra_property_sets = [
        ["country", "hs_country_region_code", "deal_country"],
        ["country", "hs_country_region_code"],
        ["country"],
        [],
    ]
    last_err = None
    for extras in extra_property_sets:
        try:
            return _do_fetch_deals(pipeline_id, base_props + extras)
        except requests.HTTPError as e:
            last_err = e
            continue
    if last_err:
        raise last_err
    return []


def _do_fetch_deals(pipeline_id, properties):
    url = "https://api.hubapi.com/crm/v3/objects/deals/search"
    base_payload = {
        "filterGroups": [{
            "filters": [{
                "propertyName": "pipeline",
                "operator": "EQ",
                "value": pipeline_id,
            }]
        }],
        "properties": properties,
        "limit": 100,
        "sorts": [{"propertyName": "createdate", "direction": "DESCENDING"}],
    }
    deals, after = [], None
    while True:
        payload = dict(base_payload)
        if after:
            payload["after"] = after
        data = hs_post(url, payload)
        deals.extend(data.get("results", []))
        nxt = data.get("paging", {}).get("next")
        if not nxt:
            break
        after = nxt.get("after")
    return deals


def fetch_deal_to_company_map(deal_ids):
    """Returns {deal_id: primary_company_id} via PARALLEL batch association reads."""
    if not deal_ids:
        return {}
    url = "https://api.hubapi.com/crm/v4/associations/deals/companies/batch/read"
    results = _parallel_batch_assoc_read(url, deal_ids)
    mapping = {}
    for result in results:
        from_id = (result.get("from") or {}).get("id")
        to_list = result.get("to") or []
        if from_id and to_list:
            first = to_list[0]
            company_id = first.get("toObjectId") or first.get("id")
            if company_id is not None:
                mapping[str(from_id)] = str(company_id)
    return mapping


def fetch_company_countries(company_ids):
    """Returns {company_id: country_string} via PARALLEL batch reads."""
    if not company_ids:
        return {}
    url = "https://api.hubapi.com/crm/v3/objects/companies/batch/read"
    results = _parallel_batch_read(url, list(company_ids),
                                    ["country", "hs_country_code"])
    out = {}
    for company in results:
        cid = str(company.get("id"))
        cprops = company.get("properties") or {}
        raw = (cprops.get("country") or cprops.get("hs_country_code") or "").strip()
        out[cid] = normalize_country(raw)
    return out


def count_companies_by_owner(owner_id):
    """Return total number of HubSpot companies owned by this owner."""
    if not owner_id:
        return 0
    url = "https://api.hubapi.com/crm/v3/objects/companies/search"
    payload = {
        "filterGroups": [{
            "filters": [{
                "propertyName": "hubspot_owner_id",
                "operator": "EQ",
                "value": str(owner_id),
            }]
        }],
        "properties": ["name"],
        "limit": 1,
    }
    try:
        data = hs_post(url, payload)
        return int(data.get("total", 0) or 0)
    except requests.HTTPError:
        return 0


def fetch_deal_to_contacts_map(deal_ids):
    """Returns {deal_id: [contact_id, ...]} via PARALLEL batch association reads."""
    if not deal_ids:
        return {}
    url = "https://api.hubapi.com/crm/v4/associations/deals/contacts/batch/read"
    results = _parallel_batch_assoc_read(url, deal_ids)
    mapping = {}
    for result in results:
        from_id = (result.get("from") or {}).get("id")
        to_list = result.get("to") or []
        ids = []
        for t in to_list:
            cid = t.get("toObjectId") or t.get("id")
            if cid is not None:
                ids.append(str(cid))
        if from_id and ids:
            mapping[str(from_id)] = ids
    return mapping


def fetch_contact_data(contact_ids):
    """Fetch country + createdate for all contacts in ONE parallel pass.
    Returns (countries, create_dates) — replaces the two separate fetches
    to cut contact round-trips in half."""
    if not contact_ids:
        return {}, {}
    url = "https://api.hubapi.com/crm/v3/objects/contacts/batch/read"
    props = ["country_list", "country", "hs_country_region_code", "createdate"]
    results = _parallel_batch_read(url, list(contact_ids), props)
    countries, create_dates = {}, {}
    for c in results:
        cid = str(c.get("id"))
        cprops = c.get("properties") or {}
        raw = (cprops.get("country_list")
               or cprops.get("country")
               or cprops.get("hs_country_region_code")
               or "").strip()
        norm = normalize_country(raw)
        if norm:
            countries[cid] = norm
        cd = cprops.get("createdate")
        if cd:
            create_dates[cid] = cd
    return countries, create_dates


# Legacy wrappers so existing callers keep working
def fetch_contact_countries(contact_ids):
    return fetch_contact_data(contact_ids)[0]


def fetch_contact_create_dates(contact_ids):
    return fetch_contact_data(contact_ids)[1]


def fetch_mql_contacts_for_month(month_start, month_end):
    """Fetch contacts created within [month_start, month_end). Tries custom
    mql_type property, falls back to lifecyclestage if not present."""
    start_ms = int(month_start.timestamp() * 1000)
    end_ms = int(month_end.timestamp() * 1000)
    url = "https://api.hubapi.com/crm/v3/objects/contacts/search"

    candidate_property_sets = [
        ["firstname", "lastname", "email", "createdate",
         "hubspot_owner_id", "mql_type", "lifecyclestage",
         "hubspot_team_id", "hs_lead_status", "reason", "reassigned_mql"],
        ["firstname", "lastname", "email", "createdate",
         "hubspot_owner_id", "mql_type", "lifecyclestage",
         "hubspot_team_id", "hs_lead_status", "reason"],
        ["firstname", "lastname", "email", "createdate",
         "hubspot_owner_id", "mql_type", "lifecyclestage",
         "hubspot_team_id", "hs_lead_status"],
        ["firstname", "lastname", "email", "createdate",
         "hubspot_owner_id", "mql_type", "lifecyclestage", "hubspot_team_id"],
        ["firstname", "lastname", "email", "createdate",
         "hubspot_owner_id", "lifecyclestage", "hubspot_team_id"],
    ]

    for properties in candidate_property_sets:
        try:
            base_payload = {
                "filterGroups": [{
                    "filters": [
                        {"propertyName": "createdate", "operator": "GTE",
                         "value": start_ms},
                        {"propertyName": "createdate", "operator": "LT",
                         "value": end_ms},
                    ]
                }],
                "properties": properties,
                "limit": 100,
            }
            contacts, after = [], None
            while True:
                payload = dict(base_payload)
                if after:
                    payload["after"] = after
                data = hs_post(url, payload)
                contacts.extend(data.get("results", []))
                nxt = data.get("paging", {}).get("next")
                if not nxt:
                    break
                after = nxt.get("after")
            return contacts
        except requests.HTTPError:
            continue
    return []


# ----- Domain helpers --------------------------------------------------------

def get_owner_team_and_rep(owner_name):
    """Return (team, rep_name) by matching owner's name against our targets."""
    if not owner_name:
        return None, None
    name_lower = owner_name.lower()
    for team, members in TARGETS.items():
        for rep in members:
            if rep.lower() in name_lower:
                return team, rep
    return None, None


def parse_amount(value):
    if value in (None, ""):
        return 0.0
    try:
        return float(value)
    except (ValueError, TypeError):
        return 0.0


def parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def parse_ts_ms(value):
    """Parse a HubSpot ms-epoch timestamp (string or int) to ISO date string."""
    if not value:
        return None
    try:
        ts = int(value) / 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    except (ValueError, TypeError):
        return None


def strip_html(text):
    """Strip HTML tags + decode entities + collapse whitespace."""
    if not text:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</p>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_lib.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


_COUNTRY_ALIASES = {
    # United States
    "us": "United States",
    "usa": "United States",
    "u.s.": "United States",
    "u.s.a.": "United States",
    "united states": "United States",
    "united states of america": "United States",
    "united_states_of_america": "United States",
    "america": "United States",
    # United Kingdom
    "uk": "United Kingdom",
    "u.k.": "United Kingdom",
    "great britain": "United Kingdom",
    "britain": "United Kingdom",
    "england": "United Kingdom",
    "scotland": "United Kingdom",
    "wales": "United Kingdom",
    "northern ireland": "United Kingdom",
    "united kingdom": "United Kingdom",
    "united_kingdom": "United Kingdom",
    # Common others
    "uae": "United Arab Emirates",
    "united arab emirates": "United Arab Emirates",
    "ksa": "Saudi Arabia",
    "saudi arabia": "Saudi Arabia",
}


def normalize_country(value):
    """Normalize country strings to a canonical display form."""
    if not value:
        return ""
    s = str(value).strip()
    if not s:
        return ""
    key = s.lower().replace("-", " ").replace(",", "").strip()
    while "  " in key:
        key = key.replace("  ", " ")
    if key in _COUNTRY_ALIASES:
        return _COUNTRY_ALIASES[key]
    # Replace underscores with spaces and title-case
    cleaned = s.replace("_", " ").strip()
    cleaned = " ".join(w.capitalize() if not w.isupper() else w
                        for w in cleaned.split())
    # Handle "Ohio, United States" → keep "United States" only
    parts = [p.strip() for p in cleaned.split(",")]
    if len(parts) > 1:
        for p in reversed(parts):
            if p and p.lower() not in ("united states", "us", "usa"):
                continue
            cleaned = "United States"
            break
        else:
            cleaned = parts[-1]
    return cleaned


def first_month_bounds(month_key=None):
    """Return (now, month_start, month_end) for the given 'YYYY-MM' key, or
    the current month if none is supplied."""
    now = datetime.now(timezone.utc)
    year, month = now.year, now.month
    if month_key:
        try:
            y_str, m_str = str(month_key).split("-")[:2]
            year = int(y_str)
            month = int(m_str)
            if not (1 <= month <= 12):
                raise ValueError
        except (ValueError, AttributeError):
            year, month = now.year, now.month
    month_start = datetime(year, month, 1, tzinfo=timezone.utc)
    if month == 12:
        month_end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        month_end = datetime(year, month + 1, 1, tzinfo=timezone.utc)
    return now, month_start, month_end


def quarter_bounds_for(year, month):
    """Return (q_start, q_end, q_label) covering the calendar quarter that
    contains (year, month). Q1=Jan-Mar, Q2=Apr-Jun, Q3=Jul-Sep, Q4=Oct-Dec."""
    q = ((month - 1) // 3) + 1
    q_start_month = (q - 1) * 3 + 1
    q_start = datetime(year, q_start_month, 1, tzinfo=timezone.utc)
    end_month = q_start_month + 3
    if end_month > 12:
        q_end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        q_end = datetime(year, end_month, 1, tzinfo=timezone.utc)
    return q_start, q_end, f"Q{q} {year}"


# Teams that have monthly targets vs quarterly targets
MONTHLY_TEAMS = {"SMB", "AM"}
QUARTERLY_TEAMS = {"ENT"}


# ----- Aggregation -----------------------------------------------------------

def _disk_cache_path():
    """Where to persist the RAW pipeline snapshot between restarts."""
    base = os.path.dirname(os.path.abspath(__file__))
    # PyInstaller-frozen apps should write next to the .exe, not into
    # the temp bundle folder.
    import sys as _sys
    if getattr(_sys, "frozen", False):
        base = os.path.dirname(os.path.abspath(_sys.executable))
    return os.path.join(base, ".mbr_raw_cache.json")


def _load_disk_cache(max_age_sec=1800):
    """Load raw pipeline data from disk if fresh enough. Returns None on miss."""
    try:
        path = _disk_cache_path()
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            j = json.load(f)
        if time.time() - float(j.get("timestamp", 0)) < max_age_sec:
            return j.get("data")
    except Exception:
        pass
    return None


def _save_disk_cache(data):
    try:
        with open(_disk_cache_path(), "w", encoding="utf-8") as f:
            json.dump({"timestamp": time.time(), "data": data}, f)
    except Exception:
        pass


def fetch_raw_pipeline_data(force=False):
    """Fetch all the heavy HubSpot data that's the same regardless of which
    month is being viewed (deals, owners, pipelines, country enrichment).

    Speed improvements:
      1. In-memory cache (fastest) — RAW_CACHE, in-process only
      2. Disk cache (survives restarts) — .mbr_raw_cache.json next to app
      3. Parallel top-level fetches (pipelines / owners / teams at once)
      4. Parallel association + batch-read fetches (5-8 workers each)
    """
    now_ts = time.time()
    if (not force and RAW_CACHE["data"] is not None
            and (now_ts - RAW_CACHE["timestamp"]) < CACHE_TTL_SECONDS):
        return RAW_CACHE["data"]

    # Disk cache — reuse the last snapshot on process restart so users
    # don't wait 10 min every time the .exe boots.
    if not force:
        disk = _load_disk_cache(max_age_sec=CACHE_TTL_SECONDS)
        if disk:
            RAW_CACHE["data"] = disk
            RAW_CACHE["timestamp"] = now_ts
            return disk

    # ---- STAGE 1: fetch independent top-level lists in parallel ----
    def _archived_owners():
        try:
            return fetch_owners(archived=True)
        except Exception:
            return []

    with ThreadPoolExecutor(max_workers=4) as ex:
        f_pipelines = ex.submit(fetch_pipelines)
        f_active_owners = ex.submit(fetch_owners)
        f_archived_owners = ex.submit(_archived_owners)
        f_teams = ex.submit(fetch_hubspot_team_id_to_name)
        pipelines = f_pipelines.result()
        owners = f_active_owners.result() + f_archived_owners.result()
        team_id_to_name = f_teams.result()

    sales_pipeline = None
    for p in pipelines:
        if "sales" in (p.get("label") or "").lower():
            sales_pipeline = p
            break
    if not sales_pipeline:
        for p in pipelines:
            if p.get("id") == "default":
                sales_pipeline = p
                break
    if not sales_pipeline and pipelines:
        sales_pipeline = pipelines[0]
    if not sales_pipeline:
        raise RuntimeError("No deal pipelines available in HubSpot.")

    stage_map = {}
    for stage in sales_pipeline.get("stages", []):
        meta = stage.get("metadata") or {}
        stage_map[stage["id"]] = {
            "label": stage.get("label", ""),
            "probability": str(meta.get("probability", "")),
            "isClosed": str(meta.get("isClosed", "")).lower() == "true",
        }

    owner_map = {}
    for o in owners:
        full_name = f"{o.get('firstName') or ''} {o.get('lastName') or ''}".strip()
        team, rep = get_owner_team_and_rep(full_name)
        owner_map[o["id"]] = {
            "name": full_name or (o.get("email") or "Unknown"),
            "email": o.get("email"),
            "team": team,
            "rep": rep,
        }

    # ---- STAGE 2: fetch deals ----
    deals = fetch_deals_for_pipeline(sales_pipeline["id"])
    deal_ids = [d.get("id") for d in deals if d.get("id")]

    # ---- STAGE 3: association reads in parallel ----
    with ThreadPoolExecutor(max_workers=2) as ex:
        f_dcomp = ex.submit(fetch_deal_to_company_map, deal_ids)
        f_dcont = ex.submit(fetch_deal_to_contacts_map, deal_ids)
        deal_to_company = f_dcomp.result()
        deal_to_contacts = f_dcont.result()

    all_contact_ids = set()
    for cids in deal_to_contacts.values():
        all_contact_ids.update(cids)

    # ---- STAGE 4: company countries + contact data + AM company counts in parallel ----
    # Contact country + createdate come from the SAME endpoint — fetched together.
    def _am_counts():
        am_owners = [(oid, info["rep"]) for oid, info in owner_map.items()
                     if info.get("team") == "AM" and info.get("rep")]
        counts = {}

        def _one(pair):
            oid, rep = pair
            return rep, count_companies_by_owner(oid)

        if not am_owners:
            return {}
        with ThreadPoolExecutor(max_workers=min(BATCH_WORKERS, len(am_owners))) as ex:
            for rep, cnt in ex.map(_one, am_owners):
                counts[rep] = cnt
        return counts

    with ThreadPoolExecutor(max_workers=3) as ex:
        f_ccountry = ex.submit(fetch_company_countries, deal_to_company.values())
        f_contactd = ex.submit(fetch_contact_data, all_contact_ids)
        f_amcounts = ex.submit(_am_counts)
        company_countries = f_ccountry.result()
        contact_countries, contact_create_dates = f_contactd.result()
        am_company_counts = f_amcounts.result()

    raw = {
        "pipelines": pipelines,
        "sales_pipeline": sales_pipeline,
        "stage_map": stage_map,
        "owner_map": owner_map,
        "team_id_to_name": team_id_to_name,
        "deals": deals,
        "deal_to_company": deal_to_company,
        "company_countries": company_countries,
        "deal_to_contacts": deal_to_contacts,
        "contact_countries": contact_countries,
        "contact_create_dates": contact_create_dates,
        "am_company_counts": am_company_counts,
    }
    RAW_CACHE["data"] = raw
    RAW_CACHE["timestamp"] = now_ts
    _save_disk_cache(raw)
    # New raw data invalidates per-month aggregations
    MONTH_CACHE.clear()
    return raw


def _smb_rep_audit_insights(rb, now, month_start, month_end):
    """Auto-generate 4-5 audit insights for an SMB rep from their deal +
    lead data for the selected month. Picks the top deal-level issues and
    the top lead-level issues with concrete examples."""
    won_deals = rb.get("closed_won_deals", []) or []
    open_deals = rb.get("open_pipeline_deals", []) or []
    lost_deals = rb.get("deals_lost_deals", []) or []
    mql_count = rb.get("mql_count", 0) or 0
    total_opps = rb.get("total_opps", 0) or 0
    closed_won = rb.get("closed_won", 0) or 0
    open_count = rb.get("open_pipeline_count", 0) or 0
    open_amount = rb.get("open_pipeline", 0) or 0

    def _parse_d(s):
        if not s: return None
        try:
            return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except Exception:
            return None

    # ---------- DEAL-LEVEL findings only ----------
    # Each finding is a dict {headline, deals[]} so the UI can list every deal
    # name instead of just one example. MQL/lead-level findings removed.
    deal_findings = []

    def _deal_item(d, extra=""):
        return {
            "name": d.get("name") or "(no name)",
            "amount": d.get("amount") or 0,
            "meta": extra,
        }

    # 1) Open deals running too long without progressing (>45 days since create)
    long_open = []
    for d in open_deals:
        dt = _parse_d(d.get("create_date"))
        if dt:
            age = (now - dt).days
            if age > 45:
                long_open.append((d, age))
    if long_open:
        long_open.sort(key=lambda x: -x[1])
        deal_findings.append({
            "headline": (
                f"{len(long_open)} open deal{'s' if len(long_open) != 1 else ''} "
                f"ageing >45 days without follow-through"
            ),
            "deals": [_deal_item(d, f"{age} days in pipeline · stage \"{d.get('stage','-')}\"")
                      for d, age in long_open],
        })

    # 2) Closed-lost deals missing a documented lost reason
    lost_no_reason = [d for d in lost_deals
                      if not (d.get("lost_reason") or "").strip()]
    if lost_no_reason:
        deal_findings.append({
            "headline": (
                f"{len(lost_no_reason)} closed-lost deal{'s' if len(lost_no_reason) != 1 else ''} "
                f"have no lost-reason recorded — always log a reason"
            ),
            "deals": [_deal_item(d, f"closed {d.get('close_date','-')}")
                      for d in lost_no_reason],
        })

    # 3) Stale opportunities — created this month, close already pushed
    slipped = []
    for d in open_deals:
        cd = _parse_d(d.get("create_date"))
        cld = _parse_d(d.get("close_date"))
        if cd and cld and cd >= month_start and (cld - cd).days > 30:
            slipped.append((d, (cld - cd).days))
    if slipped and len(deal_findings) < 2:
        deal_findings.append({
            "headline": (
                f"{len(slipped)} deal{'s' if len(slipped) != 1 else ''} created this month "
                f"already pushed >30 days to projected close — response/qualification slipped"
            ),
            "deals": [_deal_item(d, f"expected close {d.get('close_date','-')}")
                      for d, _ in slipped],
        })

    # 4) Closed-won with very long deal age — suggests slow follow-up early on
    if won_deals and len(deal_findings) < 2:
        slow_won = [d for d in won_deals if (d.get("age_days") or 0) > 60]
        if slow_won:
            slow_won.sort(key=lambda x: -(x.get("age_days") or 0))
            deal_findings.append({
                "headline": (
                    f"{len(slow_won)} closed-won deal{'s' if len(slow_won) != 1 else ''} "
                    f"took >60 days from contact-creation to close — reply cadence slow"
                ),
                "deals": [_deal_item(d, f"{d.get('age_days',0)} days from contact create")
                          for d in slow_won],
            })

    # 5) Slow-burn fallback if nothing else flagged
    if not deal_findings:
        if total_opps >= 5 and closed_won == 0:
            deal_findings.append({
                "headline": (
                    f"{total_opps} new opps created this month with 0 closed-won — "
                    f"push for first-meeting → next-step commitment in week 1"
                ),
                "deals": [],
            })

    insights = deal_findings[:2]

    if not insights:
        insights.append({
            "headline": (
                "No specific audit flags detected this month — maintain current "
                "cadence and keep logging activity + next-steps on every deal."
            ),
            "deals": [],
        })
    return insights[:2]


def fmt_usd_short(amount):
    a = float(amount or 0)
    if a >= 1_000_000:
        return f"${a/1_000_000:.1f}M"
    if a >= 1_000:
        return f"${a/1_000:.1f}K"
    return f"${a:,.0f}"


def build_dashboard(month_key=None, force=False):
    raw = fetch_raw_pipeline_data(force=force)
    sales_pipeline = raw["sales_pipeline"]
    stage_map = raw["stage_map"]
    owner_map = raw["owner_map"]
    deals = raw["deals"]
    deal_to_company = raw["deal_to_company"]
    company_countries = raw["company_countries"]
    deal_to_contacts = raw["deal_to_contacts"]
    contact_countries = raw["contact_countries"]
    contact_create_dates = raw.get("contact_create_dates", {})
    am_company_counts = raw.get("am_company_counts", {})
    team_id_to_name = raw.get("team_id_to_name", {})

    now, month_start, month_end = first_month_bounds(month_key)
    # Open Pipeline window covers the selected month + next month so reps see
    # this month's commits plus next month's expected closes in one view.
    open_window_end_month = month_end.month + 1
    open_window_end_year = month_end.year
    if open_window_end_month > 12:
        open_window_end_month = 1
        open_window_end_year += 1
    open_window_end = datetime(open_window_end_year, open_window_end_month, 1,
                                tzinfo=timezone.utc)

    try:
        mql_contacts = fetch_mql_contacts_for_month(month_start, month_end)
    except Exception:
        mql_contacts = []

    def new_rep_bucket(target):
        return {
            "target": target,
            "revenue": 0.0,
            "closed_won": 0,
            "deals_lost": 0,
            "total_opps": 0,
            "open_pipeline": 0.0,
            "open_pipeline_count": 0,
            "age_total": 0,
            "age_count": 0,
            "mql_count": 0,
            "closed_won_deals": [],
            "open_pipeline_deals": [],
            "deals_lost_deals": [],
        }

    team_data = {}
    for team_name in TARGETS:
        team_target = sum(TARGETS[team_name].values())
        team_data[team_name] = {
            "name": team_name,
            "target": team_target,
            "reps": {rep: new_rep_bucket(tgt) for rep, tgt in TARGETS[team_name].items()},
        }

    total_revenue = 0.0
    closed_won_count_month = 0
    closed_lost_count_month = 0
    total_opps_month = 0
    open_pipeline_amount = 0.0
    open_pipeline_count = 0
    age_total_days = 0
    age_count = 0
    closed_won_drilldown = []
    deals_lost_drilldown = []
    open_pipeline_drilldown = []

    # AM-only trackers
    am_no_activity = defaultdict(int)            # rep -> count of all-time closed-won deals with no activity in selected month
    am_companies_won_month = defaultdict(set)    # rep -> set of company_ids closed-won this month
    am_deal_type_counts = defaultdict(lambda: defaultdict(int))  # rep -> {deal_type_label: count}

    # ENT quarterly trackers (ENT runs on quarterly targets, not monthly)
    quarter_start, quarter_end, quarter_label = quarter_bounds_for(
        month_start.year, month_start.month)
    ent_quarter_revenue = 0.0
    ent_quarter_won_count = 0

    seen_ids = set()
    for deal in deals:
        deal_id = deal.get("id")
        if deal_id in seen_ids:
            continue
        seen_ids.add(deal_id)

        props = deal.get("properties") or {}
        amount = parse_amount(props.get("amount"))
        stage_id = props.get("dealstage")
        stage_info = stage_map.get(stage_id, {"label": "", "probability": "", "isClosed": False})
        stage_label = stage_info["label"] or ""
        stage_label_lower = stage_label.lower()

        is_closed_won = stage_info["probability"] == "1.0" or stage_label_lower == "closed won"
        is_closed_lost = (stage_info["isClosed"] and not is_closed_won) or stage_label_lower == "closed lost"

        close_dt = parse_dt(props.get("closedate"))
        create_dt = parse_dt(props.get("createdate"))

        owner_id = props.get("hubspot_owner_id")
        owner_info = owner_map.get(owner_id, {"name": "Unknown", "team": None, "rep": None})
        team = owner_info["team"]
        rep = owner_info["rep"]
        rep_bucket = (team_data[team]["reps"][rep]
                      if team in team_data and rep in team_data[team]["reps"]
                      else None)

        # Country: prefer associated contact, fall back to associated company,
        # then to any deal-level property, then "-"
        contact_country = ""
        for cid in deal_to_contacts.get(str(deal_id), []):
            v = contact_countries.get(cid)
            if v:
                contact_country = v
                break
        company_id = deal_to_company.get(str(deal_id))
        country = (contact_country
                   or (company_countries.get(company_id) if company_id else None)
                   or normalize_country(props.get("country"))
                   or normalize_country(props.get("hs_country_region_code"))
                   or normalize_country(props.get("deal_country"))
                   or "-")

        deal_type_label = normalize_deal_type(props.get("dealtype"))
        discount_amount = parse_amount(props.get("hs_discount_amount"))
        # Total before discount = HubSpot 'amount' is post-discount; pre-discount = amount + discount
        total_amount = amount + discount_amount
        after_discount = amount
        pct_rate = (discount_amount / total_amount * 100) if total_amount > 0 else 0.0

        deal_record = {
            "id": deal_id,
            "name": props.get("dealname") or "(no name)",
            "amount": amount,
            "owner": owner_info["name"],
            "team": team or "-",
            "rep": rep or "-",
            "stage": stage_label or "-",
            "country": country,
            "deal_type": deal_type_label or "-",
            "lost_reason": (props.get("closed_lost_reason") or "").strip(),
            "create_date": create_dt.strftime("%Y-%m-%d") if create_dt else "",
            "close_date": close_dt.strftime("%Y-%m-%d") if close_dt else "",
            "discount_amount": discount_amount,
            "total_amount": total_amount,
            "after_discount_amount": after_discount,
            "discount_pct": pct_rate,
        }

        # Total Opportunities (selected month, by createdate)
        if (stage_id and create_dt
                and month_start <= create_dt < month_end):
            total_opps_month += 1
            if rep_bucket:
                rep_bucket["total_opps"] += 1

        # Closed Won (selected month, by closedate)
        if (is_closed_won and close_dt
                and month_start <= close_dt < month_end):
            total_revenue += amount
            closed_won_count_month += 1
            # Avg Deal Age:
            #   AM team → deal create_date → close_date (account-management
            #   accounts are managed differently, so the deal record's own
            #   create date is the right anchor).
            #   SMB / ENT → primary (first associated) contact createdate
            #   → close_date, falls back to deal create date if no contact.
            if team == "AM":
                start_dt = create_dt
            else:
                primary_contact_create = None
                for cid in deal_to_contacts.get(str(deal_id), []):
                    cd_iso = contact_create_dates.get(cid)
                    if cd_iso:
                        primary_contact_create = parse_dt(cd_iso)
                        if primary_contact_create:
                            break
                start_dt = primary_contact_create or create_dt
            age_days = (close_dt - start_dt).days if start_dt else 0
            age_total_days += age_days
            age_count += 1
            won_record = {**deal_record, "age_days": age_days}
            closed_won_drilldown.append(won_record)
            if rep_bucket:
                rep_bucket["revenue"] += amount
                rep_bucket["closed_won"] += 1
                rep_bucket["age_total"] += age_days
                rep_bucket["age_count"] += 1
                rep_bucket["closed_won_deals"].append(won_record)
            # AM: companies closed-won this month + deal-type pivot
            if team == "AM" and rep:
                cid = deal_to_company.get(str(deal_id))
                if cid:
                    am_companies_won_month[rep].add(cid)
                am_deal_type_counts[rep][deal_type_label or "Unspecified"] += 1

        # ENT runs on a quarterly cadence — track closed-won across the
        # whole calendar quarter that the selected month belongs to.
        if (team == "ENT" and is_closed_won and close_dt
                and quarter_start <= close_dt < quarter_end):
            ent_quarter_revenue += amount
            ent_quarter_won_count += 1

        # AM: closed-won deals (any time) with no activity in selected month
        if team == "AM" and rep and is_closed_won:
            last_act = parse_dt(props.get("notes_last_updated"))
            if not last_act or last_act < month_start or last_act >= month_end:
                am_no_activity[rep] += 1

        # Closed Lost (selected month, by closedate)
        if (is_closed_lost and close_dt
                and month_start <= close_dt < month_end):
            closed_lost_count_month += 1
            deals_lost_drilldown.append(deal_record)
            if rep_bucket:
                rep_bucket["deals_lost"] += 1
                rep_bucket["deals_lost_deals"].append(deal_record)

        # Open Pipeline: deals with expected close date in NEXT month only
        # (current-month close dates are excluded per the latest spec).
        in_month_close = (close_dt is not None
                          and month_end <= close_dt < open_window_end)
        if (not is_closed_won and not is_closed_lost
                and stage_label_lower not in OPEN_PIPELINE_EXCLUDED
                and in_month_close):
            open_pipeline_amount += amount
            open_pipeline_count += 1
            open_pipeline_drilldown.append(deal_record)
            if rep_bucket:
                rep_bucket["open_pipeline"] += amount
                rep_bucket["open_pipeline_count"] += 1
                rep_bucket["open_pipeline_deals"].append(deal_record)

    # MQL counting (this month):
    #   - Include both Business MQL and Personal MQL types
    #   - Filter by HubSpot team (SMB / AM / ENT) using the contact's
    #     `hubspot_team_id` (which persists even when the owner is deactivated)
    #   - Per-rep breakdown still uses owner→rep mapping (now augmented with
    #     archived owners) so deactivated reps' contacts show up under their
    #     name when the owner-name still matches one of our targets.
    mql_count = 0
    mql_by_team = defaultdict(int)
    mql_by_type = defaultdict(int)
    mql_unassigned = 0
    # Lead-status pivot: per team -> per rep-display-name -> {status: count}
    lead_status_pivot = {t: defaultdict(lambda: defaultdict(int)) for t in TARGETS}
    lead_status_set = defaultdict(set)  # team -> set of statuses encountered
    # Per-rep unqualified reasons: team -> rep_name -> [{reason, contact}]
    unqualified_reasons = {t: defaultdict(list) for t in TARGETS}
    for c in mql_contacts:
        cprops = c.get("properties") or {}
        mql_type_raw = (cprops.get("mql_type") or "").strip()
        mql_type = mql_type_raw.lower()
        lifecycle = (cprops.get("lifecyclestage") or "").strip().lower()
        lead_status = (cprops.get("hs_lead_status") or "").strip() or "(no status)"

        # Accept Business MQL, Personal MQL, or any 'mql' value with one of
        # those qualifiers. Fall back to lifecyclestage when type is blank.
        is_target_mql = (
            "business" in mql_type
            or "personal" in mql_type
            or (not mql_type and lifecycle == "marketingqualifiedlead")
        )
        if not is_target_mql:
            continue

        # Resolve team via the contact's HubSpot team id (more reliable than
        # owner-name lookup, especially for deactivated owners).
        c_team_id = str(cprops.get("hubspot_team_id") or "").strip()
        c_team = team_id_to_name.get(c_team_id)
        if c_team not in TARGETS:
            continue

        mql_count += 1
        mql_by_team[c_team] += 1
        if mql_type_raw:
            mql_by_type[mql_type_raw] += 1

        # Per-rep attribution via owner mapping (best-effort)
        owner_info = owner_map.get(cprops.get("hubspot_owner_id"),
                                   {"team": None, "rep": None,
                                    "name": "Unassigned"})
        c_rep = owner_info.get("rep")
        owner_team = owner_info.get("team")
        owner_name = owner_info.get("name") or "Unassigned"
        if c_rep and owner_team == c_team and c_rep in team_data[c_team]["reps"]:
            team_data[c_team]["reps"][c_rep]["mql_count"] += 1
        else:
            mql_unassigned += 1

        # Lead status pivot — bucket by the contact's owner name on the
        # contact's team (so deactivated owners show up under their full name).
        pivot_key = owner_name if owner_name else "Unassigned"
        lead_status_pivot[c_team][pivot_key][lead_status] += 1
        lead_status_set[c_team].add(lead_status)

        # Per-rep Unqualified reasons drilldown — capture the contact's
        # `reason` property when the lead status is Unqualified.
        if "unqualified" in lead_status.lower() and c_rep and owner_team == c_team:
            reason_text = (cprops.get("reason") or "").strip() or "(no reason)"
            first = (cprops.get("firstname") or "").strip()
            last = (cprops.get("lastname") or "").strip()
            contact_name = (first + " " + last).strip() or (cprops.get("email") or "Contact")
            unqualified_reasons[c_team][c_rep].append({
                "reason": reason_text,
                "contact": contact_name,
                "email": cprops.get("email") or "",
            })

    # AM-only: attach account-coverage stats + deal-type pivot to each AM rep
    am_deal_type_grand_totals = defaultdict(int)
    for rep_name, rb in team_data["AM"]["reps"].items():
        total_companies = am_company_counts.get(rep_name, 0)
        companies_won = len(am_companies_won_month.get(rep_name, set()))
        rb["am_total_companies"] = total_companies
        rb["am_companies_with_closed_won"] = companies_won
        rb["am_pct_closed_won"] = (
            (companies_won / total_companies * 100) if total_companies else 0
        )
        rb["am_closed_won_no_activity"] = am_no_activity.get(rep_name, 0)

        # Deal-type pivot row (matches the report layout)
        type_counts = am_deal_type_counts.get(rep_name, {})
        rep_row = {col: type_counts.get(col, 0) for col in AM_DEAL_TYPE_COLUMNS}
        # Capture any rare types that aren't in the predefined column list
        extras = {k: v for k, v in type_counts.items()
                  if k not in AM_DEAL_TYPE_COLUMNS and v > 0}
        rep_row.update(extras)
        rep_row["Grand Total"] = sum(type_counts.values())
        rb["am_deal_type_row"] = rep_row
        for k, v in type_counts.items():
            am_deal_type_grand_totals[k] += v

    # Team-level grand totals across the deal-type columns
    team_data["AM"]["am_deal_type_columns"] = list(AM_DEAL_TYPE_COLUMNS) + sorted(
        k for k in am_deal_type_grand_totals
        if k not in AM_DEAL_TYPE_COLUMNS and am_deal_type_grand_totals[k] > 0
    )
    team_data["AM"]["am_deal_type_grand_totals"] = {
        col: am_deal_type_grand_totals.get(col, 0)
        for col in team_data["AM"]["am_deal_type_columns"]
    }
    team_data["AM"]["am_deal_type_grand_totals"]["Grand Total"] = sum(
        am_deal_type_grand_totals.values())

    # Finalize per-rep derived metrics
    for team_name, td in team_data.items():
        for rep_name, rb in td["reps"].items():
            # Audit notes — auto-generated from deal + lead data for SMB reps
            # in the selected month; otherwise fall back to manual observations.
            if team_name == "SMB":
                rb["audit_notes"] = _smb_rep_audit_insights(
                    rb, now, month_start, month_end)
            else:
                rb["audit_notes"] = list(REP_AUDIT_NOTES.get(rep_name, []))
            rb["activity_data"] = REP_ACTIVITY_DATA.get(rep_name)

            # Discount summary across this rep's closed-won deals (this month)
            disc_deals = []
            tot_amt = 0.0
            tot_disc = 0.0
            tot_after = 0.0
            for d in rb["closed_won_deals"]:
                if (d.get("discount_amount") or 0) > 0:
                    disc_deals.append({
                        "name": d["name"],
                        "total_amount": d["total_amount"],
                        "discount_amount": d["discount_amount"],
                        "after_discount_amount": d["after_discount_amount"],
                        "discount_pct": d["discount_pct"],
                    })
                    tot_amt += d["total_amount"]
                    tot_disc += d["discount_amount"]
                    tot_after += d["after_discount_amount"]
            avg_disc_rate = (tot_disc / tot_amt * 100) if tot_amt > 0 else 0.0
            rb["discount_summary"] = {
                "deals": sorted(disc_deals, key=lambda x: -x["total_amount"]),
                "total_amount": tot_amt,
                "discount_amount": tot_disc,
                "after_discount_amount": tot_after,
                "avg_discount_rate": avg_disc_rate,
            }

            rb["name"] = rep_name
            rb["team"] = team_name
            rb["attainment"] = (rb["revenue"] / rb["target"] * 100) if rb["target"] else 0
            rb["avg_deal_age"] = (rb["age_total"] / rb["age_count"]) if rb["age_count"] else 0
            rb["avg_deal_size"] = ((rb["revenue"] / rb["closed_won"])
                                   if rb["closed_won"] else 0)
            rb["opp_win_pct"] = ((rb["closed_won"] / rb["total_opps"] * 100)
                                  if rb["total_opps"] else 0)
            rb["closed_won_deals"].sort(key=lambda x: -x["amount"])
            rb["open_pipeline_deals"].sort(key=lambda x: -x["amount"])
            rb["deals_lost_deals"].sort(key=lambda x: -x["amount"])
            # Per-rep closed-lost reason categorization
            rb["lost_reasons"] = group_lost_reasons(rb["deals_lost_deals"])
            rb["lost_total"] = sum(c["count"] for c in rb["lost_reasons"])
            # Country breakdown for closed won
            country_counts = defaultdict(int)
            for d in rb["closed_won_deals"]:
                country_counts[d.get("country") or "-"] += 1
            rb["closed_won_countries"] = sorted(
                [{"country": c, "count": n} for c, n in country_counts.items()],
                key=lambda x: -x["count"])

    # Roll up team-level metrics from rep buckets
    for team_name, td in team_data.items():
        reps = list(td["reps"].values())
        td["revenue"] = sum(r["revenue"] for r in reps)
        td["closed_won"] = sum(r["closed_won"] for r in reps)
        td["deals_lost"] = sum(r["deals_lost"] for r in reps)
        td["open_pipeline"] = sum(r["open_pipeline"] for r in reps)
        td["open_pipeline_count"] = sum(r["open_pipeline_count"] for r in reps)
        td["total_opps"] = sum(r["total_opps"] for r in reps)
        team_age_total = sum(r["age_total"] for r in reps)
        team_age_count = sum(r["age_count"] for r in reps)
        td["avg_deal_age"] = (team_age_total / team_age_count) if team_age_count else 0
        td["avg_deal_size"] = ((td["revenue"] / td["closed_won"])
                                if td["closed_won"] else 0)
        td["attainment"] = (td["revenue"] / td["target"] * 100) if td["target"] else 0
        td["opp_win_pct"] = ((td["closed_won"] / td["total_opps"] * 100)
                              if td["total_opps"] else 0)
        td["mql_count"] = mql_by_team.get(team_name, 0)

        # Aggregated deal lists for team-level drilldowns
        td["closed_won_deals"] = sorted(
            [d for r in reps for d in r["closed_won_deals"]],
            key=lambda x: -x["amount"])
        td["deals_lost_deals"] = sorted(
            [d for r in reps for d in r["deals_lost_deals"]],
            key=lambda x: -x["amount"])
        td["open_pipeline_deals"] = sorted(
            [d for r in reps for d in r["open_pipeline_deals"]],
            key=lambda x: -x["amount"])

        # ---- Closed-Lost reason categorization (per team, this month) ----
        td["lost_reasons"] = group_lost_reasons(td["deals_lost_deals"])
        td["lost_total"] = sum(c["count"] for c in td["lost_reasons"])
        # Backward-compat alias (some older code paths still reference it)
        td["lost_themes"] = td["lost_reasons"]

        # ---- Lead-Status pivot (this month MQLs) ----
        pivot = lead_status_pivot.get(team_name, {})
        statuses = sorted(lead_status_set.get(team_name, set()))
        rows = []
        for owner_name in sorted(pivot.keys()):
            row = {"owner": owner_name, "counts": {}}
            row_total = 0
            for s in statuses:
                v = pivot[owner_name].get(s, 0)
                row["counts"][s] = v
                row_total += v
            row["total"] = row_total
            rows.append(row)
        col_totals = {s: sum(r["counts"].get(s, 0) for r in rows) for s in statuses}
        td["lead_status"] = {
            "statuses": statuses,
            "rows": rows,
            "col_totals": col_totals,
            "grand_total": sum(col_totals.values()),
        }

        # Per-rep "Unqualified" count (visible primarily for SMB).
        # Aggregates contacts with hs_lead_status containing "unqualified"
        # and maps them back to a known rep on this team.
        unq_status_keys = [s for s in statuses if "unqualified" in s.lower()]
        rep_unq = {}
        for r_pivot in rows:
            owner_name = r_pivot.get("owner", "")
            cnt = sum(r_pivot["counts"].get(s, 0) for s in unq_status_keys)
            if cnt <= 0:
                continue
            # Best-effort rep-name extraction: match against this team's reps
            for rep_name in td["reps"]:
                if rep_name.lower() in owner_name.lower():
                    rep_unq[rep_name] = rep_unq.get(rep_name, 0) + cnt
                    break
        for rep_name, rb in td["reps"].items():
            rb["unqualified_count"] = rep_unq.get(rep_name, 0)
            # Group this rep's unqualified MQLs by reason
            rep_reason_items = unqualified_reasons.get(team_name, {}).get(rep_name, [])
            reason_groups = defaultdict(list)
            for item in rep_reason_items:
                reason_groups[item["reason"]].append(item)
            rb["unqualified_reasons"] = sorted(
                [{
                    "reason": reason,
                    "count": len(items),
                    "contacts": [{"name": x["contact"], "email": x["email"]}
                                 for x in items[:50]],
                } for reason, items in reason_groups.items()],
                key=lambda x: -x["count"])
        td["unqualified_total"] = sum(rep_unq.values())

    # ---- Trailing 3-month trends (revenue + MQL) per rep ----
    # When viewing April, surface Jan/Feb/March on each rep's view.
    trailing_months = []
    for offset in (3, 2, 1):
        y = month_start.year
        m = month_start.month - offset
        while m < 1:
            m += 12; y -= 1
        ts = datetime(y, m, 1, tzinfo=timezone.utc)
        em = m + 1; ey = y
        if em > 12:
            em = 1; ey += 1
        te = datetime(ey, em, 1, tzinfo=timezone.utc)
        trailing_months.append({
            "label": ts.strftime("%b %Y"),
            "key": ts.strftime("%Y-%m"),
            "start": ts, "end": te,
        })

    rev_by_rep_month = defaultdict(lambda: defaultdict(float))
    for deal in deals:
        props = deal.get("properties") or {}
        sid = props.get("dealstage")
        si = stage_map.get(sid, {})
        is_won = si.get("probability") == "1.0" or (si.get("label") or "").lower() == "closed won"
        if not is_won:
            continue
        cd = parse_dt(props.get("closedate"))
        if not cd:
            continue
        info = owner_map.get(props.get("hubspot_owner_id"), {})
        rep_n = info.get("rep")
        if not rep_n:
            continue
        amt = parse_amount(props.get("amount"))
        for tm in trailing_months:
            if tm["start"] <= cd < tm["end"]:
                rev_by_rep_month[rep_n][tm["label"]] += amt
                break

    # MQL trend: fetch MQLs for the trailing window (Jan..Mar when viewing Apr)
    if trailing_months:
        try:
            trail_mqls = fetch_mql_contacts_for_month(
                trailing_months[0]["start"], trailing_months[-1]["end"])
        except Exception:
            trail_mqls = []
    else:
        trail_mqls = []
    mql_by_rep_month = defaultdict(lambda: defaultdict(int))
    for c in trail_mqls:
        cprops = c.get("properties") or {}
        mtype = (cprops.get("mql_type") or "").strip().lower()
        if not ("business" in mtype or "personal" in mtype):
            continue
        c_team_id = str(cprops.get("hubspot_team_id") or "").strip()
        c_team = team_id_to_name.get(c_team_id)
        if c_team not in TARGETS:
            continue
        info = owner_map.get(cprops.get("hubspot_owner_id"), {})
        rep_n = info.get("rep")
        if not rep_n:
            continue
        cdt = parse_dt(cprops.get("createdate"))
        if not cdt:
            continue
        for tm in trailing_months:
            if tm["start"] <= cdt < tm["end"]:
                mql_by_rep_month[rep_n][tm["label"]] += 1
                break

    # ---- Rolling 90 days per rep ----
    rolling_end = month_end
    rolling_start = rolling_end - timedelta(days=90)
    try:
        rolling_mqls = fetch_mql_contacts_for_month(rolling_start, rolling_end)
    except Exception:
        rolling_mqls = []
    rolling_per_rep = defaultdict(lambda: {"mql": 0, "opps": 0, "won": 0})
    rolling_per_team = defaultdict(lambda: {"mql": 0, "opps": 0, "won": 0})
    for c in rolling_mqls:
        cprops = c.get("properties") or {}
        # MQL type must be Business or Personal
        mtype = (cprops.get("mql_type") or "").strip().lower()
        if not ("business" in mtype or "personal" in mtype):
            continue
        # Rolling 90 excludes contacts that have been explicitly reassigned.
        # The HubSpot `reassigned_mql` property only has "Yes" and "NO" as
        # options — anything else (empty / None) means "unknown / not yet
        # set", which is what we want to count.
        reassigned = (cprops.get("reassigned_mql") or "").strip().lower()
        if reassigned in ("yes", "no"):
            continue
        # Team is determined by who owns the contact (must map to a current rep)
        info = owner_map.get(cprops.get("hubspot_owner_id"), {})
        c_team = info.get("team")
        rep_n = info.get("rep")
        if c_team not in TARGETS or not rep_n:
            continue
        rolling_per_team[c_team]["mql"] += 1
        rolling_per_rep[rep_n]["mql"] += 1
    for deal in deals:
        props = deal.get("properties") or {}
        info = owner_map.get(props.get("hubspot_owner_id"), {})
        c_team = info.get("team"); rep_n = info.get("rep")
        if c_team not in TARGETS:
            continue
        sid = props.get("dealstage")
        si = stage_map.get(sid, {})
        is_won = si.get("probability") == "1.0" or (si.get("label") or "").lower() == "closed won"
        cd = parse_dt(props.get("closedate"))
        cdt = parse_dt(props.get("createdate"))
        if cdt and rolling_start <= cdt < rolling_end:
            rolling_per_team[c_team]["opps"] += 1
            if rep_n: rolling_per_rep[rep_n]["opps"] += 1
        if is_won and cd and rolling_start <= cd < rolling_end:
            rolling_per_team[c_team]["won"] += 1
            if rep_n: rolling_per_rep[rep_n]["won"] += 1

    # Stamp trends + rolling-90 onto each rep bucket; team rolling-90 too.
    trail_labels = [tm["label"] for tm in trailing_months]
    # Rolling 90 is now fully dynamic — every count comes from live HubSpot
    # data filtered by `reassigned_mql == "unknown"`. No manual overrides.

    # Build a parallel list of trailing month keys (YYYY-MM) so we can look
    # up per-month revenue goals from REP_MONTHLY_REVENUE_GOALS.
    trail_keys = [tm["key"] for tm in trailing_months]

    for tn, td in team_data.items():
        for rn, rb in td["reps"].items():
            rb["trend_months"] = trail_labels
            rb["trend_revenue"] = [rev_by_rep_month.get(rn, {}).get(l, 0.0) for l in trail_labels]
            rb["trend_mql"]     = [mql_by_rep_month.get(rn, {}).get(l, 0)   for l in trail_labels]
            # Per-month revenue goal (only set for reps configured in
            # REP_MONTHLY_REVENUE_GOALS). Achieved % is computed on the
            # frontend as (revenue / goal * 100) so totals stay live.
            goals_map = REP_MONTHLY_REVENUE_GOALS.get(rn, {})
            rb["trend_revenue_goals"] = [goals_map.get(k, 0) for k in trail_keys]
            r90 = rolling_per_rep.get(rn, {"mql": 0, "opps": 0, "won": 0})
            mq, op, wn = r90["mql"], r90["opps"], r90["won"]
            rb["rolling_90"] = {
                "mql": mq, "opps": op, "won": wn,
                "mql_to_opp":  (op / mq * 100) if mq else 0,
                "opp_to_won":  (wn / op * 100) if op else 0,
                "mql_to_won":  (wn / mq * 100) if mq else 0,
            }
        # Roll up team-level rolling 90 from the per-rep values so any manual
        # overrides flow into the team total automatically.
        reps_r90 = [r["rolling_90"] for r in td["reps"].values()]
        t_mq = sum(x["mql"] for x in reps_r90)
        t_op = sum(x["opps"] for x in reps_r90)
        t_wn = sum(x["won"] for x in reps_r90)
        td["rolling_90"] = {
            "mql": t_mq, "opps": t_op, "won": t_wn,
            "mql_to_opp": (t_op / t_mq * 100) if t_mq else 0,
            "opp_to_won": (t_wn / t_op * 100) if t_op else 0,
            "mql_to_won": (t_wn / t_mq * 100) if t_mq else 0,
        }
    rolling_window_label = (
        f"{rolling_start.strftime('%b %d, %Y')} – {(rolling_end - timedelta(days=1)).strftime('%b %d, %Y')}"
    )

    # "Total Revenue" / "Total Target" on the overview cover only the teams
    # that run on a monthly cadence (SMB + AM). ENT is on a quarterly target
    # and is reported separately.
    monthly_total_revenue = sum(team_data[t]["revenue"] for t in MONTHLY_TEAMS
                                 if t in team_data)
    monthly_total_target = sum(sum(TARGETS[t].values()) for t in MONTHLY_TEAMS
                                if t in TARGETS)
    attainment_pct = ((monthly_total_revenue / monthly_total_target * 100)
                       if monthly_total_target else 0)

    # ENT quarterly numbers
    ent_quarter_target = sum(sum(TARGETS[t].values()) for t in QUARTERLY_TEAMS
                              if t in TARGETS)
    ent_quarter_attainment = ((ent_quarter_revenue / ent_quarter_target * 100)
                                if ent_quarter_target else 0)

    opp_win_pct = (closed_won_count_month / total_opps_month * 100) if total_opps_month else 0
    avg_deal_size = (total_revenue / closed_won_count_month) if closed_won_count_month else 0
    avg_deal_age = (age_total_days / age_count) if age_count else 0

    closed_won_drilldown.sort(key=lambda x: -x["amount"])
    deals_lost_drilldown.sort(key=lambda x: -x["amount"])
    open_pipeline_drilldown.sort(key=lambda x: -x["amount"])

    return {
        "kpis": {
            "total_revenue": monthly_total_revenue,
            "closed_won_count": closed_won_count_month,
            "total_target": monthly_total_target,
            "attainment_pct": attainment_pct,
            "opp_win_pct": opp_win_pct,
            "total_opps": total_opps_month,
            "deals_lost": closed_lost_count_month,
            "open_pipeline": open_pipeline_amount,
            "open_pipeline_count": open_pipeline_count,
            "avg_deal_size": avg_deal_size,
            "avg_deal_age": avg_deal_age,
            "mql_count": mql_count,
            "mql_by_team": dict(mql_by_team),
            "mql_by_type": dict(mql_by_type),
            "mql_unassigned": mql_unassigned,
            # ENT-only quarterly section (shown separately on overview)
            "ent_quarter_revenue": ent_quarter_revenue,
            "ent_quarter_target": ent_quarter_target,
            "ent_quarter_attainment": ent_quarter_attainment,
            "ent_quarter_won_count": ent_quarter_won_count,
            "ent_quarter_label": quarter_label,
            "rolling_window_label": rolling_window_label,
        },
        "teams": team_data,
        "closed_won_deals": closed_won_drilldown,
        "deals_lost_deals": deals_lost_drilldown,
        "open_pipeline_deals": open_pipeline_drilldown,
        "pipeline_name": sales_pipeline.get("label") or "Sales Pipeline",
        "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "month": month_start.strftime("%B %Y"),
        "month_key": month_start.strftime("%Y-%m"),
        "is_current_month": (month_start.year == now.year
                              and month_start.month == now.month),
    }


def get_dashboard_data(month_key=None, force_refresh=False):
    """Cached per-month dashboard data."""
    cache_key = month_key or "_current"
    now_ts = time.time()
    cached = MONTH_CACHE.get(cache_key)
    if (not force_refresh and cached
            and (now_ts - cached["timestamp"]) < CACHE_TTL_SECONDS):
        return cached["data"]
    data = build_dashboard(month_key=month_key, force=force_refresh)
    MONTH_CACHE[cache_key] = {"data": data, "timestamp": now_ts}
    return data


def list_available_months(count=18):
    """Generate (key, label) for the last N months including current."""
    now = datetime.now(timezone.utc)
    out = []
    y, m = now.year, now.month
    for _ in range(count):
        d = datetime(y, m, 1, tzinfo=timezone.utc)
        out.append({
            "key": d.strftime("%Y-%m"),
            "label": d.strftime("%B %Y"),
        })
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return out


# ----- Routes ----------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


def fetch_deal_insights(deal_id):
    """Return deal info + closed lost reason + chronological activity timeline."""
    # 1) Get deal with associations
    deal_url = f"https://api.hubapi.com/crm/v3/objects/deals/{deal_id}"
    deal_data = hs_get(deal_url, {
        "properties": ("dealname,amount,closedate,createdate,dealstage,"
                       "closed_lost_reason,closed_won_reason,hubspot_owner_id,"
                       "notes_last_contacted,notes_last_updated,"
                       "num_notes,num_contacted_notes"),
        "associations": "notes,calls,emails,meetings,tasks",
    })
    props = deal_data.get("properties") or {}
    associations = deal_data.get("associations") or {}

    # 2) Collect engagement IDs by type. Each entry has nested fallback
    # property lists — first attempts the richest set, then degrades.
    type_configs = [
        ("notes", [
            ["hs_note_body", "hs_timestamp", "hs_createdate", "hs_lastmodifieddate"],
            ["hs_note_body", "hs_timestamp"],
        ]),
        ("calls", [
            ["hs_call_title", "hs_call_body", "hs_timestamp",
             "hs_call_disposition", "hs_call_direction", "hs_call_duration"],
            ["hs_call_title", "hs_call_body", "hs_timestamp"],
        ]),
        ("emails", [
            ["hs_email_subject", "hs_email_text", "hs_email_html", "hs_timestamp",
             "hs_email_direction", "hs_email_status"],
            ["hs_email_subject", "hs_email_text", "hs_timestamp", "hs_email_direction"],
            ["hs_email_subject", "hs_timestamp"],
        ]),
        ("meetings", [
            ["hs_meeting_title", "hs_meeting_body", "hs_timestamp",
             "hs_meeting_outcome", "hs_meeting_start_time", "hs_meeting_end_time"],
            ["hs_meeting_title", "hs_meeting_body", "hs_timestamp"],
        ]),
        ("tasks", [
            ["hs_task_subject", "hs_task_body", "hs_timestamp",
             "hs_task_status", "hs_task_completion_date"],
            ["hs_task_subject", "hs_task_body", "hs_timestamp"],
        ]),
    ]

    def _batch_with_fallback(obj_type, ids, prop_sets):
        """Try each property set until one succeeds. Returns list of results."""
        batch_url = f"https://api.hubapi.com/crm/v3/objects/{obj_type}/batch/read"
        out = []
        for i in range(0, len(ids), 100):
            batch = ids[i:i + 100]
            success = False
            for props in prop_sets:
                try:
                    resp = hs_post(batch_url, {
                        "properties": props,
                        "inputs": [{"id": x} for x in batch],
                    })
                    out.extend(resp.get("results", []))
                    success = True
                    break
                except requests.HTTPError:
                    continue
            if not success:
                continue
        return out

    timeline = []
    inaccessible = {}   # type -> count of items we could see but not read (e.g. 403)
    for obj_type, prop_sets in type_configs:
        ids = []
        results = (associations.get(obj_type) or {}).get("results") or []
        for r in results:
            if r.get("id"):
                ids.append(r["id"])
        if not ids:
            continue
        batch_results = _batch_with_fallback(obj_type, ids, prop_sets)
        if len(batch_results) < len(ids):
            inaccessible[obj_type] = len(ids) - len(batch_results)
        for item in batch_results:
            p = item.get("properties") or {}
            ts_iso = (parse_ts_ms(p.get("hs_timestamp"))
                      or parse_ts_ms(p.get("hs_createdate"))
                      or parse_ts_ms(p.get("hs_lastmodifieddate"))
                      or item.get("createdAt"))
            title = ""
            body = ""
            meta = {}
            if obj_type == "notes":
                title = "Note"
                body = strip_html(p.get("hs_note_body") or "")
            elif obj_type == "calls":
                title = p.get("hs_call_title") or "Call"
                body = strip_html(p.get("hs_call_body") or "")
                meta = {
                    "direction": p.get("hs_call_direction"),
                    "disposition": p.get("hs_call_disposition"),
                    "duration_ms": p.get("hs_call_duration"),
                }
            elif obj_type == "emails":
                title = p.get("hs_email_subject") or "Email"
                body = strip_html(p.get("hs_email_text")
                                  or p.get("hs_email_html") or "")
                meta = {
                    "direction": p.get("hs_email_direction"),
                    "status": p.get("hs_email_status"),
                }
            elif obj_type == "meetings":
                title = p.get("hs_meeting_title") or "Meeting"
                body = strip_html(p.get("hs_meeting_body") or "")
                meta = {"outcome": p.get("hs_meeting_outcome")}
            elif obj_type == "tasks":
                title = p.get("hs_task_subject") or "Task"
                body = strip_html(p.get("hs_task_body") or "")
                meta = {
                    "status": p.get("hs_task_status"),
                    "completion": parse_ts_ms(p.get("hs_task_completion_date")),
                }
            timeline.append({
                "type": obj_type[:-1],
                "id": item.get("id"),
                "timestamp": ts_iso,
                "title": title,
                "body": body[:1500],
                "body_truncated": len(body) > 1500,
                "meta": {k: v for k, v in meta.items() if v not in (None, "")},
            })

    timeline.sort(key=lambda x: x.get("timestamp") or "", reverse=True)

    # Engagement counts
    counts = {}
    for obj_type, _ in type_configs:
        counts[obj_type] = len(((associations.get(obj_type) or {}).get("results")) or [])

    return {
        "deal_id": deal_id,
        "deal_name": props.get("dealname") or "",
        "amount": parse_amount(props.get("amount")),
        "stage": props.get("dealstage") or "",
        "closed_lost_reason": (props.get("closed_lost_reason") or "").strip(),
        "closed_won_reason": (props.get("closed_won_reason") or "").strip(),
        "create_date": props.get("createdate"),
        "close_date": props.get("closedate"),
        "last_contacted": props.get("notes_last_contacted"),
        "last_updated_engagement": props.get("notes_last_updated"),
        "num_notes": props.get("num_notes"),
        "num_contacted_notes": props.get("num_contacted_notes"),
        "engagement_counts": counts,
        "inaccessible_counts": inaccessible,
        "timeline": timeline,
    }


@app.route("/api/deal/<deal_id>/insights")
def api_deal_insights(deal_id):
    try:
        return jsonify({"success": True, "data": fetch_deal_insights(deal_id)})
    except requests.HTTPError as e:
        body = ""
        try:
            body = e.response.text[:500]
        except Exception:
            pass
        return jsonify({"success": False,
                        "error": f"HubSpot API error: {e}. {body}"}), 500
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/dashboard")
def api_dashboard():
    import traceback
    refresh = request.args.get("refresh") == "true"
    month = (request.args.get("month") or "").strip() or None
    try:
        data = get_dashboard_data(month_key=month, force_refresh=refresh)
        return jsonify({"success": True, "data": data})
    except requests.HTTPError as e:
        traceback.print_exc()
        body = ""
        try:
            body = e.response.text[:500]
        except Exception:
            pass
        return jsonify({
            "success": False,
            "error": f"HubSpot API error: {e}. {body}",
        }), 500
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/months")
def api_months():
    return jsonify({"success": True, "data": list_available_months(18)})


if __name__ == "__main__":
    print("=" * 60)
    print(" MBR Dashboard - starting server")
    print("=" * 60)
    print(" Open in browser: http://localhost:5000")
    print(" Press Ctrl+C to stop")
    print("=" * 60)
    app.run(debug=False, host="0.0.0.0", port=5000)

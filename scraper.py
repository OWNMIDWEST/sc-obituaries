#!/usr/bin/env python3
"""
SC Obituary Scraper — GitHub Actions edition
Reads email credentials from environment variables (GitHub Secrets).
Writes history to docs/sc_obituaries_history.json
Writes dashboard to docs/index.html  (served by GitHub Pages)
"""

import requests
from bs4 import BeautifulSoup
import json
import re
import smtplib
import ssl
import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import date, datetime, timedelta
import time

# ─────────────────────────────────────────────
#  CONFIG — credentials come from GitHub Secrets
# ─────────────────────────────────────────────
SMTP_SERVER    = "smtp.gmail.com"
SMTP_PORT      = 465
EMAIL_FROM     = os.environ.get("EMAIL_FROM", "")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD", "")
EMAIL_TO       = [e.strip() for e in os.environ.get("EMAIL_TO", "").split(",") if e.strip()]

DOCS_DIR      = "docs"
HISTORY_FILE  = os.path.join(DOCS_DIR, "sc_obituaries_history.json")
DASHBOARD_OUT = os.path.join(DOCS_DIR, "index.html")
HISTORY_DAYS  = 7

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    )
}

COUNTIES = ["Greenville", "Spartanburg", "Anderson"]

SOURCES = {
    "Greenville": [
        {"name": "Greenville News (Legacy.com)",     "url": "https://www.legacy.com/us/obituaries/greenvilleonline/", "parser": "legacy"},
        {"name": "Robinson Funeral Homes",            "url": "https://www.robinsonfuneralhomes.com/obituaries",        "parser": "generic"},
        {"name": "Geo. W. Harley Funeral Home",       "url": "https://www.georgewharleyfuneralhome.com/obituaries",   "parser": "generic"},
    ],
    "Spartanburg": [
        {"name": "Spartanburg Herald-Journal (Legacy.com)", "url": "https://www.legacy.com/us/obituaries/shj/",      "parser": "legacy"},
        {"name": "Floyd's Mortuary",                         "url": "https://www.floydsmortuary.com/obituaries",      "parser": "generic"},
        {"name": "Spartanburg Mortuary",                     "url": "https://www.spartanburgmortuary.com/obituaries", "parser": "generic"},
    ],
    "Anderson": [
        {"name": "Anderson Independent-Mail (Legacy.com)", "url": "https://www.legacy.com/us/obituaries/independentmail/", "parser": "legacy"},
        {"name": "McDougald Funeral Home",                  "url": "https://www.mcdougaldfuneralhome.com/obituaries",       "parser": "generic"},
        {"name": "Sullivan-King Mortuary",                  "url": "https://www.sullivankingmortuary.com/obituaries",        "parser": "generic"},
    ],
}

COUNTY_COLORS = {
    "Greenville":  "#2563eb",
    "Spartanburg": "#7c3aed",
    "Anderson":    "#059669",
}


# ─────────────────────────────────────────────
#  FETCHING
# ─────────────────────────────────────────────

def fetch_page(url, retries=2):
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            if resp.status_code == 200:
                return resp.text
            print(f"  [HTTP {resp.status_code}] {url}")
        except requests.RequestException as e:
            print(f"  [Error] {url}: {e}")
        if attempt < retries:
            time.sleep(2)
    return None


# ─────────────────────────────────────────────
#  PARSERS
# ─────────────────────────────────────────────

def parse_legacy(html, source_name, county):
    soup = BeautifulSoup(html, "html.parser")
    results = []
    cards = soup.find_all("div", class_=re.compile(r"obituary-listing|ObituaryCard|listing-item", re.I))
    if not cards:
        cards = soup.find_all(["article", "li"], class_=re.compile(r"obit|listing|card", re.I))
    for card in cards:
        name_tag = card.find(["h2","h3","h4","a"], class_=re.compile(r"name|title", re.I)) or card.find("a")
        if not name_tag:
            continue
        name = name_tag.get_text(strip=True)
        if not name or len(name) < 3:
            continue
        link_tag = card.find("a", href=True)
        link = ""
        if link_tag:
            href = link_tag["href"]
            link = href if href.startswith("http") else "https://www.legacy.com" + href
        date_tag = card.find(class_=re.compile(r"date|death|born", re.I))
        obit_date = date_tag.get_text(strip=True) if date_tag else "See source"
        location_tag = card.find(class_=re.compile(r"location|city|residence", re.I))
        location = location_tag.get_text(strip=True) if location_tag else f"{county} County, SC"
        results.append({"name": name, "date": obit_date, "location": location,
                         "source": source_name, "county": county, "link": link})
    return results


def parse_generic(html, source_name, county):
    soup = BeautifulSoup(html, "html.parser")
    results = []
    containers = (
        soup.find_all(class_=re.compile(r"obit|deceased|listing|memorial|person", re.I))
        or soup.find_all("article")
        or soup.find_all("li", class_=True)
    )
    seen = set()
    for container in containers:
        name_tag = (
            container.find(["h1","h2","h3","h4","strong","a"], class_=re.compile(r"name|title|deceased", re.I))
            or container.find(["h2","h3","h4"])
        )
        if not name_tag:
            continue
        name = name_tag.get_text(strip=True)
        if not name or len(name) < 3 or name in seen:
            continue
        if any(w in name.lower() for w in ["home","about","contact","service","menu","obituar"]):
            continue
        seen.add(name)
        link_tag = container.find("a", href=True)
        link = link_tag["href"] if link_tag and link_tag["href"].startswith("http") else ""
        date_tag = container.find(class_=re.compile(r"date|death|born|age", re.I))
        obit_date = date_tag.get_text(strip=True) if date_tag else "See source"
        results.append({"name": name, "date": obit_date, "location": f"{county} County, SC",
                         "source": source_name, "county": county, "link": link})
    return results


# ─────────────────────────────────────────────
#  SCRAPING
# ─────────────────────────────────────────────

def scrape_all():
    today = date.today().strftime("%Y-%m-%d")
    all_results = {county: [] for county in COUNTIES}
    total = 0
    print(f"\n{'='*60}\n  SC Obituary Scraper — {today}\n{'='*60}\n")
    for county, sources in SOURCES.items():
        print(f"[{county} County]")
        for source in sources:
            print(f"  Fetching: {source['name']} ...")
            html = fetch_page(source["url"])
            if not html:
                print("  -> Skipped.")
                continue
            parse_fn = parse_legacy if source["parser"] == "legacy" else parse_generic
            entries = parse_fn(html, source["name"], county)
            print(f"  -> {len(entries)} found")
            all_results[county].extend(entries)
            total += len(entries)
            time.sleep(1)
        print()
    print(f"Total: {total}\n")
    return all_results, today, total


# ─────────────────────────────────────────────
#  HISTORY
# ─────────────────────────────────────────────

def load_history():
    os.makedirs(DOCS_DIR, exist_ok=True)
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_history(history, today, results):
    history[today] = results
    cutoff = (date.today() - timedelta(days=HISTORY_DAYS)).strftime("%Y-%m-%d")
    history = {d: v for d, v in history.items() if d >= cutoff}
    history = dict(sorted(history.items(), reverse=True))
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)
    print(f"History saved -> {HISTORY_FILE}  ({len(history)} days)")
    return history


# ─────────────────────────────────────────────
#  DASHBOARD HTML (written to docs/index.html)
# ─────────────────────────────────────────────

def save_dashboard(history):
    """Write a self-contained dashboard embedding the history JSON inline."""
    history_json = json.dumps(history, ensure_ascii=False)
    today_key = sorted(history.keys())[-1] if history else ""
    updated = today_key or datetime.now().strftime("%Y-%m-%d")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>SC Obituary Dashboard</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:Georgia,serif;background:#f5f5f0;color:#222;padding:16px}}
.header{{background:#1a1a2e;color:white;padding:20px 24px;border-radius:8px;margin-bottom:18px}}
.header h1{{font-size:22px;margin-bottom:4px}}
.header p{{color:#aaa;font-size:13px;font-family:Arial,sans-serif}}
.nav{{display:flex;gap:8px;margin-bottom:16px;flex-wrap:wrap;align-items:center}}
.tab{{padding:8px 18px;border-radius:20px;border:none;cursor:pointer;
      font-family:Arial,sans-serif;font-size:13px;font-weight:600;transition:opacity .15s}}
.tab:hover{{opacity:.85}}
.panel{{display:none}}.panel.active{{display:block}}
.card{{background:white;border-radius:8px;padding:20px;margin-bottom:16px;box-shadow:0 1px 4px rgba(0,0,0,.08)}}
.card-title{{font-size:18px;font-weight:bold;border-left:5px solid;padding-left:12px;margin-bottom:14px}}
.badge{{font-family:Arial,sans-serif;font-size:11px;background:#f3f4f6;color:#555;
        padding:2px 9px;border-radius:12px;margin-left:8px;font-weight:normal}}
table{{width:100%;border-collapse:collapse;font-size:14px}}
thead tr{{background:#f0f0f0}}
th{{padding:8px 10px;text-align:left;font-family:Arial,sans-serif;font-size:11px;
    text-transform:uppercase;letter-spacing:.4px;color:#555}}
td{{padding:9px 10px;border-bottom:1px solid #eee;vertical-align:top}}
tr:last-child td{{border-bottom:none}}
tr:hover td{{background:#fafafa}}
a.vl{{text-decoration:none;font-size:12px;font-family:Arial,sans-serif;
      padding:3px 10px;border-radius:10px;color:white}}
.notice{{background:#fffbeb;border:1px solid #fde68a;border-radius:8px;
         padding:14px 18px;margin-bottom:16px;font-family:Arial,sans-serif;
         font-size:13px;color:#78350f;line-height:1.6}}
.step{{background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;
       padding:14px 18px;margin-bottom:12px;font-family:Arial,sans-serif;
       font-size:13px;color:#14532d;line-height:1.8}}
.step strong{{display:block;margin-bottom:4px;font-size:14px}}
code{{background:#e5e7eb;padding:2px 6px;border-radius:4px;font-family:monospace;font-size:12px}}
.day-tab{{display:inline-block;padding:5px 14px;border-radius:16px;cursor:pointer;
          font-family:Arial,sans-serif;font-size:12px;font-weight:600;margin:3px;
          background:#f3f4f6;color:#374151;border:1px solid #e5e7eb}}
.day-tab:hover{{background:#e5e7eb}}
.day-tab.sel{{background:#b45309;color:white;border-color:#b45309}}
.day-panel{{display:none}}.day-panel.active{{display:block}}
.county-seg{{margin-bottom:20px}}
.empty-msg{{color:#999;font-style:italic;padding:8px 0;font-family:Arial,sans-serif;font-size:13px}}
.src-row{{display:flex;align-items:center;justify-content:space-between;
          padding:9px 12px;border-radius:6px;margin-bottom:6px;
          background:#f9f9f9;border:1px solid #eee}}
.src-row:hover{{background:#f0f0f0}}
.src-name{{font-size:13px;color:#222;font-weight:500}}
.src-type{{font-size:11px;color:#888;margin-top:1px;font-family:Arial,sans-serif}}
.src-btn{{padding:5px 14px;border-radius:12px;border:none;cursor:pointer;
          font-size:12px;font-weight:600;color:white;text-decoration:none;display:inline-block}}
</style>
</head>
<body>
<div class="header">
  <h1>SC Obituary Dashboard</h1>
  <p>Greenville &nbsp;·&nbsp; Spartanburg &nbsp;·&nbsp; Anderson &nbsp;|&nbsp; Updated: {updated}</p>
</div>

<div class="nav">
  <button class="tab" style="background:#374151;color:white" onclick="showPanel('all')">Today — All</button>
  <button class="tab" style="background:#2563eb;color:white" onclick="showPanel('gvl')">Greenville</button>
  <button class="tab" style="background:#7c3aed;color:white" onclick="showPanel('spt')">Spartanburg</button>
  <button class="tab" style="background:#059669;color:white" onclick="showPanel('and')">Anderson</button>
  <button class="tab" style="background:#b45309;color:white" onclick="showPanel('hist')">7-Day History</button>
  <button class="tab" style="background:#e5e7eb;color:#374151;margin-left:auto" onclick="showPanel('links')">Sources</button>
</div>

<div id="panel-all"   class="panel active"></div>
<div id="panel-gvl"   class="panel"></div>
<div id="panel-spt"   class="panel"></div>
<div id="panel-and"   class="panel"></div>
<div id="panel-hist"  class="panel">
  <div class="card">
    <div class="card-title" style="border-color:#b45309;color:#b45309;">7-Day History</div>
    <div id="hist-tabs" style="margin-bottom:16px;"></div>
    <div id="hist-body"></div>
  </div>
</div>
<div id="panel-links" class="panel">
  <div class="card">
    <div class="card-title" style="border-color:#2563eb;color:#2563eb;">Greenville Sources</div>
    <div class="src-row"><div><div class="src-name">Greenville News — Legacy.com</div><div class="src-type">Newspaper</div></div><a class="src-btn" style="background:#2563eb" href="https://www.legacy.com/us/obituaries/greenvilleonline/" target="_blank">Open</a></div>
    <div class="src-row"><div><div class="src-name">Robinson Funeral Homes</div><div class="src-type">Funeral home</div></div><a class="src-btn" style="background:#2563eb" href="https://www.robinsonfuneralhomes.com/obituaries" target="_blank">Open</a></div>
    <div class="src-row"><div><div class="src-name">Geo. W. Harley Funeral Home</div><div class="src-type">Funeral home</div></div><a class="src-btn" style="background:#2563eb" href="https://www.georgewharleyfuneralhome.com/obituaries" target="_blank">Open</a></div>
    <div class="src-row"><div><div class="src-name">Legacy.com county search</div><div class="src-type">Aggregator</div></div><a class="src-btn" style="background:#2563eb" href="https://www.legacy.com/obituaries/search?countryId=1&regionId=42&countyName=Greenville" target="_blank">Open</a></div>
  </div>
  <div class="card">
    <div class="card-title" style="border-color:#7c3aed;color:#7c3aed;">Spartanburg Sources</div>
    <div class="src-row"><div><div class="src-name">Spartanburg Herald-Journal — Legacy.com</div><div class="src-type">Newspaper</div></div><a class="src-btn" style="background:#7c3aed" href="https://www.legacy.com/us/obituaries/shj/" target="_blank">Open</a></div>
    <div class="src-row"><div><div class="src-name">Floyd's Mortuary</div><div class="src-type">Funeral home</div></div><a class="src-btn" style="background:#7c3aed" href="https://www.floydsmortuary.com/obituaries" target="_blank">Open</a></div>
    <div class="src-row"><div><div class="src-name">Spartanburg Mortuary</div><div class="src-type">Funeral home</div></div><a class="src-btn" style="background:#7c3aed" href="https://www.spartanburgmortuary.com/obituaries" target="_blank">Open</a></div>
    <div class="src-row"><div><div class="src-name">Legacy.com county search</div><div class="src-type">Aggregator</div></div><a class="src-btn" style="background:#7c3aed" href="https://www.legacy.com/obituaries/search?countryId=1&regionId=42&countyName=Spartanburg" target="_blank">Open</a></div>
  </div>
  <div class="card">
    <div class="card-title" style="border-color:#059669;color:#059669;">Anderson Sources</div>
    <div class="src-row"><div><div class="src-name">Anderson Independent-Mail — Legacy.com</div><div class="src-type">Newspaper</div></div><a class="src-btn" style="background:#059669" href="https://www.legacy.com/us/obituaries/independentmail/" target="_blank">Open</a></div>
    <div class="src-row"><div><div class="src-name">McDougald Funeral Home</div><div class="src-type">Funeral home</div></div><a class="src-btn" style="background:#059669" href="https://www.mcdougaldfuneralhome.com/obituaries" target="_blank">Open</a></div>
    <div class="src-row"><div><div class="src-name">Sullivan-King Mortuary</div><div class="src-type">Funeral home</div></div><a class="src-btn" style="background:#059669" href="https://www.sullivankingmortuary.com/obituaries" target="_blank">Open</a></div>
    <div class="src-row"><div><div class="src-name">Legacy.com county search</div><div class="src-type">Aggregator</div></div><a class="src-btn" style="background:#059669" href="https://www.legacy.com/obituaries/search?countryId=1&regionId=42&countyName=Anderson" target="_blank">Open</a></div>
  </div>
</div>

<script>
const COUNTIES = ["Greenville","Spartanburg","Anderson"];
const COLORS   = {{Greenville:"#2563eb",Spartanburg:"#7c3aed",Anderson:"#059669"}};
const HISTORY  = {history_json};

function showPanel(n) {{
  document.querySelectorAll('.panel').forEach(p=>p.classList.remove('active'));
  document.getElementById('panel-'+n).classList.add('active');
}}

function tbl(entries, county) {{
  if (!entries||!entries.length) return '<p class="empty-msg">No obituaries found.</p>';
  const c=COLORS[county]||'#333';
  const rows=entries.map(e=>{{
    const lk=e.link?`<a class="vl" style="background:${{c}}" href="${{e.link}}" target="_blank">View</a>`:'—';
    return `<tr><td><strong>${{e.name}}</strong></td><td style="color:#666;font-size:13px">${{e.date}}</td><td style="color:#666;font-size:13px">${{e.location}}</td><td style="color:#666;font-size:13px">${{e.source}}</td><td>${{lk}}</td></tr>`;
  }}).join('');
  return `<table><thead><tr><th>Name</th><th>Date</th><th>Location</th><th>Source</th><th>Link</th></tr></thead><tbody>${{rows}}</tbody></table>`;
}}

function fmt(d) {{
  const [y,m,day]=d.split('-');
  return ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][+m-1]+' '+day;
}}

function renderToday() {{
  const days=Object.keys(HISTORY).sort().reverse();
  const today=days[0];
  if (!today) return;
  const data=HISTORY[today];
  // all panel
  document.getElementById('panel-all').innerHTML=COUNTIES.map(county=>{{
    const entries=data[county]||[];
    const c=COLORS[county];
    return `<div class="card"><div class="card-title" style="border-color:${{c}};color:${{c}}">${{county}} County <span class="badge">${{entries.length}} found</span></div>${{tbl(entries,county)}}</div>`;
  }}).join('');
  // individual county panels
  [['gvl','Greenville'],['spt','Spartanburg'],['and','Anderson']].forEach(([id,county])=>{{
    const entries=data[county]||[];
    const c=COLORS[county];
    document.getElementById('panel-'+id).innerHTML=`<div class="card"><div class="card-title" style="border-color:${{c}};color:${{c}}">${{county}} County <span class="badge">${{entries.length}} found</span></div>${{tbl(entries,county)}}</div>`;
  }});
}}

function renderHistory() {{
  const days=Object.keys(HISTORY).sort().reverse();
  if (!days.length) return;
  document.getElementById('hist-tabs').innerHTML=days.map((d,i)=>
    `<span class="day-tab ${{i===0?'sel':''}}" onclick="selDay('${{d}}',this)">${{fmt(d)}}</span>`
  ).join('');
  document.getElementById('hist-body').innerHTML=days.map((d,i)=>{{
    const data=HISTORY[d];
    const secs=COUNTIES.map(county=>{{
      const entries=data[county]||[];
      const c=COLORS[county];
      return `<div class="county-seg"><h3 style="color:${{c}};border-left:4px solid ${{c}};padding-left:10px;margin-bottom:10px;font-size:16px">${{county}} County <span class="badge">${{entries.length}}</span></h3>${{tbl(entries,county)}}</div>`;
    }}).join('');
    return `<div id="day-${{d}}" class="day-panel ${{i===0?'active':''}}">${{secs}}</div>`;
  }}).join('');
}}

function selDay(d,el) {{
  document.querySelectorAll('.day-tab').forEach(t=>t.classList.remove('sel'));
  document.querySelectorAll('.day-panel').forEach(p=>p.classList.remove('active'));
  el.classList.add('sel');
  document.getElementById('day-'+d).classList.add('active');
}}

renderToday();
renderHistory();
</script>
</body>
</html>"""

    os.makedirs(DOCS_DIR, exist_ok=True)
    with open(DASHBOARD_OUT, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Dashboard saved -> {DASHBOARD_OUT}")


# ─────────────────────────────────────────────
#  EMAIL
# ─────────────────────────────────────────────

def build_email_html(results, today):
    total = sum(len(v) for v in results.values())
    def make_tbl(entries, county):
        color = COUNTY_COLORS.get(county, "#333")
        if not entries:
            return '<p style="color:#999;font-style:italic;">No results found.</p>'
        rows = "".join(
            f'<tr><td style="padding:7px 10px;border-bottom:1px solid #eee">{e["name"]}</td>'
            f'<td style="padding:7px 10px;border-bottom:1px solid #eee;color:#666;font-size:13px">{e["date"]}</td>'
            f'<td style="padding:7px 10px;border-bottom:1px solid #eee;color:#666;font-size:13px">{e["source"]}</td>'
            f'<td style="padding:7px 10px;border-bottom:1px solid #eee">{"<a href=" + repr(e["link"]) + " style=color:" + repr(color) + ">View</a>" if e.get("link") else "—"}</td></tr>'
            for e in entries
        )
        return (f'<table style="width:100%;border-collapse:collapse;font-size:14px">'
                f'<thead><tr style="background:#f5f5f5">'
                f'<th style="padding:7px 10px;text-align:left;font-size:11px;color:#555;text-transform:uppercase">Name</th>'
                f'<th style="padding:7px 10px;text-align:left;font-size:11px;color:#555;text-transform:uppercase">Date</th>'
                f'<th style="padding:7px 10px;text-align:left;font-size:11px;color:#555;text-transform:uppercase">Source</th>'
                f'<th style="padding:7px 10px;text-align:left;font-size:11px;color:#555;text-transform:uppercase">Link</th>'
                f'</tr></thead><tbody>{rows}</tbody></table>')
    sections = "".join(
        f'<div style="background:white;border-radius:8px;padding:18px;margin-bottom:14px;border:1px solid #e5e7eb">'
        f'<h2 style="color:{COUNTY_COLORS.get(c,"#333")};border-left:4px solid {COUNTY_COLORS.get(c,"#333")};'
        f'padding-left:10px;margin-bottom:12px;font-size:17px">'
        f'{c} County <span style="font-size:13px;font-weight:normal;color:#666">({len(results.get(c,[]))} found)</span></h2>'
        f'{make_tbl(results.get(c,[]),c)}</div>'
        for c in COUNTIES
    )
    return (f'<div style="font-family:Georgia,serif;background:#f5f5f0;padding:20px;max-width:800px;margin:0 auto">'
            f'<div style="background:#1a1a2e;color:white;padding:18px 24px;border-radius:8px;margin-bottom:14px">'
            f'<h1 style="font-size:21px;margin-bottom:4px">South Carolina Obituaries</h1>'
            f'<p style="color:#aaa;font-family:Arial,sans-serif;font-size:13px">Greenville · Spartanburg · Anderson | {today} | {total} total</p></div>'
            f'{sections}'
            f'<p style="text-align:center;color:#999;font-size:11px;font-family:Arial,sans-serif;margin-top:14px">'
            f'Sent by SC Obituary Scraper via GitHub Actions</p></div>')


def build_email_text(results, today):
    lines = [f"SC Obituaries — {today}", "="*50, ""]
    for county in COUNTIES:
        entries = results.get(county, [])
        lines += [f"{county} County ({len(entries)} found)", "-"*30]
        lines += ([f"  {e['name']} | {e['date']} | {e['source']}" + (f"\n    {e['link']}" if e.get('link') else "") for e in entries]
                  if entries else ["  No results found."])
        lines.append("")
    return "\n".join(lines)


def send_email(results, today):
    if not EMAIL_FROM or not EMAIL_PASSWORD:
        print("Email credentials not set — skipping email.")
        return
    if not EMAIL_TO:
        print("EMAIL_TO not set — skipping email.")
        return
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"SC Obituaries — {today}"
    msg["From"]    = EMAIL_FROM
    msg["To"]      = ", ".join(EMAIL_TO)
    msg.attach(MIMEText(build_email_text(results, today), "plain"))
    msg.attach(MIMEText(build_email_html(results, today),  "html"))
    try:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, context=context) as server:
            server.login(EMAIL_FROM, EMAIL_PASSWORD)
            server.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())
        print(f"Email sent to: {', '.join(EMAIL_TO)}")
    except smtplib.SMTPAuthenticationError:
        print("ERROR: Gmail auth failed. Use an App Password from myaccount.google.com/apppasswords")
    except Exception as e:
        print(f"ERROR sending email: {e}")


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────

if __name__ == "__main__":
    results, today, total = scrape_all()
    history = load_history()
    history = save_history(history, today, results)
    save_dashboard(history)
    send_email(results, today)
    print(f"\nDone! History: {HISTORY_FILE} | Dashboard: {DASHBOARD_OUT}")

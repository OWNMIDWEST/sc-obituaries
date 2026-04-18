#!/usr/bin/env python3
"""
SC Obituary Scraper — GitHub Actions edition (v2)
Uses Legacy.com's search API as the primary source (most reliable),
with funeral home sites as secondary sources.
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
#  CONFIG
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

COUNTIES = ["Greenville", "Spartanburg", "Anderson"]

COUNTY_COLORS = {
    "Greenville":  "#2563eb",
    "Spartanburg": "#7c3aed",
    "Anderson":    "#059669",
}

# Legacy.com state/region ID for South Carolina
LEGACY_STATE = "SC"

# Legacy.com newspaper slugs for each county
LEGACY_PAPERS = {
    "Greenville":  ["greenvilleonline", "goupstate"],
    "Spartanburg": ["shj", "goupstate"],
    "Anderson":    ["independentmail"],
}

# Funeral home sites per county — verified working URLs
FUNERAL_HOMES = {
    "Greenville": [
        {"name": "Mackey Mortuary",             "url": "https://www.mackeymortuary.com/obituaries"},
        {"name": "Thomas McAfee Funeral Homes", "url": "https://www.thomasmcafee.com/obituaries"},
        {"name": "Legacy.com Greenville area",  "url": "https://www.legacy.com/us/obituaries/local/south-carolina/greenville-area"},
    ],
    "Spartanburg": [
        {"name": "Floyd Mortuary",              "url": "https://www.floydmortuary.com/obituaries"},
        {"name": "Community Mortuary",          "url": "https://www.communitymortuaryinc.com/listings"},
        {"name": "Bobo Funeral Chapel",         "url": "https://www.bobofuneralchapel.com/listings"},
        {"name": "Roberts Funeral Home",        "url": "https://www.robertsfhsc.com/listings"},
        {"name": "E.L. Collins Funeral Home",   "url": "https://www.elcollinsfh.com/obituaries"},
        {"name": "Legacy.com Spartanburg area", "url": "https://www.legacy.com/us/obituaries/local/south-carolina/spartanburg-area"},
    ],
    "Anderson": [
        {"name": "McDougald Funeral Home",      "url": "https://www.mcdougaldfuneralhome.com/obituaries/obituary-listings"},
        {"name": "Marcus D. Brown Funeral Home","url": "https://www.marcusdbrownfuneralhome.com/listings"},
        {"name": "D.B. Walker Funeral Services","url": "https://www.dbwalkerfuneralservices.com/listings"},
        {"name": "Johnson Funeral Home",        "url": "https://www.johnsonfuneralhm.com/listings"},
        {"name": "Sosebee Mortuary",            "url": "https://sosebeemortuary.com/obituaries"},
        {"name": "Anderson Simple Cremations",  "url": "https://andersonsimplecremations.com/obituary/"},
        {"name": "Legacy.com Anderson area",    "url": "https://www.legacy.com/us/obituaries/local/south-carolina/anderson"},
    ],
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

LEGACY_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.legacy.com/",
    "Origin": "https://www.legacy.com",
}


# ─────────────────────────────────────────────
#  LEGACY.COM API  (primary, most reliable)
# ─────────────────────────────────────────────

def fetch_legacy_api(county):
    """
    Use Legacy.com's internal search API to find obituaries by city/county.
    This is more reliable than scraping their HTML.
    """
    results = []
    today = date.today().strftime("%Y-%m-%d")

    # Search by city names within each county
    city_map = {
        "Greenville":  [
            "Greenville", "Mauldin", "Simpsonville", "Greer", "Taylors",
            "Travelers Rest", "Fountain Inn", "Piedmont", "Greenville SC",
            "Pelham", "Tigerville", "Slater", "Marietta",
        ],
        "Spartanburg": [
            "Spartanburg", "Duncan", "Boiling Springs", "Inman", "Gaffney",
            "Chesnee", "Landrum", "Pauline", "Moore", "Roebuck",
            "Cowpens", "Wellford", "Lyman", "Woodruff", "Spartanburg SC",
        ],
        "Anderson":    [
            "Anderson", "Seneca", "Pendleton", "Williamston", "Belton",
            "Honea Path", "Iva", "Starr", "Townville", "Pelzer",
            "Powdersville", "Anderson SC", "Clemson", "Central",
        ],
    }

    cities = city_map.get(county, [county])
    seen_names = set()

    for city in cities:
        try:
            # Legacy.com search endpoint
            url = (
                f"https://www.legacy.com/api/obituary/search"
                f"?firstName=&lastName=&state={LEGACY_STATE}"
                f"&city={requests.utils.quote(city)}"
                f"&limit=50&offset=0"
            )
            resp = requests.get(url, headers=LEGACY_HEADERS, timeout=15)

            if resp.status_code == 200:
                data = resp.json()
                obits = data.get("obituaries", data.get("results", data.get("data", [])))

                for obit in obits:
                    name = (
                        obit.get("fullName")
                        or f"{obit.get('firstName','')} {obit.get('lastName','')}".strip()
                    )
                    if not name or name in seen_names:
                        continue
                    seen_names.add(name)

                    pub_date = (
                        obit.get("publishDate", "")
                        or obit.get("deathDate", "")
                        or obit.get("createdDate", today)
                    )[:10]

                    obit_city = (
                        obit.get("city", "")
                        or obit.get("residenceCity", city)
                    )
                    location = f"{obit_city}, SC" if obit_city else f"{county} County, SC"

                    slug = obit.get("slug", obit.get("id", ""))
                    link = f"https://www.legacy.com/us/obituaries/name/{slug}" if slug else ""

                    results.append({
                        "name":     name,
                        "date":     pub_date,
                        "location": location,
                        "source":   "Legacy.com",
                        "county":   county,
                        "link":     link,
                    })

            time.sleep(0.5)

        except Exception as e:
            print(f"  [Legacy API error for {city}]: {e}")

    # Fallback 1: try newspaper pages directly
    if not results:
        results = fetch_legacy_newspaper_pages(county, seen_names)

    # Fallback 2: try Legacy.com county search page
    if not results:
        results = fetch_legacy_county_page(county, seen_names)

    print(f"  Legacy.com -> {len(results)} found for {county}")
    return results


def fetch_legacy_county_page(county, seen_names=None):
    """Scrape Legacy.com county search page as last resort."""
    if seen_names is None:
        seen_names = set()
    results = []
    url = f"https://www.legacy.com/obituaries/search?countryId=1&regionId=42&countyName={requests.utils.quote(county)}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            print(f"  [Legacy county page HTTP {resp.status_code}]")
            return []
        soup = BeautifulSoup(resp.text, "html.parser")

        # Try to find next-data JSON embedded in page
        script = soup.find("script", id="__NEXT_DATA__")
        if script:
            try:
                data = json.loads(script.string)
                # Walk the JSON tree looking for obituary lists
                def find_obits(obj, depth=0):
                    if depth > 8:
                        return []
                    found = []
                    if isinstance(obj, list):
                        for item in obj:
                            if isinstance(item, dict) and ("fullName" in item or "firstName" in item):
                                found.append(item)
                            else:
                                found.extend(find_obits(item, depth+1))
                    elif isinstance(obj, dict):
                        for v in obj.values():
                            found.extend(find_obits(v, depth+1))
                    return found
                obits = find_obits(data)
                for obit in obits:
                    name = obit.get("fullName") or f"{obit.get('firstName','')} {obit.get('lastName','')}".strip()
                    if not name or name in seen_names or len(name) < 4:
                        continue
                    seen_names.add(name)
                    slug = obit.get("slug", obit.get("id", ""))
                    link = f"https://www.legacy.com/us/obituaries/name/{slug}" if slug else ""
                    results.append({
                        "name":     name,
                        "date":     obit.get("publishDate", obit.get("deathDate", "See source"))[:10] if obit.get("publishDate") or obit.get("deathDate") else "See source",
                        "location": f"{obit.get('city', county)}, SC",
                        "source":   "Legacy.com (county search)",
                        "county":   county,
                        "link":     link,
                    })
            except Exception as e:
                print(f"  [Legacy county JSON parse error]: {e}")

        # Also try plain HTML scraping
        if not results:
            for card in soup.find_all(True, class_=re.compile(r"obit|listing|result|card", re.I)):
                name_el = card.find(class_=re.compile(r"name|title", re.I)) or card.find(["h2","h3","h4"])
                if not name_el:
                    continue
                name = name_el.get_text(strip=True)
                if not name or len(name) < 4 or name in seen_names:
                    continue
                if any(w in name.lower() for w in ["obituar","search","legacy","home"]):
                    continue
                seen_names.add(name)
                link_el = card.find("a", href=True)
                href = link_el["href"] if link_el else ""
                link = href if href.startswith("http") else ("https://www.legacy.com" + href if href.startswith("/") else "")
                results.append({
                    "name": name, "date": "See source",
                    "location": f"{county} County, SC",
                    "source": "Legacy.com (county search)",
                    "county": county, "link": link,
                })
    except Exception as e:
        print(f"  [Legacy county page error]: {e}")

    print(f"  Legacy county page -> {len(results)} found")
    return results


def fetch_legacy_newspaper_pages(county, seen_names=None):
    """Scrape Legacy.com newspaper obituary pages as fallback."""
    if seen_names is None:
        seen_names = set()
    results = []
    papers = LEGACY_PAPERS.get(county, [])

    for paper in papers:
        url = f"https://www.legacy.com/us/obituaries/{paper}/recent"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            if resp.status_code != 200:
                continue
            soup = BeautifulSoup(resp.text, "html.parser")

            # Try multiple selector patterns Legacy uses
            cards = (
                soup.find_all("div", attrs={"data-component": re.compile(r"obit|Obit|listing", re.I)})
                or soup.find_all("li", class_=re.compile(r"obit|listing|result", re.I))
                or soup.find_all("article")
                or soup.find_all("div", class_=re.compile(r"obit|Obit|card|listing|result", re.I))
            )

            for card in cards:
                # Try to find name
                name_el = (
                    card.find(attrs={"data-component": re.compile(r"name|Name", re.I)})
                    or card.find(class_=re.compile(r"name|Name|title|Title", re.I))
                    or card.find(["h2", "h3", "h4"])
                    or card.find("a")
                )
                if not name_el:
                    continue
                name = name_el.get_text(strip=True)
                if not name or len(name) < 4 or name in seen_names:
                    continue
                if any(w in name.lower() for w in ["obituar", "legacy", "home", "search", "menu"]):
                    continue
                seen_names.add(name)

                link_el = card.find("a", href=True)
                href = link_el["href"] if link_el else ""
                link = href if href.startswith("http") else ("https://www.legacy.com" + href if href.startswith("/") else "")

                date_el = card.find(class_=re.compile(r"date|Date|death|born", re.I))
                obit_date = date_el.get_text(strip=True) if date_el else "See source"

                results.append({
                    "name":     name,
                    "date":     obit_date,
                    "location": f"{county} County, SC",
                    "source":   f"Legacy.com ({paper})",
                    "county":   county,
                    "link":     link,
                })

        except Exception as e:
            print(f"  [Legacy page error {paper}]: {e}")
        time.sleep(1)

    return results


# ─────────────────────────────────────────────
#  FUNERAL HOME SCRAPER  (secondary sources)
# ─────────────────────────────────────────────

def fetch_funeral_home(name, url, county):
    """
    Aggressively scrape a funeral home obituary page.
    Tries many different HTML patterns used by common funeral home website builders.
    """
    results = []
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            print(f"  [{resp.status_code}] {name}")
            return []
    except Exception as e:
        print(f"  [Error] {name}: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    seen = set()

    # Strategy 1: structured data (JSON-LD) — most reliable when present
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            items = data if isinstance(data, list) else [data]
            for item in items:
                if item.get("@type") in ("Person", "Obituary", "Event"):
                    n = item.get("name", "")
                    if n and n not in seen and len(n) > 3:
                        seen.add(n)
                        results.append({
                            "name":     n,
                            "date":     item.get("deathDate", item.get("startDate", "See source")),
                            "location": f"{county} County, SC",
                            "source":   name,
                            "county":   county,
                            "link":     item.get("url", url),
                        })
        except Exception:
            pass

    if results:
        return results

    # Strategy 2: common CSS class patterns used by funeral home site builders
    # (Tribute Archive, FrontRunner, SRS Computing, Domani, etc.)
    selectors = [
        {"class": re.compile(r"obit.?name|deceased.?name|obituary.?name", re.I)},
        {"class": re.compile(r"fn|fullname|full.name", re.I)},
        {"itemprop": "name"},
        {"class": re.compile(r"obit|obituary|deceased|memorial|tribute", re.I)},
    ]

    for sel in selectors:
        tags = soup.find_all(True, attrs=sel)
        for tag in tags:
            n = tag.get_text(strip=True)
            if not n or len(n) < 4 or n in seen:
                continue
            if any(w in n.lower() for w in ["home", "about", "contact", "service", "menu",
                                             "obituar", "search", "phone", "address"]):
                continue
            # Must look like a name (2+ words, mostly letters)
            if len(n.split()) < 2:
                continue
            if sum(c.isalpha() or c in " .,'-" for c in n) / max(len(n), 1) < 0.8:
                continue
            seen.add(n)

            # Try to find link and date near this element
            parent = tag.parent
            link_el = tag.find("a", href=True) or (parent.find("a", href=True) if parent else None)
            href = link_el["href"] if link_el else ""
            if href and not href.startswith("http"):
                base = "/".join(url.split("/")[:3])
                href = base + href if href.startswith("/") else ""
            date_el = (tag.find_next(class_=re.compile(r"date|death|born|age", re.I))
                       or (parent.find(class_=re.compile(r"date|death|born|age", re.I)) if parent else None))
            obit_date = date_el.get_text(strip=True) if date_el else "See source"

            results.append({
                "name":     n,
                "date":     obit_date,
                "location": f"{county} County, SC",
                "source":   name,
                "county":   county,
                "link":     href or url,
            })

        if results:
            break

    # Strategy 3: look for heading tags near "obituar" context
    if not results:
        for heading in soup.find_all(["h1", "h2", "h3", "h4"]):
            n = heading.get_text(strip=True)
            if not n or len(n) < 4 or n in seen:
                continue
            # Skip non-name headings
            if any(w in n.lower() for w in ["obituar", "home", "about", "service",
                                             "contact", "welcome", "funeral", "recent"]):
                continue
            if len(n.split()) < 2 or len(n) > 60:
                continue
            if sum(c.isalpha() or c in " .,'-" for c in n) / max(len(n), 1) < 0.8:
                continue

            # Check nearby context for obituary-related words
            parent = heading.parent
            context = parent.get_text(" ", strip=True).lower() if parent else ""
            if not any(w in context for w in ["born", "passed", "survived", "memorial",
                                               "funeral", "service", "died", "death"]):
                continue
            seen.add(n)
            link_el = heading.find("a", href=True) or heading.find_next("a", href=True)
            href = link_el["href"] if link_el else ""
            if href and not href.startswith("http"):
                base = "/".join(url.split("/")[:3])
                href = base + href if href.startswith("/") else ""
            results.append({
                "name":     n,
                "date":     "See source",
                "location": f"{county} County, SC",
                "source":   name,
                "county":   county,
                "link":     href or url,
            })

    return results


# ─────────────────────────────────────────────
#  MAIN SCRAPE
# ─────────────────────────────────────────────

def scrape_all():
    today = date.today().strftime("%Y-%m-%d")
    all_results = {county: [] for county in COUNTIES}
    total = 0

    print(f"\n{'='*60}\n  SC Obituary Scraper v2 — {today}\n{'='*60}\n")

    for county in COUNTIES:
        print(f"[{county} County]")
        county_results = []
        seen_names = set()

        # Primary: Legacy.com API
        print("  Trying Legacy.com API ...")
        legacy_results = fetch_legacy_api(county)
        for r in legacy_results:
            if r["name"] not in seen_names:
                seen_names.add(r["name"])
                county_results.append(r)
        time.sleep(1)

        # Secondary: funeral homes
        for fh in FUNERAL_HOMES.get(county, []):
            print(f"  Trying {fh['name']} ...")
            fh_results = fetch_funeral_home(fh["name"], fh["url"], county)
            added = 0
            for r in fh_results:
                if r["name"] not in seen_names:
                    seen_names.add(r["name"])
                    county_results.append(r)
                    added += 1
            print(f"  -> {added} new")
            time.sleep(1)

        print(f"  Total for {county}: {len(county_results)}\n")
        all_results[county] = county_results
        total += len(county_results)

    print(f"Grand total: {total} obituaries\n")
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
    print(f"History saved ({len(history)} days)")
    return history


# ─────────────────────────────────────────────
#  DASHBOARD
# ─────────────────────────────────────────────

def save_dashboard(history):
    history_json = json.dumps(history, ensure_ascii=False)
    today_key = sorted(history.keys())[-1] if history else ""
    updated = today_key or datetime.now().strftime("%Y-%m-%d")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1.0"/>
<title>SC Obituary Dashboard</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:Georgia,serif;background:#f5f5f0;color:#222;padding:16px}}
.hdr{{background:#1a1a2e;color:white;padding:20px 24px;border-radius:8px;margin-bottom:18px}}
.hdr h1{{font-size:22px;margin-bottom:4px}}
.hdr p{{color:#aaa;font-size:13px;font-family:Arial,sans-serif}}
.nav{{display:flex;gap:8px;margin-bottom:16px;flex-wrap:wrap;align-items:center}}
.tab{{padding:8px 18px;border-radius:20px;border:none;cursor:pointer;font-family:Arial,sans-serif;font-size:13px;font-weight:600}}
.panel{{display:none}}.panel.active{{display:block}}
.card{{background:white;border-radius:8px;padding:20px;margin-bottom:16px;box-shadow:0 1px 4px rgba(0,0,0,.08)}}
.ctitle{{font-size:18px;font-weight:bold;border-left:5px solid;padding-left:12px;margin-bottom:14px}}
.badge{{font-family:Arial,sans-serif;font-size:11px;background:#f3f4f6;color:#555;padding:2px 9px;border-radius:12px;margin-left:8px;font-weight:normal}}
table{{width:100%;border-collapse:collapse;font-size:14px}}
thead tr{{background:#f0f0f0}}
th{{padding:8px 10px;text-align:left;font-family:Arial,sans-serif;font-size:11px;text-transform:uppercase;letter-spacing:.4px;color:#555}}
td{{padding:9px 10px;border-bottom:1px solid #eee;vertical-align:top}}
tr:last-child td{{border-bottom:none}}
tr:hover td{{background:#fafafa}}
a.vl{{text-decoration:none;font-size:12px;font-family:Arial,sans-serif;padding:3px 10px;border-radius:10px;color:white}}
.empty{{color:#999;font-style:italic;padding:8px 0;font-family:Arial,sans-serif;font-size:13px}}
.day-tab{{display:inline-block;padding:5px 14px;border-radius:16px;cursor:pointer;font-family:Arial,sans-serif;font-size:12px;font-weight:600;margin:3px;background:#f3f4f6;color:#374151;border:1px solid #e5e7eb}}
.day-tab.sel{{background:#b45309;color:white;border-color:#b45309}}
.day-panel{{display:none}}.day-panel.active{{display:block}}
.src-row{{display:flex;align-items:center;justify-content:space-between;padding:9px 12px;border-radius:6px;margin-bottom:6px;background:#f9f9f9;border:1px solid #eee}}
.src-btn{{padding:5px 14px;border-radius:12px;border:none;font-size:12px;font-weight:600;color:white;text-decoration:none;display:inline-block}}
</style>
</head>
<body>
<div class="hdr">
  <h1>SC Obituary Dashboard</h1>
  <p>Greenville &nbsp;·&nbsp; Spartanburg &nbsp;·&nbsp; Anderson &nbsp;|&nbsp; Updated: {updated}</p>
</div>
<div class="nav">
  <button class="tab" style="background:#374151;color:white" onclick="show('all')">Today — All</button>
  <button class="tab" style="background:#2563eb;color:white" onclick="show('gvl')">Greenville</button>
  <button class="tab" style="background:#7c3aed;color:white" onclick="show('spt')">Spartanburg</button>
  <button class="tab" style="background:#059669;color:white" onclick="show('and')">Anderson</button>
  <button class="tab" style="background:#b45309;color:white" onclick="show('hist')">7-Day History</button>
  <button class="tab" style="background:#0891b2;color:white" onclick="show('dl')">Downloads</button>
  <button class="tab" style="background:#e5e7eb;color:#374151;margin-left:auto" onclick="show('src')">Sources</button>
</div>
<div id="p-all"  class="panel active"></div>
<div id="p-gvl"  class="panel"></div>
<div id="p-spt"  class="panel"></div>
<div id="p-and"  class="panel"></div>
<div id="p-dl"   class="panel"></div>
<div id="p-hist" class="panel">
  <div class="card">
    <div class="ctitle" style="border-color:#b45309;color:#b45309;">7-Day History</div>
    <div id="dtabs" style="margin-bottom:14px;"></div>
    <div id="dbody"></div>
  </div>
</div>
<div id="p-src" class="panel">
  <div class="card">
    <div class="ctitle" style="border-color:#2563eb;color:#2563eb;">Greenville Sources</div>
    <div class="src-row"><div><strong>Legacy.com</strong><br><small style="color:#888">Newspaper aggregator</small></div><a class="src-btn" style="background:#2563eb" href="https://www.legacy.com/obituaries/search?countryId=1&regionId=42&countyName=Greenville" target="_blank">Open</a></div>
    <div class="src-row"><div><strong>Robinson Funeral Homes</strong></div><a class="src-btn" style="background:#2563eb" href="https://www.robinsonfuneralhomes.com/obituaries" target="_blank">Open</a></div>
    <div class="src-row"><div><strong>Thomas McAfee Funeral Homes</strong></div><a class="src-btn" style="background:#2563eb" href="https://www.thomasmcafee.com/obituaries" target="_blank">Open</a></div>
    <div class="src-row"><div><strong>Geo. W. Harley Funeral Home</strong></div><a class="src-btn" style="background:#2563eb" href="https://www.georgewharleyfuneralhome.com/obituaries" target="_blank">Open</a></div>
  </div>
  <div class="card">
    <div class="ctitle" style="border-color:#7c3aed;color:#7c3aed;">Spartanburg Sources</div>
    <div class="src-row"><div><strong>Legacy.com</strong><br><small style="color:#888">Newspaper aggregator</small></div><a class="src-btn" style="background:#7c3aed" href="https://www.legacy.com/obituaries/search?countryId=1&regionId=42&countyName=Spartanburg" target="_blank">Open</a></div>
    <div class="src-row"><div><strong>Floyd's Mortuary</strong></div><a class="src-btn" style="background:#7c3aed" href="https://www.floydsmortuary.com/obituaries" target="_blank">Open</a></div>
    <div class="src-row"><div><strong>Spartanburg Mortuary</strong></div><a class="src-btn" style="background:#7c3aed" href="https://www.spartanburgmortuary.com/obituaries" target="_blank">Open</a></div>
  </div>
  <div class="card">
    <div class="ctitle" style="border-color:#059669;color:#059669;">Anderson Sources</div>
    <div class="src-row"><div><strong>Legacy.com</strong><br><small style="color:#888">Newspaper aggregator</small></div><a class="src-btn" style="background:#059669" href="https://www.legacy.com/obituaries/search?countryId=1&regionId=42&countyName=Anderson" target="_blank">Open</a></div>
    <div class="src-row"><div><strong>McDougald Funeral Home</strong></div><a class="src-btn" style="background:#059669" href="https://www.mcdougaldfuneralhome.com/obituaries" target="_blank">Open</a></div>
    <div class="src-row"><div><strong>Sullivan-King Mortuary</strong></div><a class="src-btn" style="background:#059669" href="https://www.sullivankingmortuary.com/obituaries" target="_blank">Open</a></div>
  </div>
</div>

<script>
const C=['Greenville','Spartanburg','Anderson'];
const CLR={{Greenville:'#2563eb',Spartanburg:'#7c3aed',Anderson:'#059669'}};
const H={history_json};

function show(n){{
  document.querySelectorAll('.panel').forEach(p=>p.classList.remove('active'));
  document.getElementById('p-'+n).classList.add('active');
}}

function tbl(entries,county){{
  if(!entries||!entries.length) return '<p class="empty">No obituaries found — the websites may have changed or had no new postings today.</p>';
  const c=CLR[county]||'#333';
  return '<table><thead><tr><th>Name</th><th>Date</th><th>Location</th><th>Source</th><th>Link</th></tr></thead><tbody>'
    +entries.map(e=>`<tr><td><strong>${{e.name}}</strong></td><td style="color:#666;font-size:13px">${{e.date}}</td><td style="color:#666;font-size:13px">${{e.location}}</td><td style="color:#666;font-size:13px">${{e.source}}</td><td>${{e.link?`<a class="vl" style="background:${{c}}" href="${{e.link}}" target="_blank">View</a>`:'—'}}</td></tr>`).join('')
    +'</tbody></table>';
}}

function fmt(d){{
  const[y,m,day]=d.split('-');
  return ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][+m-1]+' '+day;
}}

function renderToday(){{
  const days=Object.keys(H).sort().reverse();
  if(!days.length) return;
  const data=H[days[0]];
  document.getElementById('p-all').innerHTML=C.map(co=>{{
    const e=data[co]||[];
    const c=CLR[co];
    return `<div class="card"><div class="ctitle" style="border-color:${{c}};color:${{c}}">${{co}} County <span class="badge">${{e.length}} found</span></div>${{tbl(e,co)}}</div>`;
  }}).join('');
  [['gvl','Greenville'],['spt','Spartanburg'],['and','Anderson']].forEach(([id,co])=>{{
    const e=data[co]||[];const c=CLR[co];
    document.getElementById('p-'+id).innerHTML=`<div class="card"><div class="ctitle" style="border-color:${{c}};color:${{c}}">${{co}} County <span class="badge">${{e.length}} found</span></div>${{tbl(e,co)}}</div>`;
  }});
}}

function renderHistory(){{
  const days=Object.keys(H).sort().reverse();
  if(!days.length) return;
  document.getElementById('dtabs').innerHTML=days.map((d,i)=>`<span class="day-tab ${{i===0?'sel':''}}" onclick="selDay('${{d}}',this)">${{fmt(d)}}</span>`).join('');
  document.getElementById('dbody').innerHTML=days.map((d,i)=>{{
    const data=H[d];
    return `<div id="day-${{d}}" class="day-panel ${{i===0?'active':''}}">`+C.map(co=>{{
      const e=data[co]||[];const c=CLR[co];
      return `<div style="margin-bottom:20px"><h3 style="color:${{c}};border-left:4px solid ${{c}};padding-left:10px;margin-bottom:10px;font-size:16px">${{co}} County <span class="badge">${{e.length}}</span></h3>${{tbl(e,co)}}</div>`;
    }}).join('')+'</div>';
  }}).join('');
}}

function selDay(d,el){{
  document.querySelectorAll('.day-tab').forEach(t=>t.classList.remove('sel'));
  document.querySelectorAll('.day-panel').forEach(p=>p.classList.remove('active'));
  el.classList.add('sel');document.getElementById('day-'+d).classList.add('active');
}}

/* ── DOWNLOADS ── */
function toCSV(rows){{
  const hdr='Name,Date,Location,County,Source,Link';
  const lines=rows.map(e=>
    [e.name,e.date,e.location,e.county,e.source,e.link||'']
      .map(v=>'"'+String(v).replace(/"/g,'""')+'"').join(',')
  );
  return [hdr,...lines].join('\r\n');
}}

function downloadFile(content,filename,mime){{
  const blob=new Blob([content],{{type:mime}});
  const a=document.createElement('a');
  a.href=URL.createObjectURL(blob);
  a.download=filename;
  a.click();
  URL.revokeObjectURL(a.href);
}}

function dlDayCSV(dateStr){{
  const data=H[dateStr];
  if(!data)return;
  const rows=C.flatMap(co=>data[co]||[]);
  downloadFile(toCSV(rows),`sc_obituaries_${{dateStr}}.csv`,'text/csv');
}}

function dlDayJSON(dateStr){{
  const data=H[dateStr];
  if(!data)return;
  downloadFile(JSON.stringify({{date:dateStr,counties:data}},null,2),`sc_obituaries_${{dateStr}}.json`,'application/json');
}}

function dlWeekCSV(){{
  const rows=Object.entries(H).flatMap(([d,data])=>
    C.flatMap(co=>(data[co]||[]).map(e=>{{return{{...e,scraped_date:d}}}}))
  );
  const hdr='Name,Date,Location,County,Source,Link,Scraped Date';
  const lines=rows.map(e=>
    [e.name,e.date,e.location,e.county,e.source,e.link||'',e.scraped_date]
      .map(v=>'"'+String(v).replace(/"/g,'""')+'"').join(',')
  );
  downloadFile([hdr,...lines].join('\r\n'),'sc_obituaries_7day.csv','text/csv');
}}

function dlWeekJSON(){{
  downloadFile(JSON.stringify(H,null,2),'sc_obituaries_7day.json','application/json');
}}

function renderDownloads(){{
  const days=Object.keys(H).sort().reverse();
  const today=days[0]||'';
  const totalToday=today?C.reduce((n,co)=>n+(H[today][co]||[]).length,0):0;
  const totalWeek=Object.values(H).reduce((n,d)=>n+C.reduce((m,co)=>m+(d[co]||[]).length,0),0);
  document.getElementById('p-dl').innerHTML=`
    <div class="card">
      <div class="ctitle" style="border-color:#0891b2;color:#0891b2;">Download Today's Data</div>
      <p style="color:#666;font-family:Arial,sans-serif;font-size:13px;margin-bottom:14px;">${{today||'No data yet'}} &nbsp;·&nbsp; ${{totalToday}} obituaries</p>
      <div style="display:flex;gap:10px;flex-wrap:wrap;">
        <button onclick="dlDayCSV('${{today}}')" style="padding:9px 20px;background:#0891b2;color:white;border:none;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer;">Download CSV</button>
        <button onclick="dlDayJSON('${{today}}')" style="padding:9px 20px;background:#0e7490;color:white;border:none;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer;">Download JSON</button>
      </div>
    </div>
    <div class="card">
      <div class="ctitle" style="border-color:#0891b2;color:#0891b2;">Download Full 7-Day History</div>
      <p style="color:#666;font-family:Arial,sans-serif;font-size:13px;margin-bottom:14px;">${{days.length}} days stored &nbsp;·&nbsp; ${{totalWeek}} total obituaries</p>
      <div style="display:flex;gap:10px;flex-wrap:wrap;">
        <button onclick="dlWeekCSV()" style="padding:9px 20px;background:#0891b2;color:white;border:none;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer;">Download 7-Day CSV</button>
        <button onclick="dlWeekJSON()" style="padding:9px 20px;background:#0e7490;color:white;border:none;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer;">Download 7-Day JSON</button>
      </div>
    </div>
    <div class="card">
      <div class="ctitle" style="border-color:#0891b2;color:#0891b2;">Download by Day</div>
      <p style="color:#666;font-family:Arial,sans-serif;font-size:13px;margin-bottom:14px;">Pick a specific day to download</p>
      ${{days.map(d=>{{
        const cnt=C.reduce((n,co)=>n+(H[d][co]||[]).length,0);
        return `<div style="display:flex;align-items:center;justify-content:space-between;padding:10px 12px;border-radius:6px;margin-bottom:6px;background:#f9f9f9;border:1px solid #eee;">
          <span style="font-family:Arial,sans-serif;font-size:13px;font-weight:500;">${{fmt(d)}} &nbsp;<span style="color:#888;font-weight:normal;">${{cnt}} results</span></span>
          <div style="display:flex;gap:8px;">
            <button onclick="dlDayCSV('${{d}}')" style="padding:5px 12px;background:#0891b2;color:white;border:none;border-radius:6px;font-size:12px;font-weight:600;cursor:pointer;">CSV</button>
            <button onclick="dlDayJSON('${{d}}')" style="padding:5px 12px;background:#0e7490;color:white;border:none;border-radius:6px;font-size:12px;font-weight:600;cursor:pointer;">JSON</button>
          </div>
        </div>`;
      }}).join('')}}
    </div>
    <div class="card">
      <div class="ctitle" style="border-color:#374151;color:#374151;">Raw history file</div>
      <p style="color:#666;font-family:Arial,sans-serif;font-size:13px;margin-bottom:12px;">The master history file lives in your GitHub repo and is updated every time the scraper runs.</p>
      <a href="https://ownmidwest.github.io/sc-obituaries/sc_obituaries_history.json" target="_blank" style="padding:9px 20px;background:#374151;color:white;border-radius:8px;font-size:13px;font-weight:600;text-decoration:none;display:inline-block;">View raw JSON file</a>
    </div>`;
}}

renderToday();renderHistory();renderDownloads();
</script>
</body></html>"""

    os.makedirs(DOCS_DIR, exist_ok=True)
    with open(DASHBOARD_OUT, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Dashboard saved -> {DASHBOARD_OUT}")


# ─────────────────────────────────────────────
#  EMAIL
# ─────────────────────────────────────────────

def send_email(results, today):
    if not EMAIL_FROM or not EMAIL_PASSWORD or not EMAIL_TO:
        print("Email not configured — skipping.")
        return

    total = sum(len(v) for v in results.values())

    def rows(entries, county):
        color = COUNTY_COLORS.get(county, "#333")
        if not entries:
            return '<tr><td colspan="4" style="color:#999;font-style:italic;padding:8px">No results found today.</td></tr>'
        return "".join(
            f'<tr><td style="padding:7px 10px;border-bottom:1px solid #eee"><strong>{e["name"]}</strong></td>'
            f'<td style="padding:7px 10px;border-bottom:1px solid #eee;color:#666;font-size:13px">{e["date"]}</td>'
            f'<td style="padding:7px 10px;border-bottom:1px solid #eee;color:#666;font-size:13px">{e["source"]}</td>'
            f'<td style="padding:7px 10px;border-bottom:1px solid #eee">{"<a href=" + repr(e["link"]) + " style=color:" + repr(color) + ">View</a>" if e.get("link") else "—"}</td></tr>'
            for e in entries
        )

    sections = "".join(
        f'<div style="background:white;border-radius:8px;padding:18px;margin-bottom:14px;border:1px solid #e5e7eb">'
        f'<h2 style="color:{COUNTY_COLORS.get(c,"#333")};border-left:4px solid {COUNTY_COLORS.get(c,"#333")};padding-left:10px;margin-bottom:12px;font-size:17px">'
        f'{c} County <span style="font-size:13px;font-weight:normal;color:#666">({len(results.get(c,[]))} found)</span></h2>'
        f'<table style="width:100%;border-collapse:collapse;font-size:14px">'
        f'<thead><tr style="background:#f5f5f5"><th style="padding:7px 10px;text-align:left;font-size:11px;color:#555;text-transform:uppercase">Name</th>'
        f'<th style="padding:7px 10px;text-align:left;font-size:11px;color:#555;text-transform:uppercase">Date</th>'
        f'<th style="padding:7px 10px;text-align:left;font-size:11px;color:#555;text-transform:uppercase">Source</th>'
        f'<th style="padding:7px 10px;text-align:left;font-size:11px;color:#555;text-transform:uppercase">Link</th></tr></thead>'
        f'<tbody>{rows(results.get(c,[]),c)}</tbody></table></div>'
        for c in COUNTIES
    )

    html_body = (
        f'<div style="font-family:Georgia,serif;background:#f5f5f0;padding:20px;max-width:800px;margin:0 auto">'
        f'<div style="background:#1a1a2e;color:white;padding:18px 24px;border-radius:8px;margin-bottom:14px">'
        f'<h1 style="font-size:21px;margin-bottom:4px">South Carolina Obituaries</h1>'
        f'<p style="color:#aaa;font-family:Arial,sans-serif;font-size:13px">Greenville · Spartanburg · Anderson | {today} | {total} total</p></div>'
        f'{sections}'
        f'<p style="text-align:center;color:#999;font-size:11px;font-family:Arial,sans-serif;margin-top:14px">Sent by SC Obituary Scraper via GitHub Actions</p></div>'
    )

    text_body = f"SC Obituaries — {today}\n" + "="*50 + "\n\n" + "\n".join(
        f"{c} County ({len(results.get(c,[]))} found)\n" + "-"*30 + "\n" +
        ("\n".join(f"  {e['name']} | {e['date']} | {e['source']}" for e in results.get(c,[])) or "  No results.") + "\n"
        for c in COUNTIES
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"SC Obituaries — {today}"
    msg["From"]    = EMAIL_FROM
    msg["To"]      = ", ".join(EMAIL_TO)
    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, context=context) as server:
            server.login(EMAIL_FROM, EMAIL_PASSWORD)
            server.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())
        print(f"Email sent to: {', '.join(EMAIL_TO)}")
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
    print(f"\nDone! {total} obituaries found for {today}.")

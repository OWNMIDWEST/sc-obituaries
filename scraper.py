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
# DASHBOARD_OUT is no longer used — index.html is managed separately
# DASHBOARD_OUT = os.path.join(DOCS_DIR, "index.html")
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
#  DATE EXTRACTION HELPER
# ─────────────────────────────────────────────

_DATE_PATS = [
    # "passed/died/departed on April 17, 2026"
    re.compile(
        r'(?:passed(?:\s+away)?|died|departed|entered[^.]*rest|called[^.]*home|born)'
        r'\s+(?:on\s+)?'
        r'((?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|'
        r'Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)'
        r'\s+\d{1,2},?\s+20\d\d)', re.I),
    # Standalone "April 17, 2026"
    re.compile(
        r'\b((?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|'
        r'Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)'
        r'\s+\d{1,2},?\s+20\d\d)\b', re.I),
    # ISO: 2026-04-17
    re.compile(r'\b(20\d\d-\d{2}-\d{2})\b'),
    # US: 04/17/2026
    re.compile(r'\b(\d{1,2}/\d{1,2}/20\d\d)\b'),
]

def extract_date(text):
    if not text:
        return ""
    text = " ".join(text.split())
    for pat in _DATE_PATS:
        m = pat.search(text)
        if m:
            return (m.group(1) if m.lastindex else m.group(0)).strip()
    return ""

def best_date(*texts):
    """Try each text in order, return first date found."""
    for t in texts:
        if not t:
            continue
        d = extract_date(t if isinstance(t, str) else t.get_text(" ", strip=True))
        if d:
            return d
    return ""


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

def is_real_name(text):
    """
    Return True only if text looks like an actual person's name.
    Rejects cities, states, buttons, headings, and other page junk.
    """
    if not text:
        return False
    t = text.strip()

    # Length bounds
    if len(t) < 5 or len(t) > 80:
        return False

    # Must have at least 2 words
    words = t.split()
    if len(words) < 2:
        return False

    # Every word must start with a capital letter (names are Title Case)
    # Allow particles: de, van, von, la, le, of, jr, sr, ii, iii, iv
    particles = {"de","van","von","la","le","of","jr","sr","ii","iii","iv","the","and","rev","dr","mr","mrs","ms","prof","col","sgt","cpl","pvt","lt","capt","maj","gen","brig","cdr"}
    for w in words:
        clean = w.strip(".,\'\"-")
        if not clean:
            continue
        if clean.lower() in particles:
            continue
        clean = clean.rstrip(".")
        if not clean:
            continue
        if not clean[0].isupper():
            return False

    # Must be mostly letters, spaces, and name punctuation
    letter_ratio = sum(c.isalpha() or c in " .,\'-\"" for c in t) / len(t)
    if letter_ratio < 0.85:
        return False

    # Hard reject list — common non-name strings found on funeral home pages
    reject_words = [
        "send flowers", "add a memory", "share obituary", "plant a tree",
        "light a candle", "sign guestbook", "leave condolence", "view obituary",
        "read more", "learn more", "click here", "see more", "load more",
        "all obituaries", "recent obituaries", "obituary listing",
        "funeral home", "mortuary", "cremation", "chapel", "memorial",
        "arrangements", "services held", "visitation", "graveside",
        "south carolina", "north carolina", "georgia", "tennessee",
        "county, sc", "county, nc", ", sc", ", nc", ", ga",
        "home", "about", "contact", "directions", "staff", "careers",
        "preplanning", "pre-planning", "grief support", "resources",
        "privacy policy", "terms of use", "accessibility",
        "phone", "fax", "email", "address", "copyright",
        "facebook", "twitter", "instagram", "youtube",
        "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
        "january", "february", "march", "april", "may", "june",
        "july", "august", "september", "october", "november", "december",
        " 7am", " 8am", " 9am", " 10am", " 11am", " 12pm", " 1pm", " 2pm", " 3pm",
    ]
    tl = t.lower()
    if any(rw in tl for rw in reject_words):
        return False

    # Reject if it looks like a city/state pattern: "Word, XX" e.g. "Union, SC"
    if re.match(r'^[A-Z][a-z]+(?: [A-Z][a-z]+)*, [A-Z]{2}$', t):
        return False

    # Reject pure ALL CAPS (usually headings/buttons)
    if t.upper() == t and len(t) > 6:
        return False

    # Reject if contains digits (addresses, phone numbers, dates)
    if any(c.isdigit() for c in t):
        return False

    # Must contain at least one vowel per word (real names have vowels)
    particles_lower = {"de","van","von","la","le","of","jr","sr","ii","iii","iv","the","and","rev","dr","mr","mrs","ms","prof","col","sgt","cpl","pvt","lt","capt","maj","gen","brig","cdr","st","mt"}
    for w in words:
        clean = w.strip(".,\'-\"").rstrip(".").lower()
        if clean in particles_lower:
            continue
        if len(clean) > 2 and not any(v in clean for v in "aeiou"):
            return False

    return True


def fetch_funeral_home(name, url, county):
    """Scrape a funeral home obituary page with strict name validation."""
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
    base_url = "/".join(url.split("/")[:3])

    def make_link(tag):
        a = tag.find("a", href=True) if tag else None
        if not a:
            return ""
        href = a["href"]
        if href.startswith("http"):
            return href
        if href.startswith("/"):
            return base_url + href
        return ""

    def make_date(tag, context_text=""):
        date_el = tag.find(class_=re.compile(r"date|death|born|age", re.I)) if tag else None
        return best_date(
            date_el.get_text(" ", strip=True) if date_el else "",
            context_text
        ) or "See source"

    def extract_family(text):
        """Pull names after survived-by keywords."""
        family = []
        # Find "survived by his/her/their ... wife/husband/son/daughter/children"
        surv = re.search(
            r'survived?\s+by[:\s]+(.{10,300}?)(?:\.|He was|She was|Services|Funeral|Born|\Z)',
            text, re.I | re.S
        )
        if surv:
            chunk = surv.group(1)
            # Extract individual names — look for capitalized pairs
            found = re.findall(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b', chunk)
            for fn in found:
                if is_real_name(fn) and fn not in family:
                    family.append(fn)
        return ", ".join(family[:6])  # cap at 6 names

    def add(n, link, obit_date, card_text=""):
        n = n.strip()
        if n in seen or not is_real_name(n):
            return
        seen.add(n)
        # Location: try to find city, ST in card text
        loc_match = re.search(r'\b([A-Z][a-z]+(?: [A-Z][a-z]+)*),\s*([A-Z]{2})\b', card_text)
        location = loc_match.group(0) if loc_match else f"{county} County, SC"
        # Family members from card text
        family = extract_family(card_text)
        results.append({
            "name": n, "date": obit_date, "location": location,
            "family": family, "source": name, "county": county, "link": link,
        })

    # ── Strategy 1: JSON-LD structured data (most reliable) ──
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            items = data if isinstance(data, list) else [data]
            for item in items:
                if item.get("@type") in ("Person", "Obituary", "Event"):
                    n = item.get("name", "").strip()
                    if is_real_name(n):
                        seen.add(n)
                        results.append({
                            "name": n,
                            "date": item.get("deathDate", item.get("startDate", "See source")),
                            "location": f"{county} County, SC",
                            "family": "",
                            "source": name, "county": county,
                            "link": item.get("url", url),
                        })
        except Exception:
            pass
    if results:
        return results

    # ── Strategy 2: CFS/TA platform ──
    # Name is usually in <strong> or <b> or <h2>/<h3> inside a listing card.
    # Each card also contains the obituary text with dates and family info.
    for card in soup.find_all(class_=re.compile(r"listing|obit|deceased|tribute|memorial|card|item", re.I)):
        card_text = card.get_text(" ", strip=True)
        # Priority: bold tag > named class > heading
        name_el = (
            card.find(["strong", "b"])
            or card.find(class_=re.compile(r"name|title|deceased|fullname", re.I))
            or card.find(["h2", "h3", "h4"])
        )
        if not name_el:
            continue
        n = name_el.get_text(strip=True)
        add(n, make_link(card), make_date(card, card_text), card_text)

    if results:
        return results

    # ── Strategy 3: bold/strong tags anywhere on page ──
    for bold in soup.find_all(["strong", "b"]):
        n = bold.get_text(strip=True)
        if not is_real_name(n):
            continue
        parent = bold.parent
        context = parent.get_text(" ", strip=True) if parent else ""
        if not any(w in context.lower() for w in [
            "born", "passed", "survived", "memorial", "funeral",
            "service", "died", "death", "departed", "eternal", "heaven",
            "age", "years"
        ]):
            continue
        add(n, make_link(bold), make_date(bold, context), context)

    if results:
        return results

    # ── Strategy 4: headings with obituary context nearby ──
    for heading in soup.find_all(["h2", "h3", "h4"]):
        n = heading.get_text(strip=True)
        if not is_real_name(n):
            continue
        parent = heading.parent
        context = parent.get_text(" ", strip=True) if parent else ""
        if not any(w in context.lower() for w in [
            "born", "passed", "survived", "memorial", "funeral",
            "service", "died", "death", "departed", "eternal", "heaven"
        ]):
            continue
        add(n, make_link(heading), make_date(heading, context), context)

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



# ─────────────────────────────────────────────
#  DASHBOARD — disabled, index.html is standalone
# ─────────────────────────────────────────────

def save_dashboard(history):
    """Disabled — docs/index.html is a standalone file managed separately."""
    pass


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
    send_email(results, today)
    print(f"\nDone! {total} obituaries found for {today}.")

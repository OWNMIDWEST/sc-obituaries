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
# "fetch_full": True = listing page has no obit text, must fetch each individual page
# "fetch_full": False = listing page already has full text (default, faster)
FUNERAL_HOMES = {
    "Greenville": [
        {"name": "Watkins Garrett & Woods Mortuary", "url": "https://www.wgwmortuary.com/obituaries"},
        {"name": "Heritage Funeral Home Simpsonville", "url": "https://cannonbyrd.com/obituaries/"},
        {"name": "Cannon-Byrd Funeral Home",          "url": "https://cannonbyrd.com/obituaries/"},
        {"name": "Thomas McAfee Funeral Homes",      "url": "https://www.thomasmcafee.com/obituaries/obituary-listings", "fetch_full": True},
        {"name": "Mackey Mortuary",                  "url": "https://www.mackeymortuary.com/obituaries", "fetch_full": True},
        {"name": "Robinson Funeral Homes",           "url": "https://www.robinsonfuneralhomes.com/obituaries"},
        {"name": "Cremation Society of SC",          "url": "https://www.cremationsocietyofsc.com/obituaries"},
        {"name": "Dignity Memorial Greenville",      "url": "https://www.dignitymemorial.com/obituaries/greenville-sc"},
        {"name": "Beasley Funeral Home",             "url": "https://www.beasleyfuneralhome.com/obituaries", "fetch_full": True},
        {"name": "Hatcher Funeral Home",             "url": "https://www.hatcherfuneralhome.com/obituaries", "fetch_full": True},
        {"name": "Legacy.com Greenville County",     "url": "https://www.legacy.com/us/obituaries/local/south-carolina/greenville-county"},
        {"name": "Legacy.com Greenville Area",       "url": "https://www.legacy.com/us/obituaries/local/south-carolina/greenville-area"},
    ],
    "Spartanburg": [
        {"name": "Floyd Mortuary",                   "url": "https://www.floydmortuary.com/listings"},
        {"name": "Bobo Funeral Chapel",              "url": "https://www.bobofuneralchapel.com/listings",     "fetch_full": True},
        {"name": "Roberts Funeral Home",             "url": "https://www.robertsfhsc.com/listings",           "fetch_full": True},
        {"name": "E.L. Collins Funeral Home",        "url": "https://www.elcollinsfh.com/spantanburg-sc-obituaries"},
        {"name": "Community Mortuary",               "url": "https://www.communitymortuaryinc.com/listings",  "fetch_full": True},
        {"name": "Seawright Funeral Home",           "url": "https://seawright-funeralhome.com/obituaries"},
        {"name": "J.W. Woodward Funeral Home",       "url": "https://www.articobits.com/obituaries/jw-woodward-fh/"},
        {"name": "White Columns Funeral Service",    "url": "https://www.whitecolumnsfuneralservice.com/listings", "fetch_full": True},
        {"name": "Legacy.com Spartanburg County",    "url": "https://www.legacy.com/us/obituaries/local/south-carolina/spartanburg-county"},
        {"name": "Legacy.com Spartanburg Area",      "url": "https://www.legacy.com/us/obituaries/local/south-carolina/spartanburg-area"},
        {"name": "Legacy.com Herald-Journal",        "url": "https://www.legacy.com/us/obituaries/spartanburg/today"},
    ],
    "Anderson": [
        {"name": "Sosebee Mortuary",                 "url": "https://sosebeemortuary.com/obituaries"},
        {"name": "Anderson Simple Cremations",       "url": "https://andersonsimplecremations.com/obituary/"},
        {"name": "D.B. Walker Funeral Services",     "url": "https://www.dbwalkerfuneralservices.com/listings", "fetch_full": True},
        {"name": "Marcus D. Brown Funeral Home",     "url": "https://www.marcusdbrownfuneralhome.com/listings", "fetch_full": True},
        {"name": "Johnson Funeral Home",             "url": "https://www.johnsonfuneralhm.com/listings",       "fetch_full": True},
        {"name": "Rich-Colonial Funeral Home",       "url": "https://www.rich-colonial-funeral-home.com/listings", "fetch_full": True},
        {"name": "McDougald Funeral Home",           "url": "https://www.mcdougaldfuneralhome.com/obituaries/obituary-listings", "fetch_full": True},
        {"name": "Sullivan-King Mortuary",           "url": "https://www.sullivanking.com/obits",              "fetch_full": True},
        {"name": "Legacy.com Anderson County",       "url": "https://www.legacy.com/us/obituaries/local/south-carolina/anderson-county"},
        {"name": "Legacy.com Anderson Area",         "url": "https://www.legacy.com/us/obituaries/local/south-carolina/anderson"},
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
    # "passed/died/departed on April 17th, 2026" — with or without ordinal suffix
    re.compile(
        r'(?:passed(?:\s+away)?|died|departed|entered[^.]*rest|called[^.]*home|transitioned|born)'
        r'\s+(?:on\s+)?'
        r'((?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|'
        r'Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)'
        r'\s+\d{1,2}(?:st|nd|rd|th)?,?\s+20\d\d)', re.I),
    # Standalone "April 17, 2026" or "April 17th, 2026"
    re.compile(
        r'\b((?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|'
        r'Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)'
        r'\s+\d{1,2}(?:st|nd|rd|th)?,?\s+20\d\d)\b', re.I),
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
    Very strict — requires pattern of a real human name.
    """
    if not text:
        return False
    t = text.strip()

    # Length bounds — names are 5-60 chars
    if len(t) < 5 or len(t) > 60:
        return False

    # No digits — dates, phone numbers, addresses
    if any(c.isdigit() for c in t):
        return False

    # Must have 2-5 words only
    words = t.split()
    if len(words) < 2 or len(words) > 6:
        return False

    # Must be mostly alphabetic + name punctuation
    letter_ratio = sum(c.isalpha() or c in " .,'-\"" for c in t) / len(t)
    if letter_ratio < 0.90:
        return False

    # Reject if it looks like "City, ST"
    if re.match(r'^[A-Z][a-z]+(?: [A-Z][a-z]+)*, [A-Z]{2}$', t):
        return False

    # Reject ALL CAPS strings (headings/buttons)
    if t.upper() == t:
        return False

    # Known title prefixes — these are OK as first word
    titles = {"mr","mrs","ms","miss","dr","rev","sr","jr","prof","col","sgt","cpl",
              "pvt","lt","capt","maj","gen","brig","cdr","hon","atty","det"}

    # Known name particles — OK anywhere
    particles = {"de","van","von","la","le","of","the","and","st","mc","mac",
                 "jr","sr","ii","iii","iv","v"}

    # Hard reject words — if ANY word matches, it's not a name
    # These are verbs, nouns, adjectives common in UI/web pages
    reject_single = {
        "view","request","send","add","sign","leave","read","learn","click",
        "load","share","plant","light","all","recent","our","your","my",
        "frequently","asked","pricing","gallery","appointment","flowers",
        "candle","guestbook","condolence","obituary","obituaries","listing",
        "services","service","arrangements","visitation","graveside","chapel",
        "memorial","mortuary","cremation","funeral","home","store","shop",
        "about","contact","directions","staff","careers","resources","support",
        "privacy","policy","terms","accessibility","copyright","sitemap",
        "preplanning","planning","grief","questions","faq","information",
        "phone","fax","email","address","location","hours","map","news",
        "facebook","twitter","instagram","youtube","linkedin","social",
        "monday","tuesday","wednesday","thursday","friday","saturday","sunday",
        "january","february","march","april","may","june","july","august",
        "september","october","november","december","urn","casket","vault",
        "headstone","monument","flower","tribute","legacy","featured",
        "affordable","professional","compassionate","dedicated","trusted",
        "welcome","thank","please","available","complete","full","new","free",
        "schedule","purchase","order","payment","plan","pre","post",
        "county","state","city","town","village","markers","memorials",
        "immediate","need","overview","next","previous","navigation","pagination",
        "south","north","east","west","central","upper","lower",
        # More UI/nav terms seen in real scrape output
        "cookie","preferences","general","tour","photographic","cremations","simple","complex","consultation","lists","video","virtual","interactive","digital","online","live",
        "virtual","search","filter","sort","featured","special","online",
        "donation","donate","record","register","login","account","password",
        "brochure","download","print","share","copy","save","export",
        "directions","parking","accessibility","feedback","review","rating",
        "package","option","selection","choice","item","product","category",
    }

    for w in words:
        clean = w.strip(".,'\"-").rstrip(".").lower()
        if not clean:
            continue
        # First word can be a title
        if w == words[0] and clean in titles:
            continue
        # Particles allowed anywhere
        if clean in particles:
            continue
        # Any word matching reject list = not a name
        if clean in reject_single:
            return False
        # Every non-particle word must start with capital
        original = w.strip(".,'\"-")
        if original and not original[0].isupper():
            return False
        # Must have a vowel (real name syllables)
        if len(clean) > 2 and not any(v in clean for v in "aeiou"):
            return False

    # Final check: must have at least one word that looks like a proper surname
    # (more than 2 chars, starts with capital, not a particle or title)
    has_surname = False
    for w in words[1:]:  # skip first word (could be title)
        clean = w.strip(".,'\"-").rstrip(".")
        if len(clean) >= 2 and clean[0].isupper() and clean.lower() not in particles and clean.lower() not in titles:
            has_surname = True
            break
    if not has_surname:
        return False

    return True


def fetch_funeral_home(name, url, county, fetch_full=False):
    """Scrape a funeral home obituary page with strict name validation.
    fetch_full=True: always fetch each individual obit page (CFS/TA sites with no listing text)
    fetch_full=False: only fetch individual pages if listing text is missing survived-by info
    """
    results = []
    _fetch_full_flag = fetch_full  # store for use in add()
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

    def extract_family(text, deceased_name=""):
        """Pull family member names after survived-by keywords, excluding the deceased."""
        family = []
        if not text:
            return ""

        # Build a set of name parts to exclude — the deceased's own first, last, middle names
        exclude = set()
        if deceased_name:
            for part in deceased_name.replace(",", " ").split():
                w = part.strip(".\'\"-").lower()
                if len(w) > 1:
                    exclude.add(w)

        # Sentence-boundary terminator — stops at real new sentences, NOT at Jr./Sr./Dr. abbreviations
        # Covers common obituary closing phrases that signal the end of the survivors section
        _end = (
            r'(?=\.\s*(?:' +
            r'Funeral|Services|Visitation|Born|In lieu|Memorial|Burial|Graveside|' +
            r'Interment|Cremation|Celebration of|She was born|He was born|' +
            r'She will be|He will be|She is remembered|He is remembered|' +
            r'She lived|He lived|Please|Friends may|Arrangements|Condolences|' +
            r'Pallbearers|Honorary|A celebration|The family|' +
            r'Preceding her|Preceding him|Preceded in' +
            r')|\ Z)'
        )

        # All known "survived by" phrase patterns — including regional/cultural variants
        patterns = [
            r'(?:is|are)\s+survived\s+by[:\s]+(.+?)' + _end,
            r'also\s+survived\s+by[:\s]+(.+?)' + _end,
            r'survivors?\s+include[:\s]+(.+?)' + _end,
            r'leaves?\s+to\s+cherish(?:\s+(?:his|her|their))?\s+(?:memory|memories|life)[:\s]+(.+?)' + _end,
            r'left\s+to\s+cherish(?:\s+(?:his|her|their))?\s+(?:memory|memories)[:\s]+(.+?)' + _end,
            r'leaves?\s+to\s+cherish[:\s]+(.+?)' + _end,
            r'leaves?\s+behind[:\s]+(.+?)' + _end,
            r'surviving\s+are[:\s]+(.+?)' + _end,
            r'those\s+left\s+to\s+cherish[:\s]+(.+?)' + _end,
            r'left\s+to\s+mourn[:\s]+(.+?)' + _end,
            r'left\s+to\s+celebrate[:\s]+(.+?)' + _end,
            r'cherishing(?:\s+(?:his|her|their))?\s+memory[:\s]+(.+?)' + _end,
        ]

        chunks = []
        seen_chunks = set()
        for pat in patterns:
            # Use findall to catch MULTIPLE survived-by sections in long obituaries
            for m in re.finditer(pat, text, re.I | re.S):
                chunk = m.group(1).strip()
                if chunk and chunk not in seen_chunks:
                    seen_chunks.add(chunk)
                    chunks.append(chunk)

        # Use full text if short enough and no pattern matched (listing previews)
        if not chunks and len(text) < 800:
            chunks.append(text)

        for chunk in chunks:
            # Strip "Previous [Name]" and "Next [Name]" pagination labels
            chunk = re.sub(r'\b(?:Previous|Next)\b', ' ', chunk, flags=re.I)
            found = re.findall(r'\b([A-Z][a-z]+(?:\s+(?:[A-Z][a-z]+|[A-Z]\.)){1,3})\b', chunk)
            for fn in found:
                fn = fn.strip()
                if not is_real_name(fn):
                    continue
                if fn in family:
                    continue

                # Reject institutions, places, and organizations — not people
                fn_lower = fn.lower()
                institution_words = {
                    # Church/religious
                    "church", "chapel", "cathedral", "ministry", "ministries", "missionary",
                    "baptist", "methodist", "presbyterian", "catholic", "lutheran", "episcopal",
                    "pentecostal", "apostolic", "assembly", "assemblies", "fellowship",
                    "temple", "synagogue", "mosque", "parish",
                    # Schools/education
                    "school", "academy", "college", "university", "institute", "district",
                    "elementary", "middle", "high", "class",
                    # Businesses/organizations
                    "manor", "lodge", "gardens", "estates", "plantation", "farms",
                    "hospital", "medical", "clinic", "center", "centre",
                    "chamber", "association", "society", "club", "foundation",
                    "corporation", "company", "industries", "services", "group",
                    "funeral", "mortuary", "cremation", "cemetery",
                    # Facilities/places without obvious institution words
                    "terrace", "inn", "commons", "place", "court", "crossing",
                    "pointe", "point", "landing", "ridge", "view", "vista",
                    # Common SC city names that appear in obituaries
                    "inman", "cowpens", "pacolet", "woodruff", "gaffney",
                    "landrum", "chesnee", "boiling", "wellford", "lyman",
                    "campobello", "pauline", "roebuck", "duncan",
                    "simpsonville", "mauldin", "easley", "pickens", "greer",
                    # Navigation/pagination text from listing pages
                    "previous", "next", "pagination",
                    # Religious phrases (not names)
                    "christ", "jesus", "savior", "heaven", "lord", "almighty",
                    "blessed", "holy", "eternal", "grace", "amazing",
                    # Street/address indicators
                    "blvd", "boulevard", "highway", "hwy", "ave", "rd", "drive", "hospice",
                    # Military/organizational
                    "department", "corps", "regiment", "division", "battalion",
                    "fire", "police", "army", "navy", "airforce",
                    # Common junk from Woodward site
                    "howard", "stinson", "woodward", "daniel", "morgan",
                }
                fn_words = set(w.strip(".,'\"-").lower() for w in fn.split())
                if fn_words & institution_words:
                    continue
                # Exclude only if the candidate IS the deceased — not just shares a last name.
                # Allow family members who share only the last name (spouse, children, grandchildren).
                fn_parts = set(w.strip(".\'\"-").lower() for w in fn.split() if len(w) > 2)
                suffix_words = {"jr", "sr", "ii", "iii", "iv", "v"}
                fn_all_words = set(w.strip(".\'\"-").lower() for w in fn.split())
                has_suffix = bool(fn_all_words & suffix_words)
                shared = fn_parts & exclude
                first_word = fn.split()[0].strip(".\'\"-").lower()
                deceased_first = deceased_name.split()[0].strip(".\'\"-").lower() if deceased_name else ""
                if shared and not has_suffix:
                    if len(shared) >= 2:  # shares 2+ name parts = probably the deceased
                        continue
                    if first_word == deceased_first and len(first_word) > 2:  # same first name
                        continue
                family.append(fn)

        return ", ".join(family[:15])

    def fetch_full_text(link):
        """Fetch the full obituary page and return its complete text."""
        if not link or not link.startswith("http"):
            return ""
        # Skip links that are just the listing page itself
        if link.rstrip("/") == url.rstrip("/"):
            return ""
        try:
            time.sleep(0.2)  # Be polite — small delay between individual page fetches
            r = requests.get(link, headers=HEADERS, timeout=15)
            if r.status_code == 200:
                s = BeautifulSoup(r.text, "html.parser")
                # Remove nav/header/footer/sidebar noise — keep only main content
                for tag in s.find_all(["nav", "header", "footer", "script", "style",
                                       "aside", "iframe", "noscript"]):
                    tag.decompose()
                # Also strip common sidebar class names
                for tag in s.find_all(class_=re.compile(
                    r"sidebar|widget|menu|footer|header|nav|social|share|subscribe|"
                    r"related|comment|advertisement|banner|popup", re.I)):
                    tag.decompose()
                return s.get_text(" ", strip=True)
        except Exception:
            pass
        return ""

    def add(n, link, obit_date, card_text=""):
        n = n.strip()
        if n in seen or not is_real_name(n):
            return
        seen.add(n)

        full_text = card_text
        _surv_pat = re.compile(r'surviv|cherish|mourn|leaves?\s+to|left\s+to', re.I)

        # Fetch individual obit page if:
        # - fetch_full flag set (site has no text on listing page), OR
        # - card text is short, OR
        # - no survived-by language found in card text
        needs_full = (
            _fetch_full_flag or
            len(card_text) < 400 or
            not _surv_pat.search(card_text)
        )

        if link and needs_full:
            fetched = fetch_full_text(link)
            if fetched and len(fetched) > len(card_text):
                full_text = fetched

        # Location: try to find city, ST in text
        loc_match = re.search(r'\b([A-Z][a-z]+(?: [A-Z][a-z]+)*),\s*([A-Z]{2})\b', full_text)
        location = loc_match.group(0) if loc_match else f"{county} County, SC"

        # Family members — pass the deceased's name so it gets excluded
        family = extract_family(full_text, deceased_name=n)

        # If still no family from full text, try card text as fallback
        if not family and full_text != card_text:
            family = extract_family(card_text, deceased_name=n)

        results.append({
            "name": n, "date": obit_date if obit_date != "See source" else best_date(full_text),
            "location": location, "family": family,
            "source": name, "county": county, "link": link,
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

    # ── Strategy 2b: CFS/TA platform link-based layout ──
    # Sites like Bobo, Roberts, Community Mortuary use this pattern:
    # <a href="/obituary/First-Last">First Last</a>
    # The listing page has NO full text — must always fetch the individual obit page
    for a in soup.find_all("a", href=re.compile(r"/obituary/", re.I)):
        n = a.get_text(strip=True)
        if not is_real_name(n):
            continue
        href = a.get("href", "")
        link = (base_url + href) if href.startswith("/") else href
        # Always fetch full page for these sites — listing page has no text
        full_text = fetch_full_text(link) if link else ""
        card_text = full_text or ""
        add(n, link, make_date(a, card_text), card_text)

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
            fh_results = fetch_funeral_home(
                fh["name"], fh["url"], county,
                fetch_full=fh.get("fetch_full", False)
            )
            added = 0
            for r in fh_results:
                if r["name"] not in seen_names:
                    seen_names.add(r["name"])
                    county_results.append(r)
                    added += 1
            print(f"  -> {added} new")
            time.sleep(0.5)

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

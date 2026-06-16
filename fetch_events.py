#!/usr/bin/env python3
"""
Global Events Tracker - News Fetcher & Geocoder
Agent 1 + Agent 2 Production Script

Data Sources:
  1. GDELT Project API  (free, no key required)
  2. RSS Feeds          (BBC World, Reuters, AP News via feedparser)
  3. NewsAPI.org        (optional, 100 req/day free - set NEWSAPI_KEY below)

Geocoding:
  Nominatim (OpenStreetMap) - free, no key required, 1 req/sec rate limit

Output: events_data.json  (loaded by index.html)

Usage:
  pip install requests feedparser geopy
  python fetch_events.py
"""

import json, time, hashlib, re
from datetime import datetime, timezone
from typing import Optional

import requests
import feedparser
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderServiceError

# ── Config ──────────────────────────────────────────────────────────────────
NEWSAPI_KEY = ""   # Optional: https://newsapi.org (free 100 req/day)
OUTPUT_FILE = "events_data.json"

RSS_FEEDS = {
    "BBC World":  "http://feeds.bbci.co.uk/news/world/rss.xml",
    "Reuters":    "https://feeds.reuters.com/reuters/worldNews",
    "AP News":    "https://rsshub.app/apnews/topics/apf-intlnews",
}

KEYWORDS = [
    "war","attack","explosion","earthquake","hurricane","flood","tsunami",
    "missile","nuclear","sanctions","election","coup","protest","crisis",
    "climate","fire","disaster","outbreak","pandemic","breakthrough",
    "AI","artificial intelligence","ceasefire","invasion","strike","conflict",
]

CATEGORY_MAP = {
    "military|war|attack|missile|nuclear|ceasefire|invasion|troops|airstrike": ("Military",   "#ff2222"),
    "earthquake|tsunami|flood|hurricane|typhoon|fire|volcano|disaster|storm":  ("Disaster",    "#ff8800"),
    "AI|artificial intelligence|tech|robot|quantum|space|launch|satellite":    ("Technology",  "#00aaff"),
    "climate|deforestation|carbon|wildfire|extinction|pollution|emissions":    ("Environment", "#22cc44"),
    "election|protest|sanction|economy|trade|inflation|GDP|bank|crisis|coup":  ("Politics",    "#aa44ff"),
}

SEVERITY_MAP = {
    "critical": ["nuclear","invasion","pandemic","tsunami","world war"],
    "high":     ["attack","strike","earthquake","hurricane","flood","crisis","missile","airstrike"],
    "medium":   ["protest","sanction","disaster","fire","explosion","ceasefire"],
}

# ── Geocoder (Nominatim) ─────────────────────────────────────────────────────
geolocator = Nominatim(user_agent="global-events-tracker/1.0")
_geo_cache: dict = {}

def geocode(location: str) -> Optional[tuple[float, float]]:
    if not location or location in ("Unknown", "N/A", ""):
        return None
    if location in _geo_cache:
        return _geo_cache[location]
    time.sleep(1.1)  # Nominatim rate: 1 req/sec
    for attempt in range(3):
        try:
            loc = geolocator.geocode(location, timeout=10)
            if loc:
                result = (round(loc.latitude, 4), round(loc.longitude, 4))
                _geo_cache[location] = result
                return result
        except (GeocoderTimedOut, GeocoderServiceError):
            time.sleep(2 ** attempt)
    return None


# ── Classifiers ───────────────────────────────────────────────────────────────
def classify(text: str) -> tuple[str, str]:
    t = text.lower()
    for pattern, (label, color) in CATEGORY_MAP.items():
        if any(k in t for k in pattern.split("|")):
            return label, color
    return ("General", "#6688aa")

def get_severity(text: str) -> str:
    t = text.lower()
    for level, keywords in SEVERITY_MAP.items():
        if any(k in t for k in keywords):
            return level
    return "low"

def uid(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()[:8]


# ── Country NER (regex) ───────────────────────────────────────────────────────
_COUNTRIES = (
    "Afghanistan|Albania|Algeria|Angola|Argentina|Australia|Austria|Azerbaijan|"
    "Bangladesh|Belarus|Belgium|Bolivia|Brazil|Cambodia|Cameroon|Canada|Chile|"
    "China|Colombia|Congo|Croatia|Cuba|Czech|Denmark|Ecuador|Egypt|Ethiopia|"
    "Finland|France|Gaza|Georgia|Germany|Ghana|Greece|Guatemala|Haiti|Honduras|"
    "Hungary|India|Indonesia|Iran|Iraq|Ireland|Israel|Italy|Japan|Jordan|"
    "Kazakhstan|Kenya|Kosovo|Lebanon|Libya|Malaysia|Mali|Mexico|Moldova|Morocco|"
    "Myanmar|Nepal|Netherlands|Nicaragua|Niger|Nigeria|Norway|Pakistan|Palestine|"
    "Panama|Peru|Philippines|Poland|Portugal|Romania|Russia|Saudi Arabia|Serbia|"
    "Somalia|South Korea|Spain|Sri Lanka|Sudan|Sweden|Syria|Taiwan|Thailand|"
    "Tunisia|Turkey|Ukraine|United Kingdom|United States|Venezuela|Vietnam|Yemen|Zimbabwe"
)
_COUNTRY_RE = re.compile(rf'\b({_COUNTRIES})\b', re.IGNORECASE)

def extract_location(text: str) -> Optional[str]:
    m = _COUNTRY_RE.search(text)
    return m.group(0).title() if m else None


# ── GDELT Fetcher ─────────────────────────────────────────────────────────────
def fetch_gdelt(max_events: int = 20) -> list[dict]:
    """GDELT GKG Doc API - completely free, no key."""
    url = (
        "https://api.gdeltproject.org/api/v2/doc/doc"
        "?query=conflict+OR+disaster+OR+military+OR+technology"
        "&mode=artlist&format=json&maxrecords=25&sort=ToneDesc"
    )
    events = []
    try:
        r = requests.get(url, timeout=20)
        for item in (r.json().get("articles") or [])[:max_events]:
            title    = item.get("title", "")
            location = item.get("sourcecountry", "")
            coords   = geocode(location)
            if not coords:
                continue
            cat, color = classify(title)
            events.append({
                "id":        uid(title),
                "title":     title,
                "summary":   title,
                "category":  cat,
                "severity":  get_severity(title),
                "source":    item.get("domain", "GDELT"),
                "url":       item.get("url", "#"),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "location":  location,
                "lat":       coords[0],
                "lon":       coords[1],
                "color":     color,
            })
    except Exception as e:
        print(f"  [GDELT] {e}")
    return events


# ── RSS Fetcher ───────────────────────────────────────────────────────────────
def fetch_rss(max_per_feed: int = 15) -> list[dict]:
    events = []
    for source, url in RSS_FEEDS.items():
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:max_per_feed]:
                title   = entry.get("title", "")
                summary = (entry.get("summary") or title)[:400].strip()
                combined = title + " " + summary
                if not any(k.lower() in combined.lower() for k in KEYWORDS):
                    continue
                location = extract_location(combined)
                coords   = geocode(location) if location else None
                if not coords:
                    continue
                cat, color = classify(combined)
                pub = entry.get("published_parsed")
                ts  = (datetime(*pub[:6], tzinfo=timezone.utc).isoformat()
                       if pub else datetime.now(timezone.utc).isoformat())
                events.append({
                    "id":        uid(title),
                    "title":     title,
                    "summary":   summary,
                    "category":  cat,
                    "severity":  get_severity(combined),
                    "source":    source,
                    "url":       entry.get("link", "#"),
                    "timestamp": ts,
                    "location":  location or "Unknown",
                    "lat":       coords[0],
                    "lon":       coords[1],
                    "color":     color,
                })
        except Exception as e:
            print(f"  [RSS:{source}] {e}")
    return events


# ── NewsAPI Fetcher (optional) ────────────────────────────────────────────────
def fetch_newsapi(max_events: int = 25) -> list[dict]:
    if not NEWSAPI_KEY:
        return []
    events = []
    try:
        r = requests.get(
            "https://newsapi.org/v2/top-headlines",
            params={"category": "general", "language": "en",
                    "pageSize": max_events, "apiKey": NEWSAPI_KEY},
            timeout=10
        )
        for article in r.json().get("articles", []):
            title   = article.get("title", "")
            summary = article.get("description", title) or title
            location = extract_location(title + " " + summary)
            coords   = geocode(location) if location else None
            if not coords:
                continue
            cat, color = classify(title)
            events.append({
                "id":        uid(title),
                "title":     title,
                "summary":   summary[:400],
                "category":  cat,
                "severity":  get_severity(title),
                "source":    article.get("source", {}).get("name", "NewsAPI"),
                "url":       article.get("url", "#"),
                "timestamp": article.get("publishedAt", datetime.now(timezone.utc).isoformat()),
                "location":  location or "Unknown",
                "lat":       coords[0],
                "lon":       coords[1],
                "color":     color,
            })
    except Exception as e:
        print(f"  [NewsAPI] {e}")
    return events


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print("Global Events Tracker - Fetching news...\n")
    all_events: list[dict] = []

    print("[1/3] GDELT Project API (free, no key)...")
    gdelt = fetch_gdelt(20)
    all_events.extend(gdelt)
    print(f"      -> {len(gdelt)} geocoded events\n")

    print("[2/3] RSS Feeds (BBC World / Reuters / AP News)...")
    rss = fetch_rss(15)
    all_events.extend(rss)
    print(f"      -> {len(rss)} geocoded events\n")

    if NEWSAPI_KEY:
        print("[3/3] NewsAPI.org...")
        newsapi = fetch_newsapi(25)
        all_events.extend(newsapi)
        print(f"      -> {len(newsapi)} geocoded events\n")
    else:
        print("[3/3] NewsAPI.org - skipped (set NEWSAPI_KEY in script for +25 events)\n")

    # Deduplicate by id
    seen: set = set()
    unique = [e for e in all_events if not (e["id"] in seen or seen.add(e["id"]))]

    # Sort: critical first, then by timestamp desc
    sev_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    unique.sort(key=lambda x: (sev_order.get(x["severity"], 4), x["timestamp"]), reverse=False)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(unique, f, ensure_ascii=False, indent=2)

    print(f"Saved {len(unique)} events -> {OUTPUT_FILE}")
    print("Open index.html (or serve with: python -m http.server 8080)")


if __name__ == "__main__":
    main()

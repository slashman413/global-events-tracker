#!/usr/bin/env python3
"""
Global Events Tracker - Continent-based News Fetcher
Fetches top 10 news per continent (6 continents = up to 60 events)
Runs daily via GitHub Actions; outputs events_data.json for the 3D globe.

Data Sources (all free, no API key):
  BBC World Regional RSS  — Africa / Asia / Europe / Americas / Middle East
  ABC Australia RSS       — Oceania
  Geocoding: Nominatim (OpenStreetMap), 1 req/sec

Usage:
  pip install requests feedparser geopy
  python fetch_events.py
"""

import json, time, hashlib, re, os
from datetime import datetime, timezone
from typing import Optional
import requests
import feedparser
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderServiceError

# ── Continent Config ──────────────────────────────────────────────────────────
CONTINENTS = {
    "Africa": {
        "color":          "#f59e0b",
        "default_coords": (8.78, 34.51),
        "feeds": [
            ("BBC Africa",   "http://feeds.bbci.co.uk/news/world/africa/rss.xml"),
            ("Al Jazeera",   "https://www.aljazeera.com/xml/rss/all.xml"),
        ],
        "keywords": [
            "africa","kenya","nigeria","ethiopia","egypt","ghana","tanzania","uganda",
            "cameroon","senegal","mali","niger","chad","somalia","sudan","zimbabwe",
            "mozambique","angola","zambia","rwanda","congo","libya","tunisia","algeria",
            "morocco","south africa","madagascar","ivory coast","burkina",
        ],
    },
    "Asia": {
        "color":          "#ef4444",
        "default_coords": (34.05, 100.62),
        "feeds": [
            ("BBC Asia",     "http://feeds.bbci.co.uk/news/world/asia/rss.xml"),
            ("Reuters Asia", "https://feeds.reuters.com/reuters/asiaNews"),
        ],
        "keywords": [
            "china","japan","india","korea","indonesia","thailand","philippines",
            "vietnam","malaysia","myanmar","pakistan","bangladesh","sri lanka",
            "cambodia","taiwan","hong kong","singapore","nepal","bhutan",
            "kazakhstan","uzbekistan","mongolia","laos","tibet","xinjiang",
        ],
    },
    "Europe": {
        "color":          "#3b82f6",
        "default_coords": (54.53, 15.26),
        "feeds": [
            ("BBC Europe",   "http://feeds.bbci.co.uk/news/world/europe/rss.xml"),
            ("Reuters EU",   "https://feeds.reuters.com/reuters/europeanews"),
        ],
        "keywords": [
            "europe","france","germany","uk","britain","russia","italy","spain",
            "ukraine","poland","netherlands","belgium","sweden","norway","denmark",
            "finland","austria","switzerland","greece","turkey","hungary","romania",
            "czechia","slovakia","serbia","croatia","bulgaria","moldova","georgia",
            "nato","eu","european union","brexit","schengen","eurozone",
        ],
    },
    "Americas": {
        "color":          "#10b981",
        "default_coords": (18.49, -77.26),
        "feeds": [
            ("BBC US",       "http://feeds.bbci.co.uk/news/world/us_and_canada/rss.xml"),
            ("BBC LatAm",    "http://feeds.bbci.co.uk/news/world/latin_america/rss.xml"),
            ("Reuters US",   "https://feeds.reuters.com/reuters/domesticNews"),
        ],
        "keywords": [
            "united states","usa","america","canada","brazil","mexico","argentina",
            "colombia","chile","peru","venezuela","cuba","haiti","honduras","guatemala",
            "nicaragua","ecuador","bolivia","uruguay","paraguay","caribbean",
            "washington","new york","california","texas","congress","senate",
        ],
    },
    "Middle East": {
        "color":          "#8b5cf6",
        "default_coords": (29.31, 42.46),
        "feeds": [
            ("BBC MidEast",  "http://feeds.bbci.co.uk/news/world/middle_east/rss.xml"),
            ("Al Jazeera",   "https://www.aljazeera.com/xml/rss/all.xml"),
        ],
        "keywords": [
            "israel","iran","iraq","syria","saudi arabia","lebanon","jordan","turkey",
            "yemen","qatar","kuwait","bahrain","oman","uae","dubai","abu dhabi",
            "gaza","west bank","palestine","hamas","hezbollah","houthi",
            "persian gulf","red sea","suez","middle east",
        ],
    },
    "Oceania": {
        "color":          "#06b6d4",
        "default_coords": (-25.27, 133.78),
        "feeds": [
            ("ABC Australia","https://www.abc.net.au/news/feed/2942460/rss.xml"),
            ("RNZ NZ",       "https://www.rnz.co.nz/rss/world.xml"),
        ],
        "keywords": [
            "australia","new zealand","papua new guinea","fiji","solomon islands",
            "vanuatu","samoa","tonga","pacific","sydney","melbourne","auckland",
            "canberra","wellington","queensland","victoria","wildfire","reef",
        ],
    },
}

# ── Event Categories ──────────────────────────────────────────────────────────
CATEGORY_MAP = {
    "military|war|attack|missile|nuclear|ceasefire|invasion|troops|airstrike|bomb|shoot|kill|soldier": ("Military",   "💥"),
    "earthquake|tsunami|flood|hurricane|typhoon|fire|volcano|disaster|storm|cyclone|drought|quake":    ("Disaster",   "🌋"),
    "AI|artificial intelligence|tech|robot|quantum|space|satellite|breakthrough|science|chip|launch":  ("Technology", "🤖"),
    "climate|deforestation|wildfire|extinction|pollution|emissions|carbon|reef|species|environment":   ("Environment","🌿"),
    "election|president|prime minister|government|sanction|economy|trade|GDP|bank|inflation|budget":   ("Politics",   "🏛"),
}

SEVERITY_MAP = {
    "critical": ["nuclear","invasion","pandemic","tsunami","world war","catastrophe","genocide"],
    "high":     ["attack","strike","earthquake","hurricane","flood","crisis","missile","airstrike","killed","dead"],
    "medium":   ["protest","sanction","disaster","fire","explosion","ceasefire","arrested","injured"],
}

# ── Country → Geocode Cache ───────────────────────────────────────────────────
_GEO_CACHE: dict = {}
_GEO_CACHE_FILE = "geo_cache.json"

def load_cache():
    if os.path.exists(_GEO_CACHE_FILE):
        try:
            with open(_GEO_CACHE_FILE, encoding="utf-8") as f:
                _GEO_CACHE.update(json.load(f))
        except Exception:
            pass

def save_cache():
    try:
        with open(_GEO_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(_GEO_CACHE, f, ensure_ascii=False)
    except Exception:
        pass

geolocator = Nominatim(user_agent="global-events-tracker/2.0")

def geocode(location: str) -> Optional[tuple[float, float]]:
    if not location:
        return None
    key = location.strip().lower()
    if key in _GEO_CACHE:
        cached = _GEO_CACHE[key]
        return tuple(cached) if cached else None
    time.sleep(1.1)
    for attempt in range(3):
        try:
            loc = geolocator.geocode(location, timeout=10)
            if loc:
                result = (round(loc.latitude, 4), round(loc.longitude, 4))
                _GEO_CACHE[key] = list(result)
                return result
        except (GeocoderTimedOut, GeocoderServiceError):
            time.sleep(2 ** attempt)
    _GEO_CACHE[key] = None
    return None

# ── NLP helpers ───────────────────────────────────────────────────────────────
_COUNTRY_RE = re.compile(
    r'\b(Afghanistan|Albania|Algeria|Angola|Argentina|Australia|Austria|Azerbaijan|'
    r'Bahrain|Bangladesh|Belarus|Belgium|Bolivia|Bosnia|Brazil|Bulgaria|Burkina|'
    r'Cambodia|Cameroon|Canada|Chad|Chile|China|Colombia|Congo|Croatia|Cuba|Cyprus|'
    r'Czechia|Denmark|Dominican|Ecuador|Egypt|El Salvador|Ethiopia|Finland|France|'
    r'Gaza|Georgia|Germany|Ghana|Greece|Guatemala|Guinea|Haiti|Honduras|Hungary|'
    r'India|Indonesia|Iran|Iraq|Ireland|Israel|Italy|Ivory Coast|Jamaica|Japan|'
    r'Jordan|Kazakhstan|Kenya|Kosovo|Kuwait|Kyrgyzstan|Laos|Latvia|Lebanon|Libya|'
    r'Lithuania|Madagascar|Malawi|Malaysia|Mali|Malta|Mexico|Moldova|Mongolia|'
    r'Morocco|Mozambique|Myanmar|Namibia|Nepal|Netherlands|Nicaragua|Niger|Nigeria|'
    r'North Korea|Norway|Oman|Pakistan|Palestine|Panama|Papua|Paraguay|Peru|'
    r'Philippines|Poland|Portugal|Qatar|Romania|Russia|Rwanda|Saudi Arabia|Senegal|'
    r'Serbia|Sierra Leone|Singapore|Slovakia|Somalia|South Africa|South Korea|Spain|'
    r'Sri Lanka|Sudan|Sweden|Switzerland|Syria|Taiwan|Tajikistan|Tanzania|Thailand|'
    r'Tunisia|Turkey|Turkmenistan|Uganda|Ukraine|United Arab Emirates|UAE|'
    r'United Kingdom|UK|United States|USA|Uruguay|Uzbekistan|Venezuela|Vietnam|'
    r'West Bank|Yemen|Zambia|Zimbabwe)\b',
    re.IGNORECASE
)

_CITY_RE = re.compile(
    r'\b(Kabul|Algiers|Luanda|Buenos Aires|Sydney|Vienna|Baku|Dhaka|Brussels|'
    r'Minsk|La Paz|Brasilia|Phnom Penh|Yaounde|Ottawa|Toronto|Vancouver|Santiago|'
    r'Beijing|Shanghai|Hong Kong|Bogota|Kinshasa|Zagreb|Havana|Nicosia|Prague|'
    r'Copenhagen|Quito|Cairo|Addis Ababa|Helsinki|Paris|Lyon|Berlin|Hamburg|Munich|'
    r'Accra|Athens|Guatemala City|Port au Prince|Budapest|New Delhi|Mumbai|Kolkata|'
    r'Jakarta|Baghdad|Dublin|Jerusalem|Tel Aviv|Rome|Milan|Tokyo|Osaka|Amman|'
    r'Almaty|Nairobi|Pristina|Kuwait City|Vientiane|Riga|Beirut|Tripoli|Vilnius|'
    r'Antananarivo|Kuala Lumpur|Bamako|Rabat|Casablanca|Mexico City|Chisinau|'
    r'Ulaanbaatar|Kathmandu|Amsterdam|The Hague|Managua|Niamey|Lagos|Abuja|Oslo|'
    r'Muscat|Islamabad|Karachi|Lahore|Ramallah|Gaza City|Panama City|Asuncion|Lima|'
    r'Manila|Warsaw|Lisbon|Doha|Bucharest|Moscow|St Petersburg|Kigali|Riyadh|Jeddah|'
    r'Dakar|Belgrade|Freetown|Singapore|Mogadishu|Johannesburg|Cape Town|Seoul|Busan|'
    r'Madrid|Barcelona|Colombo|Khartoum|Stockholm|Bern|Zurich|Geneva|Damascus|Taipei|'
    r'Dushanbe|Dar es Salaam|Bangkok|Tunis|Ankara|Istanbul|Ashgabat|Kampala|Kyiv|Kiev|'
    r'Dubai|Abu Dhabi|London|Manchester|Washington|New York|Los Angeles|Chicago|Houston|'
    r'Montevideo|Tashkent|Caracas|Hanoi|Ho Chi Minh|Sanaa|Lusaka|Harare|'
    r'Auckland|Wellington|Canberra|Melbourne|Brisbane|Perth)\b',
    re.IGNORECASE
)

def extract_location(text: str) -> Optional[str]:
    # Try city first (more specific)
    m = _CITY_RE.search(text)
    if m:
        return m.group(0)
    # Fall back to country
    m = _COUNTRY_RE.search(text)
    return m.group(0).title() if m else None

def classify(text: str) -> tuple[str, str]:
    t = text.lower()
    for pattern, (label, icon) in CATEGORY_MAP.items():
        if any(k in t for k in pattern.split("|")):
            return label, icon
    return "General", "📰"

def get_severity(text: str) -> str:
    t = text.lower()
    for level, kws in SEVERITY_MAP.items():
        if any(k in t for k in kws):
            return level
    return "low"

def uid(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()[:8]

def google_news_url(title: str) -> str:
    q = re.sub(r'[^\w\s]', '', title)[:80].strip()
    return "https://news.google.com/search?q=" + "+".join(q.split())

# ── Continent Fetcher ─────────────────────────────────────────────────────────
def fetch_continent(name: str, cfg: dict, max_per_continent: int = 10) -> list[dict]:
    kws     = cfg["keywords"]
    color   = cfg["color"]
    default = cfg["default_coords"]
    events  = {}
    seen_titles: set = set()

    for source_name, feed_url in cfg["feeds"]:
        try:
            feed = feedparser.parse(feed_url)
        except Exception as e:
            print(f"    [{source_name}] Feed error: {e}")
            continue

        for entry in feed.entries:
            if len(events) >= max_per_continent * 2:  # fetch extra, dedupe later
                break
            title   = (entry.get("title") or "").strip()
            summary = (entry.get("summary") or entry.get("description") or title).strip()
            # Strip HTML tags from summary
            summary = re.sub(r'<[^>]+>', '', summary)[:500]

            if not title or title in seen_titles:
                continue
            seen_titles.add(title)

            combined = (title + " " + summary).lower()
            # For non-Middle East continents, skip Middle East keyword-heavy articles
            # (they appear in many feeds but belong in their own continent)
            if name != "Middle East" and any(k in combined for k in [
                "gaza","israel","iran","hamas","hezbollah","west bank","beirut","damascus","houthi"
            ]):
                continue
            # Relevance check for continent
            if not any(k in combined for k in kws):
                continue

            location = extract_location(title + " " + summary)
            coords   = geocode(location) if location else None
            if not coords:
                coords = default  # fall back to continent centre

            cat, icon = classify(combined)
            pub = entry.get("published_parsed")
            ts  = (datetime(*pub[:6], tzinfo=timezone.utc).isoformat()
                   if pub else datetime.now(timezone.utc).isoformat())

            article_url = entry.get("link", "")
            # Validate URL has a real path; otherwise use Google News search
            try:
                from urllib.parse import urlparse
                parsed = urlparse(article_url)
                if not parsed.path or parsed.path == "/":
                    article_url = google_news_url(title)
            except Exception:
                article_url = google_news_url(title)

            eid = uid(title)
            if eid not in events:
                events[eid] = {
                    "id":        eid,
                    "title":     title,
                    "summary":   summary,
                    "category":  cat,
                    "icon":      icon,
                    "continent": name,
                    "severity":  get_severity(combined),
                    "source":    source_name,
                    "url":       article_url,
                    "timestamp": ts,
                    "location":  location or name,
                    "lat":       coords[0],
                    "lon":       coords[1],
                    "color":     color,
                }

    # Sort by severity desc, take top N
    sev_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    result = sorted(events.values(), key=lambda x: (sev_order.get(x["severity"], 4), x["timestamp"]))
    return result[:max_per_continent]


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    load_cache()
    all_events = []
    total_target = 10  # top N per continent

    print(f"Global Events Tracker v2 — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"Fetching top {total_target} events per continent...\n")

    for continent, cfg in CONTINENTS.items():
        print(f"[{continent}]")
        events = fetch_continent(continent, cfg, total_target)
        all_events.extend(events)
        print(f"  -> {len(events)} events\n")
        save_cache()
        time.sleep(0.5)

    # Final sort: critical first, then by continent + timestamp
    sev_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    all_events.sort(key=lambda x: (sev_order.get(x["severity"], 4), x["continent"], x["timestamp"]))

    output = {
        "updated": datetime.now(timezone.utc).isoformat(),
        "total":   len(all_events),
        "events":  all_events,
    }

    with open("events_data.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"Saved {len(all_events)} events -> events_data.json")
    save_cache()


if __name__ == "__main__":
    main()

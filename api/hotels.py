"""Vercel serverless function — hotel search with dataset caching.

First search calls SerpAPI (1 call) + Google Places (free), saves results
to data/hotels_cache.json. Subsequent requests within 48h serve the cached file.
"""

import json
import os
import sys
import tempfile
import time
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

# Allow imports from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hotel_agent import (
    apply_official_stars,
    categorize_hotels,
    compute_distances,
    enrich_with_details,
    loyalty_url,
    merge_places_data,
    normalize_serpapi,
    score_hotels,
    search_hotels_serpapi,
    search_places,
    tag_cc_programs,
)
from flight_agent import get_serpapi_call_log, reset_serpapi_call_log
from serpapi_guard import BudgetExceeded, check_serpapi_budget, log_serpapi_calls

CITY_MAP = {
    "venice": "Venice, Italy",
    "ravenna": "Ravenna, Italy",
    "istanbul": "Istanbul, Turkey",
}

CITY_DEFAULTS = {
    "venice": {"check_in": "2026-06-30", "check_out": "2026-07-03"},
    "ravenna": {"check_in": "2026-07-02", "check_out": "2026-07-03"},
    "istanbul": {"check_in": "2026-07-13", "check_out": "2026-07-14"},
}

# Use /tmp on Vercel (read-only filesystem), data/ locally
_data_dir = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"
)
CACHE_DIR = "/tmp" if os.environ.get("VERCEL") else _data_dir
CACHE_TTL = 48 * 3600  # 48 hours in seconds


def _cache_path(city_key: str, check_in: str, check_out: str) -> str:
    """Build cache file path for a hotel search."""
    return os.path.join(CACHE_DIR, f"hotels_cache_{city_key}_{check_in}_{check_out}.json")


def _read_cache(path: str) -> dict | None:
    """Read cached hotel data if fresh (within TTL)."""
    try:
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        cached_at = data.get("_cached_at", 0)
        if time.time() - cached_at > CACHE_TTL:
            return None  # stale
        return data
    except (json.JSONDecodeError, OSError):
        return None


def _write_cache(path: str, payload: dict) -> None:
    """Atomically write cache file."""
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=CACHE_DIR, suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _hotel_to_dict(h: dict) -> dict:
    """Convert hotel to JSON-safe dict."""
    d = dict(h)
    d["loyalty_url"] = loyalty_url(h)
    return d


def _fresh_search(city_key: str, city_query: str, check_in: str, check_out: str) -> dict:
    """Run full hotel search pipeline. Costs 1 SerpAPI call + free Google Places calls."""
    reset_serpapi_call_log()

    # 1. SerpAPI: pricing, star class, photos, booking links (1 SerpAPI call)
    serpapi_raw = search_hotels_serpapi(city_query, check_in, check_out)
    hotels = normalize_serpapi(serpapi_raw, check_in, check_out)

    # 2. Google Places: user ratings, reviews (free)
    places_results = search_places(city_query)
    hotels = merge_places_data(hotels, places_results)

    # 2b. Fill missing star ratings from official open data (if available for city)
    hotels = apply_official_stars(hotels, city_key)

    # 3. Compute distances from landmark
    hotels = compute_distances(hotels, city_key)

    # 4. Tag credit card programs
    hotels = tag_cc_programs(hotels, city_key)

    # 5. Score using combined data
    hotels = score_hotels(hotels)

    # 6. Enrich top results with Place Details (free)
    hotels = enrich_with_details(hotels)

    # 7. Tag city on each hotel (for loyalty URLs)
    for h in hotels:
        h["city"] = city_query

    # 8. Categorize
    categorized = categorize_hotels(hotels)

    call_log = get_serpapi_call_log()

    from datetime import date
    ci = date.fromisoformat(check_in)
    co = date.fromisoformat(check_out)
    nights = (co - ci).days

    return {
        "best_overall": [_hotel_to_dict(h) for h in categorized["best_overall"]],
        "best_marriott": [_hotel_to_dict(h) for h in categorized["best_marriott"]],
        "best_hilton": [_hotel_to_dict(h) for h in categorized["best_hilton"]],
        "city": city_key,
        "check_in": check_in,
        "check_out": check_out,
        "nights": nights,
        "serpapi_calls": len(call_log),
        "_cached_at": time.time(),
    }


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)

            city_key = params.get("city", ["venice"])[0].lower()
            defaults = CITY_DEFAULTS.get(city_key, CITY_DEFAULTS["venice"])
            check_in = params.get("check_in", [defaults["check_in"]])[0]
            check_out = params.get("check_out", [defaults["check_out"]])[0]

            city_query = CITY_MAP.get(city_key, "Venice, Italy")
            cache_file = _cache_path(city_key, check_in, check_out)

            # Try cache first
            cached = _read_cache(cache_file)
            if cached:
                body = json.dumps(cached, indent=2).encode()
            else:
                # Budget check before fresh search (1 SerpAPI call)
                check_serpapi_budget(num_calls=1, source="hotels")
                payload = _fresh_search(city_key, city_query, check_in, check_out)
                log_serpapi_calls(num_calls=1, source="hotels")
                _write_cache(cache_file, payload)
                body = json.dumps(payload, indent=2).encode()

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header(
                "Cache-Control",
                "public, s-maxage=172800, stale-while-revalidate=172800",
            )
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        except Exception as exc:
            # On error, try serving stale cache as fallback
            try:
                cache_file = _cache_path(city_key, check_in, check_out)
                if os.path.exists(cache_file):
                    with open(cache_file, "r", encoding="utf-8") as f:
                        stale = json.load(f)
                    stale["_stale"] = True
                    body = json.dumps(stale, indent=2).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    self.wfile.write(body)
                    return
            except Exception:
                pass

            error = json.dumps({"error": str(exc)}).encode()
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(error)

    def do_OPTIONS(self):
        """Handle CORS preflight."""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

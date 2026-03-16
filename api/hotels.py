"""Vercel serverless function — on-demand hotel search.

Combines SerpAPI (pricing, stars, photos, booking links) with
Google Places API (user ratings, reviews, editorial summaries).
CDN cached by query params so the same combo is only searched once per 48h.
"""

import json
import os
import sys
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

# Allow imports from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hotel_agent import (
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

CITY_MAP = {
    "venice": "Venice, Italy",
    "istanbul": "Istanbul, Turkey",
}


def _hotel_to_dict(h: dict) -> dict:
    """Convert hotel to JSON-safe dict."""
    d = dict(h)
    d["loyalty_url"] = loyalty_url(h)
    return d


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)

            city_key = params.get("city", ["venice"])[0].lower()
            check_in = params.get("check_in", ["2026-06-30"])[0]
            check_out = params.get("check_out", ["2026-07-03"])[0]

            city_query = CITY_MAP.get(city_key, "Venice, Italy")

            reset_serpapi_call_log()

            # 1. SerpAPI: pricing, star class, photos, booking links
            serpapi_raw = search_hotels_serpapi(city_query, check_in, check_out)
            hotels = normalize_serpapi(serpapi_raw, check_in, check_out)

            # 2. Google Places: user ratings, reviews
            places_results = search_places(city_query)
            # places_count used for debugging only
            hotels = merge_places_data(hotels, places_results)

            # 3. Compute distances from landmark
            hotels = compute_distances(hotels, city_key)

            # 4. Tag credit card programs
            hotels = tag_cc_programs(hotels, city_key)

            # 5. Score using combined data
            hotels = score_hotels(hotels)

            # 6. Enrich top results with Place Details (review text)
            hotels = enrich_with_details(hotels)

            # 7. Categorize
            categorized = categorize_hotels(hotels)

            call_log = get_serpapi_call_log()

            from datetime import date
            ci = date.fromisoformat(check_in)
            co = date.fromisoformat(check_out)
            nights = (co - ci).days

            payload = {
                "best_overall": [_hotel_to_dict(h) for h in categorized["best_overall"]],
                "best_marriott": [_hotel_to_dict(h) for h in categorized["best_marriott"]],
                "best_hilton": [_hotel_to_dict(h) for h in categorized["best_hilton"]],
                "city": city_key,
                "check_in": check_in,
                "check_out": check_out,
                "nights": nights,
                "serpapi_calls": len(call_log),
            }

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

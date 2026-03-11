"""Vercel serverless function — runs the flight search pipeline and returns JSON.

Cached at the CDN edge for 24 hours so SerpAPI credits aren't burned on every
page load.  After 24h the next visitor triggers a fresh search in the background
(stale-while-revalidate).
"""

import json
import os
import sys
from http.server import BaseHTTPRequestHandler

# Allow imports from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flight_agent import (
    ROUTES,
    TRIP_DATE,
    _flight_to_dict,
    _today_pst,
    dedup_flights,
    filter_flights,
    label_fare_types,
    normalize,
    score_flights,
    search_serpapi,
    search_skyscanner,
)


def _run_route(route_cfg: dict) -> dict:
    """Run the full pipeline for one origin and return {outbound: {...}, return: {...}}."""
    today = _today_pst()
    origin = route_cfg["origin"]
    result = {}

    for direction in ("outbound", "return"):
        cfg = route_cfg[direction]
        raw = search_serpapi(cfg["from"], cfg["to"], cfg["dates"])
        if origin == "LAX" and direction == "return":
            raw += search_skyscanner(cfg["from"], cfg["to"], cfg["dates"])
        flights = normalize(raw)
        flights = filter_flights(flights)
        flights = dedup_flights(flights)
        flights = label_fare_types(flights)
        flights = score_flights(
            flights,
            airline_bonuses=cfg["bonuses"],
            auto_top_picks=cfg["auto_top"],
        )
        result[direction] = {
            "generated": today.isoformat(),
            "days_to_go": (TRIP_DATE - today).days,
            "trip_date": TRIP_DATE.isoformat(),
            "flights": [_flight_to_dict(f) for f in flights],
        }

    return result


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            payload = {}
            for route in ROUTES:
                payload[route["origin"]] = _run_route(route)

            body = json.dumps(payload, indent=2).encode()

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            # CDN caches 24h; serves stale for 1h while revalidating
            self.send_header(
                "Cache-Control",
                "public, s-maxage=86400, stale-while-revalidate=3600",
            )
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        except Exception as exc:
            error = json.dumps({"error": str(exc)}).encode()
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(error)

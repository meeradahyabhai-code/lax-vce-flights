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

from datetime import date

from flight_agent import (
    TRIP_DATE,
    _layover_info,
    dedup_flights,
    filter_flights,
    label_fare_types,
    normalize,
    score_flights,
    search_serpapi,
)


def _build_payload(flights: list[dict]) -> dict:
    today = date.today()
    return {
        "generated": today.isoformat(),
        "days_to_go": (TRIP_DATE - today).days,
        "trip_date": TRIP_DATE.isoformat(),
        "flights": [
            {
                "primary_airline": f["primary_airline"],
                "airlines": f["airlines"],
                "departure_time": f["departure_time"],
                "arrival_time": f["arrival_time"],
                "stops": f["stops"],
                "total_layover_min": f["total_layover_min"],
                "total_duration_min": f["total_duration_min"],
                "price": f["price"],
                "score": f["score"],
                "search_date": f["search_date"],
                "fare_type": f.get("fare_type", "Economy Main"),
                "economy_main_price": f.get("economy_main_price"),
                "google_flights_url": f.get("google_flights_url", ""),
                "layover_info": _layover_info(f),
            }
            for f in flights
        ],
    }


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            raw = search_serpapi()
            flights = normalize(raw)
            flights = filter_flights(flights)
            flights = dedup_flights(flights)
            flights = label_fare_types(flights)
            flights = score_flights(flights)

            payload = _build_payload(flights)
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

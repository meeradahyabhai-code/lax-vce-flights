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
    AUTO_TOP_PICK_NONSTOP,
    DEPARTURE_DATES,
    RETURN_AIRLINE_BONUSES,
    RETURN_AUTO_TOP_PICK_NONSTOP,
    RETURN_DATES,
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


def _build_payload(outbound: list[dict], return_flights: list[dict]) -> dict:
    today = _today_pst()
    return {
        "generated": today.isoformat(),
        "days_to_go": (TRIP_DATE - today).days,
        "trip_date": TRIP_DATE.isoformat(),
        "outbound_flights": [_flight_to_dict(f) for f in outbound],
        "return_flights": [_flight_to_dict(f) for f in return_flights],
    }


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            # Outbound: LAX → VCE
            raw_out = search_serpapi("LAX", "VCE", DEPARTURE_DATES)
            outbound = normalize(raw_out)
            outbound = filter_flights(outbound)
            outbound = dedup_flights(outbound)
            outbound = label_fare_types(outbound)
            outbound = score_flights(outbound)

            # Return: IST → LAX
            raw_ret = search_serpapi("IST", "LAX", RETURN_DATES)
            raw_ret += search_skyscanner("IST", "LAX", RETURN_DATES)
            ret = normalize(raw_ret)
            ret = filter_flights(ret)
            ret = dedup_flights(ret)
            ret = label_fare_types(ret)
            ret = score_flights(
                ret,
                airline_bonuses=RETURN_AIRLINE_BONUSES,
                auto_top_picks=RETURN_AUTO_TOP_PICK_NONSTOP,
            )

            payload = _build_payload(outbound, ret)
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

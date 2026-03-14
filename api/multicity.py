"""Vercel serverless function — on-demand multi-city flight search.

Searches for combined outbound + return itineraries (e.g. LAX→VCE + IST→LAX)
as a single multi-city booking on Google Flights via SerpAPI.

CDN cached by query params so the same combo is only searched once per 48h.
"""

import json
import os
import sys
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

# Allow imports from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flight_agent import (
    ROUTES,
    TRIP_DATE,
    _flight_to_dict,
    _today_pst,
    dedup_flights,
    filter_flights,
    get_serpapi_call_log,
    label_fare_types,
    normalize,
    reset_serpapi_call_log,
    score_flights,
    search_serpapi_multicity,
)


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)

            origin = params.get("origin", ["LAX"])[0].upper()
            out_date = params.get("out_date", ["2026-06-29"])[0]
            ret_date = params.get("ret_date", ["2026-07-14"])[0]

            # Find route config for this origin
            route_cfg = None
            for r in ROUTES:
                if r["origin"] == origin:
                    route_cfg = r
                    break
            if not route_cfg:
                route_cfg = ROUTES[0]  # fallback to LAX

            out_cfg = route_cfg["outbound"]
            ret_cfg = route_cfg["return"]
            max_stops = out_cfg.get("max_stops", 1)
            min_stops = out_cfg.get("min_stops", 0)

            today = _today_pst()
            reset_serpapi_call_log()

            # Search multi-city
            raw = search_serpapi_multicity(
                origin=out_cfg["from"],
                dest=out_cfg["to"],
                return_from=ret_cfg["from"],
                outbound_date=out_date,
                return_date=ret_date,
                max_stops=max_stops,
                min_stops=min_stops,
            )

            # Run through pipeline
            flights = normalize(raw)
            flights = filter_flights(flights, max_stops=max_stops)
            flights = dedup_flights(flights)
            flights = label_fare_types(flights)
            flights = score_flights(
                flights,
                airline_bonuses=out_cfg["bonuses"],
                auto_top_picks=out_cfg["auto_top"],
            )

            call_log = get_serpapi_call_log()

            payload = {
                "flights": [_flight_to_dict(f) for f in flights],
                "origin": origin,
                "out_date": out_date,
                "ret_date": ret_date,
                "generated": today.isoformat(),
                "days_to_go": (TRIP_DATE - today).days,
                "serpapi_calls": len(call_log),
            }

            body = json.dumps(payload, indent=2).encode()

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            # CDN caches 48h; same combo served from cache for all users
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

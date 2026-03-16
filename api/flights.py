"""Vercel serverless function — runs the flight search pipeline and returns JSON.

Cached at the CDN edge for 24 hours so SerpAPI credits aren't burned on every
page load.  After 24h the next visitor triggers a fresh search in the background
(stale-while-revalidate).
"""

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
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
    get_serpapi_call_log,
    label_fare_types,
    normalize,
    reset_serpapi_call_log,
    search_serpapi,
    search_skyscanner,
)


def _run_direction(origin: str, direction: str, cfg: dict, today) -> tuple[str, dict]:
    """Run the pipeline for one origin+direction. Returns (direction, result_dict)."""
    # Economy search
    ms = cfg.get("max_stops", 1)
    mins = cfg.get("min_stops", 0)
    raw = search_serpapi(cfg["from"], cfg["to"], cfg["dates"], max_stops=ms, min_stops=mins)
    if origin == "LAX" and direction == "return":
        raw += search_skyscanner(cfg["from"], cfg["to"], cfg["dates"])
    flights = normalize(raw)
    flights = filter_flights(flights, max_stops=ms)
    flights = dedup_flights(flights)
    flights = label_fare_types(flights)

    # Scoring is done client-side so we can update ranking logic
    # without invalidating the CDN cache or burning SerpAPI calls
    return direction, {
        "generated": today.isoformat(),
        "days_to_go": (TRIP_DATE - today).days,
        "trip_date": TRIP_DATE.isoformat(),
        "flights": [_flight_to_dict(f) for f in flights],
    }


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            today = _today_pst()
            reset_serpapi_call_log()
            payload = {}

            # Run all origin+direction combos in parallel
            with ThreadPoolExecutor(max_workers=6) as pool:
                futures = {}
                for route in ROUTES:
                    origin = route["origin"]
                    payload[origin] = {}
                    for direction in ("outbound", "return"):
                        cfg = route[direction]
                        fut = pool.submit(
                            _run_direction, origin, direction, cfg, today,
                        )
                        futures[fut] = origin

                for fut in as_completed(futures):
                    origin = futures[fut]
                    direction, result = fut.result()
                    payload[origin][direction] = result

            # Log and include SerpAPI usage summary
            call_log = get_serpapi_call_log()
            total_calls = len(call_log)
            by_route = {}
            for c in call_log:
                by_route[c["route"]] = by_route.get(c["route"], 0) + 1
            print(f"[SerpAPI Summary] {total_calls} total calls: "
                  + ", ".join(f"{r}={n}" for r, n in sorted(by_route.items())))
            if total_calls > 50:
                print(f"[SerpAPI WARNING] {total_calls} calls exceeds budget of 42!")

            payload["_serpapi_usage"] = {
                "total_calls": total_calls,
                "by_route": by_route,
                "cache_ttl_hours": 48,
            }

            body = json.dumps(payload, indent=2).encode()

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            # CDN caches 48h; serves stale while revalidating in background
            # ~42 SerpAPI calls per refresh
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
            self.end_headers()
            self.wfile.write(error)

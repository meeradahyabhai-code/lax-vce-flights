"""Vercel serverless function — multi-city flight search with caching.

Searches for combined outbound + return itineraries (e.g. LAX→VCE + IST→LAX)
as a single multi-city booking on Google Flights via SerpAPI.

First search saves results to data/multicity_cache_*.json.
Subsequent requests within 48h serve the cached file (0 SerpAPI calls).
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
from serpapi_guard import BudgetExceeded, check_serpapi_budget, log_serpapi_calls

CACHE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"
)
CACHE_TTL = 48 * 3600


def _cache_path(origin: str, out_date: str, ret_date: str) -> str:
    return os.path.join(CACHE_DIR, f"multicity_cache_{origin}_{out_date}_{ret_date}.json")


def _read_cache(path: str) -> dict | None:
    try:
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if time.time() - data.get("_cached_at", 0) > CACHE_TTL:
            return None
        return data
    except (json.JSONDecodeError, OSError):
        return None


def _write_cache(path: str, payload: dict) -> None:
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


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        origin = "LAX"
        out_date = "2026-06-29"
        ret_date = "2026-07-14"
        cache_file = ""

        try:
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)

            origin = params.get("origin", ["LAX"])[0].upper()
            out_date = params.get("out_date", ["2026-06-29"])[0]
            ret_date = params.get("ret_date", ["2026-07-14"])[0]

            cache_file = _cache_path(origin, out_date, ret_date)

            # Try cache first
            cached = _read_cache(cache_file)
            if cached:
                body = json.dumps(cached, indent=2).encode()
            else:
                # Budget check (2 SerpAPI calls per multicity search)
                check_serpapi_budget(num_calls=2, source="multicity")

                # Find route config
                route_cfg = None
                for r in ROUTES:
                    if r["origin"] == origin:
                        route_cfg = r
                        break
                if not route_cfg:
                    route_cfg = ROUTES[0]

                out_cfg = route_cfg["outbound"]
                ret_cfg = route_cfg["return"]
                max_stops = out_cfg.get("max_stops", 1)
                min_stops = out_cfg.get("min_stops", 0)

                today = _today_pst()
                reset_serpapi_call_log()

                raw = search_serpapi_multicity(
                    origin=out_cfg["from"],
                    dest=out_cfg["to"],
                    return_from=ret_cfg["from"],
                    outbound_date=out_date,
                    return_date=ret_date,
                    max_stops=max_stops,
                    min_stops=min_stops,
                )

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
                    "_cached_at": time.time(),
                }

                log_serpapi_calls(num_calls=len(call_log), source="multicity")
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
            # On error, try stale cache as fallback
            try:
                if cache_file and os.path.exists(cache_file):
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

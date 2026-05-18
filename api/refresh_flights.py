"""Vercel serverless function — on-demand per-origin flight refresh.

Mirrors api/hotels.py: GET ?origin=LAX checks data/flights_cache_LAX.json,
serves it if fresh (<48h). If stale, runs SerpAPI for that origin's
outbound + return, saves to cache, returns the data.

Cost per stale-origin refresh: ~10 SerpAPI calls (vs 42 for full 4-origin refresh).
"""

import json
import os
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

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
from serpapi_guard import BudgetExceeded, check_serpapi_budget, log_serpapi_calls

# Use /tmp on Vercel (read-only filesystem), data/ locally
_DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"
)
CACHE_DIR = "/tmp" if os.environ.get("VERCEL") else _DATA_DIR
# Fallback: read repo-committed cache files when /tmp is empty (Vercel cold start)
FALLBACK_DIR = _DATA_DIR
CACHE_TTL = 48 * 3600  # 48 hours

VALID_ORIGINS = {r["origin"]: r for r in ROUTES}
EXPECTED_CALLS_PER_ORIGIN = 11  # ~5-6 dates × 2 directions = ~10-12 calls
MIN_FLIGHTS_PER_DIRECTION = 3


def _cache_path(origin: str, base_dir: str | None = None) -> str:
    if base_dir is None:
        base_dir = CACHE_DIR
    return os.path.join(base_dir, f"flights_cache_{origin}.json")


def _read_cache(origin: str) -> dict | None:
    """Return cached payload if fresh, else None. Tries /tmp then repo data/."""
    seen = set()
    for base in (CACHE_DIR, FALLBACK_DIR):
        if base in seen:
            continue
        seen.add(base)
        path = _cache_path(origin, base)
        try:
            if not os.path.exists(path):
                continue
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            cached_at = data.get("_cached_at", 0)
            if time.time() - cached_at > CACHE_TTL:
                continue
            return data
        except (json.JSONDecodeError, OSError):
            continue
    return None


def _read_stale(origin: str) -> dict | None:
    """Return cached payload regardless of age (for error fallback)."""
    seen = set()
    for base in (CACHE_DIR, FALLBACK_DIR):
        if base in seen:
            continue
        seen.add(base)
        path = _cache_path(origin, base)
        try:
            if not os.path.exists(path):
                continue
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
    return None


def _write_cache(origin: str, payload: dict) -> None:
    """Atomically write cache file."""
    path = _cache_path(origin)
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=CACHE_DIR, suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except (OSError, NameError):
            pass


def _run_direction(origin: str, direction: str, cfg: dict, today) -> tuple:
    """Run the SerpAPI pipeline for one origin+direction. Same logic as scripts/refresh_flights.py."""
    ms = cfg.get("max_stops", 1)
    mins = cfg.get("min_stops", 0)
    raw = search_serpapi(cfg["from"], cfg["to"], cfg["dates"], max_stops=ms, min_stops=mins)
    if origin == "LAX" and direction == "return":
        raw += search_skyscanner(cfg["from"], cfg["to"], cfg["dates"])
    flights = normalize(raw)
    flights = filter_flights(flights, max_stops=ms)
    flights = dedup_flights(flights)
    flights = label_fare_types(flights)
    return direction, {
        "generated": today.isoformat(),
        "days_to_go": (TRIP_DATE - today).days,
        "trip_date": TRIP_DATE.isoformat(),
        "flights": [_flight_to_dict(f) for f in flights],
    }


def _fresh_search(origin: str) -> dict:
    """Run outbound + return SerpAPI searches for one origin. Returns full payload."""
    route = VALID_ORIGINS[origin]
    today = _today_pst()
    reset_serpapi_call_log()

    payload = {"origin": origin}
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = []
        for direction in ("outbound", "return"):
            cfg = route[direction]
            futures.append(pool.submit(_run_direction, origin, direction, cfg, today))
        for fut in futures:
            direction, result = fut.result()
            payload[direction] = result

    # Validate
    for direction in ("outbound", "return"):
        flights = payload.get(direction, {}).get("flights", [])
        if len(flights) < MIN_FLIGHTS_PER_DIRECTION:
            raise ValueError(
                f"{origin} {direction}: only {len(flights)} flights "
                f"(min: {MIN_FLIGHTS_PER_DIRECTION})"
            )

    call_log = get_serpapi_call_log()
    payload["_cached_at"] = time.time()
    payload["_serpapi_calls"] = len(call_log)
    payload["_refreshed_at"] = datetime.now(ZoneInfo("America/Los_Angeles")).isoformat()
    return payload


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            origin = params.get("origin", ["LAX"])[0].upper()

            if origin not in VALID_ORIGINS:
                return self._respond(400, {"error": f"Unknown origin: {origin}"})

            # Try fresh cache first
            cached = _read_cache(origin)
            if cached:
                cached["_from_cache"] = True
                return self._respond(200, cached)

            # Budget check before SerpAPI
            check_serpapi_budget(num_calls=EXPECTED_CALLS_PER_ORIGIN, source=f"refresh_flights:{origin}")

            payload = _fresh_search(origin)
            log_serpapi_calls(num_calls=payload.get("_serpapi_calls", EXPECTED_CALLS_PER_ORIGIN),
                              source=f"refresh_flights:{origin}")
            _write_cache(origin, payload)
            payload["_from_cache"] = False
            return self._respond(200, payload)

        except BudgetExceeded as exc:
            stale = _read_stale(origin)
            if stale:
                stale["_stale"] = True
                stale["_error"] = "SerpAPI budget exceeded — serving stale cache"
                return self._respond(200, stale)
            return self._respond(429, {"error": str(exc)})
        except Exception as exc:
            stale = _read_stale(origin) if origin in VALID_ORIGINS else None
            if stale:
                stale["_stale"] = True
                stale["_error"] = str(exc)[:200]
                return self._respond(200, stale)
            return self._respond(500, {"error": str(exc)[:200]})

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _respond(self, status: int, body: dict) -> None:
        out = json.dumps(body, indent=2).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(out)

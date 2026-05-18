"""Vercel serverless function — serves cached flight data from dataset.

Reads per-origin caches first (data/flights_cache_<ORIGIN>.json or /tmp on Vercel),
falls back to legacy single-file cache (data/flights_cache.json). Never calls SerpAPI.

Per-origin caches are written by api/refresh_flights.py on demand.
"""

import json
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
TMP_DIR = "/tmp"
LEGACY_CACHE = os.path.join(DATA_DIR, "flights_cache.json")
ORIGINS = ("LAX", "AKL", "ATL", "YVR")


def _load_per_origin_cache() -> dict:
    """Merge per-origin cache files into legacy multi-origin shape."""
    merged = {}
    for origin in ORIGINS:
        for base in (TMP_DIR, DATA_DIR):
            path = os.path.join(base, f"flights_cache_{origin}.json")
            if not os.path.exists(path):
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    payload = json.load(f)
            except (json.JSONDecodeError, OSError):
                continue
            entry = {}
            for direction in ("outbound", "return"):
                if direction in payload:
                    entry[direction] = payload[direction]
            if entry:
                merged[origin] = entry
                # Preserve cache metadata so frontend can display freshness
                if "_cached_at" in payload:
                    merged[origin]["_cached_at"] = payload["_cached_at"]
                if "_refreshed_at" in payload:
                    merged[origin]["_refreshed_at"] = payload["_refreshed_at"]
                break  # don't read DATA_DIR if TMP_DIR already had it
    return merged


def _load_legacy_cache() -> dict:
    if not os.path.exists(LEGACY_CACHE):
        return {}
    try:
        with open(LEGACY_CACHE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            per_origin = _load_per_origin_cache()
            legacy = _load_legacy_cache()

            # Per-origin entries take precedence; fill gaps from legacy.
            merged = {}
            for origin in ORIGINS:
                if origin in per_origin:
                    merged[origin] = per_origin[origin]
                elif origin in legacy:
                    merged[origin] = legacy[origin]

            if not merged:
                raise FileNotFoundError(
                    "No flight data available. Visit /api/refresh_flights?origin=LAX to fetch."
                )

            # Pass through legacy metadata if present
            for k, v in legacy.items():
                if k.startswith("_") and k not in merged:
                    merged[k] = v

            body = json.dumps(merged, indent=2).encode()

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

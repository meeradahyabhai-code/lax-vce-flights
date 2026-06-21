"""Vercel serverless function — dynamic, distance-aware restaurant search.

The hybrid's live layer. The curated catalog (restaurants.json) is the instant base;
this widens coverage on demand within a radius of the port anchor, using Google Places
only (free tier, NO SerpAPI). First request per (port, radius) runs the search and
caches it; later requests serve the cache. Restaurants barely change, so the TTL is
long (30 days) and the CDN holds it too.

  GET /api/restaurants?port=venice&radius_mi=10
"""
import json
import os
import sys
import tempfile
import time
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from restaurant_finder import search_area, ANCHORS
from hotel_agent import _places_key

# /tmp on Vercel (read-only fs), data/ locally — same pattern as api/hotels.py
_data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
CACHE_DIR = "/tmp" if os.environ.get("VERCEL") else _data_dir
CACHE_TTL = 30 * 24 * 3600       # 30 days — restaurants are effectively static
ALLOWED_RADII = (3, 5, 10, 15, 25)


def _cache_path(port: str, radius_mi: int) -> str:
    return os.path.join(CACHE_DIR, f"restaurants_dyn_{port}_{radius_mi}.json")


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
            json.dump(payload, f)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass


# --- Places photo proxy (folded in to stay under the Hobby 12-function cap) ---
# Streams a Google Places photo by resource name so the key stays private and no
# photo pack is committed. GET /api/restaurants?photo=places/<id>/photos/<id>&w=520
def _serve_photo(self, ref: str, w: int):
    if not ref.startswith("places/") or "/photos/" not in ref:
        return self._send(400, {"error": "bad photo ref"})
    w = max(80, min(1200, w))
    try:
        r = requests.get(f"https://places.googleapis.com/v1/{ref}/media",
                         params={"maxWidthPx": w, "key": _places_key()}, timeout=15)
        if r.status_code != 200:
            return self._send(502, {"error": f"places photo {r.status_code}"})
        self.send_response(200)
        self.send_header("Content-Type", r.headers.get("Content-Type", "image/jpeg"))
        self.send_header("Content-Length", str(len(r.content)))
        self.send_header("Cache-Control", "public, max-age=31536000, s-maxage=31536000, immutable")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(r.content)
    except Exception as exc:  # noqa: BLE001
        self._send(502, {"error": str(exc)[:120]})


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        port = "venice"
        radius_mi = 10
        try:
            params = parse_qs(urlparse(self.path).query)
            photo = (params.get("photo", [""])[0]).strip()
            if photo:
                try:
                    pw = int(params.get("w", ["520"])[0])
                except (ValueError, TypeError):
                    pw = 520
                return _serve_photo(self, photo, pw)
            port = params.get("port", ["venice"])[0].lower()
            if port not in ANCHORS:
                return self._send(400, {"error": f"unknown port '{port}'"})
            try:
                radius_mi = int(float(params.get("radius_mi", ["10"])[0]))
            except (ValueError, TypeError):
                radius_mi = 10
            # clamp to a sane allowed set so the cache key space stays small
            radius_mi = min(ALLOWED_RADII, key=lambda r: abs(r - radius_mi))

            cache_file = _cache_path(port, radius_mi)
            cached = _read_cache(cache_file)
            if cached:
                return self._send(200, cached, cache=True)

            result = search_area(port, radius_mi=radius_mi, sleep=time.sleep)
            result["_cached_at"] = time.time()
            _write_cache(cache_file, result)
            return self._send(200, result, cache=True)

        except Exception as exc:  # noqa: BLE001
            # serve stale cache (even past TTL) if we have any, else error
            try:
                p = _cache_path(port, radius_mi)
                if os.path.exists(p):
                    with open(p, encoding="utf-8") as f:
                        data = json.load(f)
                    data["_stale"] = True
                    return self._send(200, data, cache=False)
            except Exception:  # noqa: BLE001
                pass
            return self._send(500, {"error": str(exc)[:200]})

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _send(self, status: int, body: dict, cache: bool = False):
        out = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        if cache:
            # restaurants are static — let the CDN hold it a long time
            self.send_header("Cache-Control",
                             "public, s-maxage=2592000, stale-while-revalidate=2592000")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(out)

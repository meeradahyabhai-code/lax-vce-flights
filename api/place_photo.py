"""Vercel serverless function — proxy a Google Places photo by resource name.

Dynamic restaurant cards (from api/restaurants.py) carry a Places `photo_ref` like
"places/<id>/photos/<id>" instead of a downloaded file. This streams that photo
server-side so the API key stays private and nothing has to be committed to the repo
(no photo pack). Heavily CDN-cached since a given photo never changes.

  GET /api/place_photo?ref=places/<id>/photos/<id>&w=480
"""
import os
import sys
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from hotel_agent import _places_key  # noqa: E402

MAX_W = 1200
DEFAULT_W = 480


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            params = parse_qs(urlparse(self.path).query)
            ref = (params.get("ref", [""])[0]).strip()
            # only accept a well-formed Places photo resource name
            if not ref.startswith("places/") or "/photos/" not in ref:
                return self._fail(400, "bad ref")
            try:
                w = int(params.get("w", [str(DEFAULT_W)])[0])
            except (ValueError, TypeError):
                w = DEFAULT_W
            w = max(80, min(MAX_W, w))

            url = f"https://places.googleapis.com/v1/{ref}/media"
            r = requests.get(url, params={"maxWidthPx": w, "key": _places_key()},
                             timeout=15, stream=False)
            if r.status_code != 200:
                return self._fail(502, f"places photo {r.status_code}")
            data = r.content
            ctype = r.headers.get("Content-Type", "image/jpeg")

            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            # a photo for a given ref never changes — cache hard
            self.send_header("Cache-Control",
                             "public, max-age=31536000, s-maxage=31536000, immutable")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)
        except Exception as exc:  # noqa: BLE001
            self._fail(500, str(exc)[:120])

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.end_headers()

    def _fail(self, status: int, msg: str):
        body = msg.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

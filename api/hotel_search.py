"""Vercel serverless function — proxy Google Places text search for hotel autocomplete.

Accepts GET with ?q=hotel+name, returns matching lodging results as JSON.
Keeps the API key server-side.
"""

import json
import os
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

import requests

GOOGLE_PLACES_API_KEY = os.environ.get("GOOGLE_PLACES_API_KEY", "").strip()
PLACES_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            params = parse_qs(urlparse(self.path).query)
            query = params.get("q", [""])[0].strip()

            if not query or len(query) < 2:
                return self._json([])

            if not GOOGLE_PLACES_API_KEY:
                return self._json([])

            resp = requests.post(
                PLACES_SEARCH_URL,
                headers={
                    "Content-Type": "application/json",
                    "X-Goog-Api-Key": GOOGLE_PLACES_API_KEY,
                    "X-Goog-FieldMask": (
                        "places.displayName,"
                        "places.formattedAddress,"
                        "places.addressComponents,"
                        "places.location"
                    ),
                },
                json={
                    "textQuery": query + " hotel",
                    "includedType": "lodging",
                    "maxResultCount": 5,
                },
                timeout=8,
            )
            resp.raise_for_status()
            places = resp.json().get("places", [])

            results = []
            for p in places:
                name = p.get("displayName", {}).get("text", "")
                address = p.get("formattedAddress", "")
                city = ""
                for comp in p.get("addressComponents", []):
                    types = comp.get("types", [])
                    if "locality" in types:
                        loc = comp.get("longText", "").lower()
                        if "venice" in loc or "venezia" in loc:
                            city = "venice"
                        elif "ravenna" in loc:
                            city = "ravenna"
                        elif "istanbul" in loc:
                            city = "istanbul"
                results.append({
                    "name": name,
                    "address": address,
                    "city": city,
                })

            self._json(results)

        except Exception:
            self._json([])

    def _json(self, data):
        out = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(out)

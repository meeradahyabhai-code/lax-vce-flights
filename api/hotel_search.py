"""Vercel serverless function — proxy Google Places Autocomplete for hotel typeahead.

Accepts GET with ?q=hotel+name, returns matching lodging suggestions.
Uses the Places Autocomplete (New) API — designed for typeahead, lower cost.
Keeps the API key server-side.
"""

import json
import os
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

import requests

GOOGLE_PLACES_API_KEY = os.environ.get("GOOGLE_PLACES_API_KEY", "").strip()
AUTOCOMPLETE_URL = "https://places.googleapis.com/v1/places:autocomplete"
PLACE_DETAILS_URL = "https://places.googleapis.com/v1/places/"


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            params = parse_qs(urlparse(self.path).query)
            query = params.get("q", [""])[0].strip()

            if not query or len(query) < 2:
                return self._json([])

            if not GOOGLE_PLACES_API_KEY:
                return self._json([])

            # Step 1: Autocomplete suggestions
            resp = requests.post(
                AUTOCOMPLETE_URL,
                headers={
                    "Content-Type": "application/json",
                    "X-Goog-Api-Key": GOOGLE_PLACES_API_KEY,
                },
                json={
                    "input": query,
                    "includedPrimaryTypes": ["lodging"],
                },
                timeout=8,
            )
            resp.raise_for_status()
            suggestions = resp.json().get("suggestions", [])

            results = []
            for s in suggestions:
                pred = s.get("placePrediction", {})
                if not pred:
                    continue
                place_id = pred.get("placeId", "")
                main_text = pred.get("structuredFormat", {}).get("mainText", {}).get("text", "")
                secondary_text = pred.get("structuredFormat", {}).get("secondaryText", {}).get("text", "")

                # Infer city from secondary text
                city = ""
                sec_lower = secondary_text.lower()
                if "venice" in sec_lower or "venezia" in sec_lower:
                    city = "venice"
                elif "ravenna" in sec_lower:
                    city = "ravenna"
                elif "istanbul" in sec_lower:
                    city = "istanbul"

                results.append({
                    "name": main_text,
                    "address": secondary_text,
                    "city": city,
                    "place_id": place_id,
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

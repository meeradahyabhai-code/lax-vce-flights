"""Vercel serverless function — return Google Maps API key for frontend Places Autocomplete."""

import json
import os
from http.server import BaseHTTPRequestHandler

GOOGLE_PLACES_API_KEY = os.environ.get("GOOGLE_PLACES_API_KEY", "")


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        out = json.dumps({"key": GOOGLE_PLACES_API_KEY}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(out)

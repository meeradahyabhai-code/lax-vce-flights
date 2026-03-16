"""Vercel serverless function — Points AI strategy via OpenAI API.

Accepts POST with flight details and user's loyalty programs,
returns a concise points strategy from GPT-4o-mini.
"""

import json
import os
from http.server import BaseHTTPRequestHandler

import requests

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

SYSTEM_PROMPT = (
    "You are a points and miles expert. The cash price shown is always Economy Main cabin. "
    "Given a flight and the user's loyalty programs, "
    "return exactly 4 bullet points, each one short line (under 20 words):\n"
    "• Transfer: [which program transfers to this airline, ratio — or 'no transfer path']\n"
    "• Points price: [rough estimate in miles/points for Economy Main vs the cash fare]\n"
    "• Verdict: [use points or pay cash for Economy Main, and why in 5 words]\n"
    "• Action: [one specific thing to do today]\n\n"
    "IMPORTANT: All prices and comparisons are for Economy Main cabin only. "
    "Never reference Basic Economy, business class, or first class. "
    "Be direct. No emojis. No hedging. "
    "If no program connects to this airline, say so and give a cash strategy instead."
)


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            if not OPENAI_API_KEY:
                raise ValueError("OPENAI_API_KEY not configured")

            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            payload = json.loads(body)

            flight = payload.get("flight", {})
            programs = payload.get("programs", [])
            days_until = payload.get("days_until_travel", "unknown")
            flight_nums = flight.get("flight_numbers", [])
            fn_str = ", ".join(flight_nums) if flight_nums else "unknown"

            user_msg = (
                f"Flight: {flight.get('primary_airline', '?')} {fn_str} | "
                f"{flight.get('search_date', '?')} | "
                f"dep {flight.get('departure_time', '?')[:16]} | "
                f"stops={flight.get('stops', '?')} | "
                f"${flight.get('price', '?')} Economy Main\n"
                f"Route: {flight.get('route', 'unknown')}\n"
                f"Days until travel: {days_until}\n"
                f"User's loyalty programs: {', '.join(programs) if programs else 'None'}"
            )

            resp = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "gpt-4o-mini",
                    "max_tokens": 200,
                    "temperature": 0,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_msg},
                    ],
                },
                timeout=15,
            )
            resp.raise_for_status()
            result = resp.json()

            strategy = result["choices"][0]["message"]["content"]

            out = json.dumps({"strategy": strategy}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()
            self.wfile.write(out)

        except Exception:
            out = json.dumps({
                "strategy": "Points strategy unavailable right now \u2014 try again later",
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(out)

    def do_OPTIONS(self):
        """Handle CORS preflight."""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

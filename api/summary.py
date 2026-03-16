"""Vercel serverless function — generates an AI flight briefing via OpenAI API.

Accepts POST with JSON body containing flight data and family picks,
returns a 2-3 sentence summary from GPT-4o-mini.
"""

import json
import os
from http.server import BaseHTTPRequestHandler

import requests

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

SYSTEM_PROMPT = (
    "You are a warm, confident flight briefing assistant for a family planning "
    "a cruise trip. All prices shown are Economy Main cabin fares. "
    "Return exactly 4 bullet points, each one short line (under 20 words), using this format:\n"
    "• Best deal: [airline, price, date, nonstop/stops]\n"
    "• Family: [who booked or showed interest, by first name]\n"
    "• Timing: [buy now/wait, based on days out and price trend]\n"
    "• Tip: [one actionable search tip — incognito, Tue/Wed, etc.]\n\n"
    "For multi-city results, the price is total round trip (both legs). "
    "Mention if multi-city saves vs booking separately.\n"
    "Tone: warm, specific, confident. No emojis. No hedging like 'it may be worth considering.' "
    "Keep each bullet to one punchy line."
)


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            if not OPENAI_API_KEY:
                raise ValueError("OPENAI_API_KEY not configured")

            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            payload = json.loads(body)

            flights = payload.get("flights", [])
            family_picks = payload.get("family_picks", [])
            direction = payload.get("direction", "outbound")

            # Build a compact data summary
            flight_lines = []
            for f in flights[:15]:
                line = (
                    f"{f.get('primary_airline', '?')} | "
                    f"{f.get('search_date', '?')} | "
                    f"dep {f.get('departure_time', '?')[:16]} | "
                    f"stops={f.get('stops', '?')} | "
                    f"${f.get('price', '?')} | "
                    f"score={f.get('score', '?')}"
                )
                if f.get('type') == 'multi_city':
                    ret_airline = f.get('return_airline', '?')
                    ret_date = f.get('return_date', '?')
                    ret_stops = f.get('return_stops', '?')
                    line += f" | MULTI-CITY return: {ret_airline} {ret_date} stops={ret_stops}"
                flight_lines.append(line)

            pick_lines = []
            for p in family_picks[:20]:
                arrival = p.get("arrival_time", "")
                arr_str = f" arriving {arrival[:16]}" if arrival else ""
                pick_lines.append(
                    f"{p.get('name', '?')} {p.get('action', '?')} "
                    f"{p.get('airline', '?')} on {p.get('flight_date', '?')}"
                    f"{arr_str}"
                )

            origin = payload.get("origin", "LAX")
            route_map = {
                "LAX": {"outbound": "LAX to VCE", "return": "IST to LAX", "multicity": "LAX to VCE + IST to LAX"},
                "AKL": {"outbound": "AKL to VCE", "return": "IST to AKL", "multicity": "AKL to VCE + IST to AKL"},
                "ATL": {"outbound": "ATL to VCE", "return": "IST to ATL", "multicity": "ATL to VCE + IST to ATL"},
                "YVR": {"outbound": "YVR to VCE", "return": "IST to YVR", "multicity": "YVR to VCE + IST to YVR"},
            }
            city_names = {"LAX": "Los Angeles", "AKL": "Auckland", "ATL": "Atlanta", "YVR": "Vancouver"}
            route = route_map.get(origin, route_map["LAX"]).get(direction, "LAX to VCE")
            city = city_names.get(origin, origin)

            days_until = payload.get("days_until_travel", "unknown")
            prices = [f.get("price", 0) for f in flights if f.get("price")]
            price_range = ""
            if prices:
                price_range = f"Price range: ${min(prices)} – ${max(prices)}"

            user_msg = (
                f"Origin city: {city}\n"
                f"Direction: {route}\n"
                f"Days until travel: {days_until}\n"
                f"{price_range}\n\n"
                f"Available flights (sorted by score, lower=better):\n"
                + "\n".join(flight_lines)
                + "\n\nFamily activity:\n"
                + ("\n".join(pick_lines) if pick_lines else "No family picks yet.")
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
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_msg},
                    ],
                },
                timeout=15,
            )
            resp.raise_for_status()
            result = resp.json()

            summary = result["choices"][0]["message"]["content"]

            out = json.dumps({"summary": summary}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()
            self.wfile.write(out)

        except Exception as exc:
            out = json.dumps({
                "summary": "Flight data updated daily. Check back each morning for today's best picks.",
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

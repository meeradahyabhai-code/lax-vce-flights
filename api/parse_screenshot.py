"""Vercel serverless function — parse booking confirmation screenshot via OpenAI Vision.

Accepts POST with base64 image, returns parsed flight details as JSON.
"""

import json
import os
from http.server import BaseHTTPRequestHandler

import requests

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

PARSE_PROMPT = (
    "Extract flight details from this booking confirmation. "
    "This is for a family trip in JUNE/JULY 2026: outbound flights end at Venice (VCE), "
    "return flights depart from Istanbul (IST). "
    "The year is 2026 — if the image shows only month/day (e.g. '28 JUN'), use 2026 as the year.\n\n"
    "CRITICAL RULES:\n"
    "- If the image shows multiple legs (e.g., 'Flight 1 of 2' and 'Flight 2 of 2'), "
    "this is ONE connecting itinerary, not separate trips.\n"
    "- departure_airport = the FIRST origin (where the journey starts)\n"
    "- arrival_airport = the FINAL destination (where the journey ends)\n"
    "- Any intermediate airports are STOPOVERS, not the arrival\n"
    "- For outbound flights, the final destination should be VCE (Venice)\n"
    "- For return flights, the origin should be IST (Istanbul)\n"
    "- If the final destination is VCE, direction is 'outbound'\n"
    "- If the origin is IST, direction is 'return'\n"
    "- Combine all operating airlines with ' + ' (e.g., 'Virgin Atlantic + British Airways')\n"
    "- Combine all flight numbers with ' / ' (e.g., 'VS8 / BA602')\n\n"
    "Return ONLY a JSON object with these exact fields:\n"
    "{\n"
    '  "airline": string — all operating airlines joined with " + ",\n'
    '  "flight_number": string — all flight numbers joined with " / ",\n'
    '  "departure_airport": string — IATA code of FIRST origin,\n'
    '  "departure_city": string or null,\n'
    '  "departure_date": string — YYYY-MM-DD of first leg departure,\n'
    '  "departure_time": string — HH:MM 24hr of first leg departure,\n'
    '  "arrival_airport": string — IATA code of FINAL destination,\n'
    '  "arrival_city": string or null,\n'
    '  "arrival_date": string — YYYY-MM-DD of last leg arrival,\n'
    '  "arrival_time": string — HH:MM 24hr of last leg arrival,\n'
    '  "stopovers": [{"airport": "IATA", "city": string, '
    '"arrival_time": "HH:MM", "departure_time": "HH:MM"}] — intermediate stops only,\n'
    '  "direction": "outbound" if arriving VCE, "return" if departing IST, else null\n'
    "}\n"
    "If any field cannot be determined from the image, set it to null. "
    "Do not guess. Return only valid JSON, no markdown fences."
)


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            if not OPENAI_API_KEY:
                raise ValueError("OPENAI_API_KEY not configured")

            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            payload = json.loads(body)

            image_data = payload.get("image", "")
            if not image_data:
                raise ValueError("No image data provided")

            # Ensure proper data URL format
            if not image_data.startswith("data:"):
                image_data = "data:image/png;base64," + image_data

            resp = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "gpt-4o",
                    "max_tokens": 500,
                    "temperature": 0,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": PARSE_PROMPT},
                                {
                                    "type": "image_url",
                                    "image_url": {"url": image_data},
                                },
                            ],
                        }
                    ],
                },
                timeout=30,
            )
            resp.raise_for_status()
            result = resp.json()

            text = result["choices"][0]["message"]["content"]
            # Strip markdown fences if present
            text = text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

            parsed = json.loads(text)

            out = json.dumps({"parsed": parsed}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()
            self.wfile.write(out)

        except json.JSONDecodeError:
            out = json.dumps({
                "error": "Could not parse flight details from image. Please try a clearer screenshot.",
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(out)

        except Exception as exc:
            out = json.dumps({
                "error": f"Screenshot parsing failed: {str(exc)}",
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

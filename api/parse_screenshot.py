"""Vercel serverless function — parse booking confirmation screenshot via OpenAI Vision.

Accepts POST with base64 image, returns parsed flight details as JSON.
"""

import json
import os
from http.server import BaseHTTPRequestHandler

import requests

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

PARSE_PROMPT = (
    "Extract flight details from this booking confirmation. Return ONLY a JSON object "
    "with these exact fields:\n"
    "{\n"
    '  "airline": string or null,\n'
    '  "flight_number": string or null,\n'
    '  "departure_airport": string or null (use IATA code if visible),\n'
    '  "departure_city": string or null,\n'
    '  "departure_date": string or null (YYYY-MM-DD),\n'
    '  "departure_time": string or null (HH:MM 24hr),\n'
    '  "arrival_airport": string or null (use IATA code if visible),\n'
    '  "arrival_city": string or null,\n'
    '  "arrival_date": string or null (YYYY-MM-DD),\n'
    '  "arrival_time": string or null (HH:MM 24hr),\n'
    '  "stopovers": array of {"airport": string, "city": string, '
    '"arrival_time": string, "departure_time": string} or empty array,\n'
    '  "direction": "outbound" or "return" or null\n'
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

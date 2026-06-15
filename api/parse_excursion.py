"""Vercel serverless function — parse an excursion/tour booking screenshot via OpenAI Vision.

Accepts POST with a base64 image (and optional port_date hint), returns parsed
excursion details as JSON. Mirrors api/parse_screenshot.py conventions.

The frontend enforces the required fields (date, start_time, duration, price);
this endpoint just extracts what it can and leaves the rest null for the user
to fill in the confirm modal.
"""

import json
import os
from http.server import BaseHTTPRequestHandler

import requests

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

# Light abuse guards for this public, billable Vision endpoint.
# Exact-host allowlist (both the current and former production domains). Add a
# preview host here explicitly if one ever needs the parser.
ALLOWED_ORIGINS = {"https://dfc-2026.vercel.app", "https://lax-vce-flights.vercel.app"}
MAX_UPLOAD_BYTES = 8_000_000  # ~8 MB cap on an incoming screenshot payload


def _origin_ok(origin):
    # Browsers send Origin on cross- and same-origin POSTs. This is spoofable by
    # non-browser clients; real cost protection needs per-IP rate limiting, a
    # project-wide gap to address across all OpenAI endpoints.
    return origin in ALLOWED_ORIGINS

PARSE_PROMPT = (
    "Extract excursion / shore-tour booking details from this screenshot. "
    "This is for a Mediterranean cruise in July 2026. The year is 2026 if a "
    "month/day appears without a year.\n\n"
    "Return ONLY a JSON object with these exact fields:\n"
    "{\n"
    '  "title": string — the tour/excursion name,\n'
    '  "date": string — YYYY-MM-DD, or null if not shown,\n'
    '  "start_time": string — HH:MM 24hr, or null,\n'
    '  "duration": string — a decimal amount with a unit, e.g. "3 hours", "3.5 hours", or "90 minutes" (use decimals, not fractions like 1/2), or null,\n'
    '  "price": string — per-person price amount only (digits), or null,\n'
    '  "currency": string — e.g. "USD", "EUR", or null,\n'
    '  "operator": string — tour company or booking source, or null,\n'
    '  "meeting_point": string — where to meet, or null,\n'
    '  "link": string — a booking URL if visible, or null,\n'
    '  "notes": string — one short line of anything else useful, or null\n'
    "}\n"
    "If a field cannot be determined from the image, set it to null. "
    "Do not guess. Return only valid JSON, no markdown fences."
)


def strip_fences(text):
    """Remove ```json ... ``` markdown fences the model sometimes adds."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


def _send_json(self, obj, status=200):
    out = json.dumps(obj).encode()
    self.send_response(status)
    self.send_header("Content-Type", "application/json")
    self.send_header("Access-Control-Allow-Origin", "*")
    self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
    self.send_header("Access-Control-Allow-Headers", "Content-Type")
    self.end_headers()
    self.wfile.write(out)


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            if not _origin_ok(self.headers.get("Origin")):
                _send_json(self, {"error": "Forbidden"}, status=403)
                return
            if not OPENAI_API_KEY:
                raise ValueError("OPENAI_API_KEY not configured")

            content_length = int(self.headers.get("Content-Length", 0))
            if content_length > MAX_UPLOAD_BYTES:
                _send_json(self, {"error": "Image too large; please use a smaller screenshot."}, status=413)
                return
            body = self.rfile.read(content_length)
            payload = json.loads(body)

            image_data = payload.get("image", "")
            if not image_data:
                raise ValueError("No image data provided")
            if not image_data.startswith("data:"):
                image_data = "data:image/png;base64," + image_data

            # Optional hint: the port's date, so the model knows the default context.
            port_date = payload.get("port_date")
            prompt = PARSE_PROMPT
            if port_date:
                prompt += (
                    f"\n\nContext: this excursion is for the cruise stop on {port_date}. "
                    "If the screenshot shows no date, still return null for date "
                    "(the app will default it to the port date)."
                )

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
                                {"type": "text", "text": prompt},
                                {"type": "image_url", "image_url": {"url": image_data}},
                            ],
                        }
                    ],
                },
                timeout=30,
            )
            resp.raise_for_status()
            result = resp.json()

            text = strip_fences(result["choices"][0]["message"]["content"])
            parsed = json.loads(text)
            _send_json(self, {"parsed": parsed})

        except json.JSONDecodeError:
            _send_json(self, {
                "error": "Could not read the excursion details. Try a clearer screenshot or enter it manually.",
            })
        except Exception as exc:
            _send_json(self, {"error": f"Excursion parsing failed: {str(exc)}"})

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

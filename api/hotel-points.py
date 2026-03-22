"""Vercel serverless function — Hotel Points AI strategy via OpenAI API.

Accepts POST with hotel details and user's loyalty programs,
returns a concise hotel points strategy from GPT-4o-mini.
"""

import json
import os
from http.server import BaseHTTPRequestHandler

import requests

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

HOTEL_TRANSFER_DATA = """
HOTEL LOYALTY TRANSFER PARTNERS (credit card → hotel program):
- Amex Membership Rewards → Marriott Bonvoy 1:1, Hilton Honors 1:2
- Chase Ultimate Rewards → World of Hyatt 1:1, IHG One Rewards 1:1
- Citi ThankYou → (no direct hotel transfers)
- Capital One Miles → (no direct hotel transfers)

APPROXIMATE POINTS PER NIGHT (luxury hotels):
- Marriott Bonvoy: 40,000–80,000 points/night (Cat 5–7)
- Hilton Honors: 50,000–95,000 points/night
- World of Hyatt: 15,000–30,000 points/night (Cat 4–6)
- IHG One Rewards: 30,000–60,000 points/night

AMEX HOTEL CREDIT CARD BENEFITS:
- Amex Fine Hotels & Resorts (FHR): $200 property credit, room upgrade, daily breakfast, guaranteed 4pm late checkout, noon check-in. Available on Amex Platinum and Centurion cards.
- Amex The Hotel Collection (THC): $100 statement credit on stays of 2+ nights. Available on Amex Platinum and Centurion cards.

IMPORTANT RULES:
- Points value benchmark: ~0.7 cpp for Marriott, ~0.5 cpp for Hilton, ~1.7 cpp for Hyatt, ~0.5 cpp for IHG
- Hyatt points are the most valuable per point; Hilton/IHG require more points but transfer at better ratios
- FHR/THC benefits can stack with loyalty program benefits
- Always compare total points cost vs cash rate to determine cents-per-point value
"""

SYSTEM_PROMPT = (
    "You are a hotel points and miles expert. "
    "Given a hotel and the user's loyalty programs, use the transfer data below "
    "to give ACCURATE advice for THIS SPECIFIC HOTEL BRAND.\n\n"
    + HOTEL_TRANSFER_DATA +
    "\nReturn exactly 4 bullet points, each one short line (under 20 words):\n"
    "• Transfer: [which of THEIR programs transfers to this hotel's loyalty program — state the ratio]\n"
    "• Points price: [estimated points/night and total, vs the cash rate — note cpp value]\n"
    "• FHR/THC: [if they have Amex, note applicable FHR or THC benefits; otherwise say N/A]\n"
    "• Verdict: [use points or pay cash, and why in 5 words]\n\n"
    "CRITICAL: Match the hotel brand. If it's a Marriott property, discuss Bonvoy points. "
    "If it's Hilton, discuss Hilton Honors. If independent/unknown brand, say points booking "
    "is not available and suggest FHR/THC or cash strategies instead.\n\n"
    "Be direct. No emojis. No hedging. "
    "If no program connects to this hotel brand, say so and give a cash or FHR/THC strategy instead."
)


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            if not OPENAI_API_KEY:
                raise ValueError("OPENAI_API_KEY not configured")

            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            payload = json.loads(body)

            hotel = payload.get("hotel", {})
            programs = payload.get("programs", [])
            days_until = payload.get("days_until_travel", "unknown")

            rate = hotel.get("rate_per_night")
            total = hotel.get("total_rate")
            nights = hotel.get("nights", 1)
            price_str = f"${rate}/night (${total} total for {nights} night{'s' if nights != 1 else ''})" if rate else "Price not available"

            cc_progs = hotel.get("cc_programs", [])
            cc_str = ", ".join(cc_progs) if cc_progs else "None"

            user_msg = (
                f"Hotel: {hotel.get('name', '?')}\n"
                f"Brand: {hotel.get('brand', 'independent')}\n"
                f"Star class: {hotel.get('star_class', '?')}\n"
                f"City: {hotel.get('city', '?')}\n"
                f"Cash price: {price_str}\n"
                f"CC hotel programs: {cc_str}\n"
                f"Check-in: {hotel.get('check_in', '?')}\n"
                f"Rating: {hotel.get('overall_rating', '?')}/5\n"
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

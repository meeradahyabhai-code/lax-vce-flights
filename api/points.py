"""Vercel serverless function — Points AI strategy via OpenAI API.

Accepts POST with flight details and user's loyalty programs,
returns a concise points strategy from GPT-4o-mini.
"""

import json
import os
from http.server import BaseHTTPRequestHandler

import requests

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

ALLIANCE_DATA = """
AIRLINE ALLIANCES:
- SkyTeam: Delta, Air France, KLM, ITA Airways, Korean Air, Vietnam Airlines, Aerolineas Argentinas
- Star Alliance: United, Lufthansa, Swiss, Austrian, Turkish Airlines, Singapore Airlines, Air Canada, ANA, EVA Air, TAP Air Portugal, Scandinavian Airlines (SAS), LOT Polish
- oneworld: American, British Airways, Cathay Pacific, Qatar Airways, Finnair, Iberia, Japan Airlines, Alaska Airlines

TRANSFER PARTNERS (credit card → airline):
- Amex Membership Rewards → Delta 1:1, British Airways 1:1, Singapore Airlines 1:1, Air Canada 1:1, ANA 1:1, Cathay Pacific 1:1, Emirates 1:1, Etihad 1:1, JetBlue 1:1 (also 1:1 to Hilton, Marriott at worse ratios)
- Chase Ultimate Rewards → United 1:1, British Airways 1:1, Singapore Airlines 1:1, Air France/KLM 1:1, Southwest 1:1, Aer Lingus 1:1, Emirates 1:1, Iberia 1:1
- Citi ThankYou → Turkish Airlines 1:1, Singapore Airlines 1:1, Cathay Pacific 1:1, Air France/KLM 1:1, Etihad 1:1, Qatar Airways 1:1, Emirates 1:1, JetBlue 1:1
- Capital One Miles → Air France/KLM 1:1, British Airways 1:1, Turkish Airlines 1:1, Singapore Airlines 1:1, Cathay Pacific 1:1, Finnair 1:1, Etihad 1:1, Emirates 1:1, TAP 1:1

IMPORTANT RULES:
- You can book flights on ANY airline in the same alliance using that alliance's miles
- E.g., Delta SkyMiles can book Air France or KLM flights, United miles can book Lufthansa flights
- Transfer partner programs can book on the partner airline AND its alliance partners
"""

SYSTEM_PROMPT = (
    "You are a points and miles expert. The cash price shown is always Economy Main cabin. "
    "Given a flight and the user's loyalty programs, use the alliance and transfer data below "
    "to give ACCURATE advice for THIS SPECIFIC AIRLINE.\n\n"
    + ALLIANCE_DATA +
    "\nReturn exactly 4 bullet points, each one short line (under 20 words):\n"
    "• Transfer: [which of THEIR programs connects to THIS airline — if indirect, "
    "explain the chain: e.g. 'Amex MR → British Airways Avios (oneworld partner of American)']\n"
    "• Points price: [rough estimate in miles/points for Economy Main vs the cash fare]\n"
    "• Verdict: [use points or pay cash for Economy Main, and why in 5 words]\n"
    "• Action: [one specific thing to do today]\n\n"
    "If the transfer is indirect (e.g., using BA Avios to book an American Airlines flight "
    "because both are oneworld), you MUST explain the connection. Never just say 'transfer to X' "
    "without explaining why X can book a flight on Y.\n\n"
    "CRITICAL: Match the airline on the ticket. If the flight is American Airlines, use oneworld "
    "partners. If it's Delta, use SkyTeam. Do NOT mix alliances. "
    "All prices and comparisons are for Economy Main cabin only. "
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
            airlines = flight.get("airlines", [])
            airlines_str = " + ".join(dict.fromkeys(airlines)) if airlines else flight.get("primary_airline", "?")

            user_msg = (
                f"Operating airline(s): {airlines_str}\n"
                f"Flight number(s): {fn_str}\n"
                f"Date: {flight.get('search_date', '?')}\n"
                f"Departure: {flight.get('departure_time', '?')[:16]}\n"
                f"Stops: {flight.get('stops', '?')}\n"
                f"Price: ${flight.get('price', '?')} Economy Main\n"
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

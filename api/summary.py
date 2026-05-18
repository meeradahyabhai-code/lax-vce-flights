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
    "a Mediterranean cruise trip. All prices shown are Economy Main cabin fares.\n\n"
    "CRITICAL — only use facts from the data the user gives you in this message:\n"
    "- Use ONLY the airlines, prices, dates, stop counts, and times that appear in "
    "the flight list. Never invent a flight, price, or airline.\n"
    "- Use ONLY the names that literally appear in the 'Confirmed travelers' or "
    "'Family activity' sections. Never invent a person, family name, or surname.\n"
    "- Use the 'Cruise itinerary' section to anchor timing advice. The embark date "
    "is a hard deadline for outbound flights; the disembark date is when return "
    "flights can begin. Do NOT invent ports, dates, or times.\n"
    "- Do not claim a price trend or historical price unless it is given.\n\n"
    "Return exactly 4 bullet points, each one short line (under 22 words), using this format:\n"
    "• Best deal: [airline, price, date, nonstop/stops — straight from the flight list]\n"
    "• Timing: [Reference the cruise embark date (outbound) or disembark date (return) "
    "from the Cruise itinerary. Compare the best flight's arrival/departure time to "
    "that anchor and state the buffer in days. Example: 'Board day is July 3, 5pm "
    "Ravenna. This option lands 7/2 noon, safe 1-day buffer.']\n"
    "• Family: [Summarize the Confirmed travelers list for this origin/direction. "
    "Count + first names + the dates they're arriving (outbound) or departing "
    "(return). Group people who share a date. If the list is empty, fall back to "
    "Family activity. If both are empty, say \"No one's locked a flight from this "
    "origin yet.\" Never invent names.]\n"
    "• Tip: [one concrete, true booking tip — e.g. set a fare alert, compare Tue/Wed "
    "departures, check the multi-city fare. No myths like 'browse incognito.']\n\n"
    "For multi-city results, the price is total round trip (both legs). "
    "Mention if multi-city saves vs booking separately.\n"
    "Tone: warm, specific, confident. No emojis. No em dashes (use commas or periods "
    "instead). No hedging like 'it may be worth considering.' "
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
                arr = f.get("arrival_time", "") or ""
                arr_str = f" | arr {arr[:16]}" if arr else ""
                line = (
                    f"{f.get('primary_airline', '?')} | "
                    f"{f.get('search_date', '?')} | "
                    f"dep {f.get('departure_time', '?')[:16]}"
                    f"{arr_str} | "
                    f"stops={f.get('stops', '?')} | "
                    f"${f.get('price', '?')} | "
                    f"score={f.get('score', '?')}"
                )
                if f.get('type') == 'multi_city':
                    ret_airline = f.get('return_airline', '?')
                    ret_date = f.get('return_date', '?')
                    ret_stops = f.get('return_stops', '?')
                    ret_arr = f.get('return_arrival_time', '') or ''
                    ret_arr_str = f" arr {ret_arr[:16]}" if ret_arr else ""
                    line += (
                        f" | MULTI-CITY return: {ret_airline} {ret_date} "
                        f"stops={ret_stops}{ret_arr_str}"
                    )
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
                price_range = f"Price range: ${min(prices)} - ${max(prices)}"

            cruise = payload.get("cruise") or {}
            if cruise:
                cruise_lines = (
                    f"Embark: {cruise.get('embark_date','?')} at "
                    f"{cruise.get('embark_port','?')}"
                    + (f" (departs {cruise['embark_depart']})" if cruise.get("embark_depart") else "")
                    + "\n"
                    f"Disembark: {cruise.get('disembark_date','?')} at "
                    f"{cruise.get('disembark_port','?')}"
                    + (f" (arrives {cruise['disembark_arrive']})" if cruise.get("disembark_arrive") else "")
                )
            else:
                cruise_lines = "Not provided."

            confirmed = payload.get("confirmed_travelers") or []
            if confirmed:
                # Group by date so the model can say "N people on 7/2, M on 7/1"
                by_date = {}
                for c in confirmed[:25]:
                    d = c.get("date", "?") or "?"
                    by_date.setdefault(d, []).append(c)
                confirmed_lines = []
                for d in sorted(by_date.keys()):
                    group = by_date[d]
                    names = ", ".join(g.get("name", "?") for g in group if g.get("name"))
                    airlines = sorted({g.get("airline", "") for g in group if g.get("airline")})
                    airline_str = f" via {', '.join(airlines)}" if airlines else ""
                    # First non-empty time for the group
                    times = [g.get("time", "") for g in group if g.get("time")]
                    t = times[0] if times else ""
                    t_str = f" @ {t[:16]}" if t else ""
                    label = "arriving" if direction == "outbound" else "departing"
                    confirmed_lines.append(
                        f"{d}: {len(group)} confirmed {label}{t_str}{airline_str} ({names})"
                    )
                confirmed_block = "\n".join(confirmed_lines)
            else:
                confirmed_block = "No confirmed travelers for this origin/direction yet."

            user_msg = (
                f"Origin city: {city}\n"
                f"Direction: {route}\n"
                f"Days until travel: {days_until}\n"
                f"{price_range}\n\n"
                f"Cruise itinerary:\n{cruise_lines}\n\n"
                f"Confirmed travelers ({direction}, this origin):\n{confirmed_block}\n\n"
                f"Available flights (sorted by score, lower=better):\n"
                + "\n".join(flight_lines)
                + "\n\nFamily activity (Google Sheet picks):\n"
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

"""Vercel serverless function — answers free-form questions about the loaded flights.

Uses gpt-4.1-nano (cheap, ~$0.10/$0.40 per 1M tokens). Grounded in the
flight slice the client sends. Rate-limited per IP.
"""

import json
import os
import time
from collections import deque
from http.server import BaseHTTPRequestHandler

import requests

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
MODEL = "gpt-4.1-nano"

# Per-IP sliding window: max 10 questions per 5 minutes.
# Stored in module globals; resets when the lambda cold-starts (good enough).
RATE_LIMIT_MAX = 10
RATE_LIMIT_WINDOW = 300
_rate_log: dict = {}

# Hard daily cap across all IPs (defense in depth).
DAILY_CAP = 500
_daily_log = deque()
DAY_SECONDS = 86400

SYSTEM_PROMPT_FLIGHTS = (
    "You are a flight assistant for a family planning a cruise trip. "
    "Answer ONLY using the JSON flight data provided in the user message. "
    "If the question cannot be answered from that data, say so in one sentence. "
    "Keep answers under 60 words. No bullets unless the user asks. "
    "Prices are USD, Economy Main cabin. Don't invent flights, fares, or airlines. "
    "Tone: warm, direct, no hedging, no marketing language."
)

SYSTEM_PROMPT_HOTELS = (
    "You are a hotel assistant for a family planning a pre-cruise hotel stay. "
    "Answer ONLY using the JSON hotel data provided in the user message. "
    "If the question cannot be answered from that data, say so in one sentence. "
    "Keep answers under 60 words. No bullets unless the user asks. "
    "Prices are USD per night and total. Don't invent hotels, brands, or rates. "
    "Tone: warm, direct, no hedging, no marketing language."
)


SYSTEM_PROMPT_RESTAURANTS = (
    "You are a warm, knowledgeable restaurant concierge for a family on a cruise. "
    "Recommend ONLY from the JSON restaurant list provided in the user message — never invent. "
    "Match the user's intent (cuisine, vibe, occasion, budget, vegetarian needs, near a landmark, "
    "walkable, Michelin, best-rated). Prefer genuinely strong matches; it's fine to return few. "
    "Reply with a JSON object: {\"answer\": string, \"picks\": [ref numbers from the \"ref\" field, best first, max 5]}. "
    "The answer is <=55 words, warm and direct, no marketing language, names your top pick and why. "
    "If nothing fits, say so in the answer and return an empty picks array."
)


SYSTEM_PROMPT_DAY = (
    "You are a warm, intelligent cruise concierge. For each meal you are given the SITUATION already worked "
    "out for the traveler. Write ONE short, natural, helpful sentence per meal that fits that exact situation. "
    "Do NOT change or re-derive the situation. The situations:\n"
    "- 'booked': they have a restaurant booked for that meal — say they're set, naming it and the time.\n"
    "- 'tour': a tour overlaps this meal. Give a practical option using the tour's start time (e.g. the tour "
    "starts at 8:15am, so have a light breakfast on the ship first, then grab something on the tour if hungry).\n"
    "- 'free': they are ashore in the port and free — they can eat in the port itself or back on the ship.\n"
    "- 'aboard': the ship hasn't docked yet or has already sailed — that meal is on the ship.\n"
    "- 'pre': pre-cruise; they're in the city and can eat anywhere.\n"
    "Use the real times from the schedule. Natural and human, never robotic, no emojis or exclamation marks. "
    'Return JSON: {"breakfast": string, "lunch": string, "dinner": string}.'
)


def _check_rate_limit(ip: str) -> bool:
    now = time.time()
    log = _rate_log.setdefault(ip, deque())
    while log and log[0] < now - RATE_LIMIT_WINDOW:
        log.popleft()
    if len(log) >= RATE_LIMIT_MAX:
        return False
    log.append(now)
    return True


def _check_daily_cap() -> bool:
    now = time.time()
    while _daily_log and _daily_log[0] < now - DAY_SECONDS:
        _daily_log.popleft()
    if len(_daily_log) >= DAILY_CAP:
        return False
    _daily_log.append(now)
    return True


def build_messages(question: str, payload: dict) -> tuple:
    """Build (system_prompt, user_msg) for the OpenAI call. Pure function — testable."""
    context = payload.get("context", "flights")

    if context == "day_summary":
        d = payload.get("day", {})
        user_msg = (
            f"Port: {d.get('port', '?')}\nDate: {d.get('date', '?')}\n"
            f"Ship schedule: {d.get('schedule', '?')}\n"
            "Per-meal situation (already determined — phrase it, do not change it):\n"
            f"{json.dumps(d.get('meals', []))}\n"
            "Write the JSON now."
        )
        return SYSTEM_PROMPT_DAY, user_msg

    if context == "restaurants":
        restaurants = payload.get("restaurants", [])[:80]
        where = payload.get("port_label", "") or payload.get("port", "")
        compact = []
        for i, r in enumerate(restaurants):
            p = r.get("profile") or {}
            compact.append({
                "ref": i + 1,
                "name": r.get("name"),
                "port": r.get("port_key"),
                "cuisine": r.get("cuisine"),
                "price": r.get("price"),
                "rating": r.get("rating"),
                "reviews": r.get("reviews"),
                "michelin": r.get("michelin_award") or r.get("michelin"),
                "veg_options": r.get("veg_options"),
                "fully_veg": r.get("fully_veg"),
                "descriptor": p.get("descriptor"),
                "formality": p.get("formality"),
                "reservation": p.get("reservation"),
                "best_dishes": p.get("best_dishes"),
                "near": r.get("nearest_landmark"),
                "mi": r.get("nearest_landmark_mi"),
            })
        user_msg = (
            f"Where: {where or 'any port'}\n\n"
            f"Request: {question}\n\n"
            f"Restaurants JSON:\n{json.dumps(compact)}"
        )
        return SYSTEM_PROMPT_RESTAURANTS, user_msg

    if context == "hotels":
        hotels = payload.get("hotels", [])[:40]
        city = payload.get("city", "?")
        check_in = payload.get("check_in", "")
        check_out = payload.get("check_out", "")
        compact = []
        for h in hotels:
            compact.append({
                "name": h.get("name"),
                "brand": h.get("brand") or "independent",
                "stars": h.get("star_class"),
                "rating": h.get("overall_rating"),
                "reviews": h.get("reviews"),
                "rate_per_night": h.get("rate_per_night"),
                "total_rate": h.get("total_rate"),
                "nights": h.get("nights"),
                "distance_mi": h.get("distance_mi"),
                "landmark": h.get("landmark_name"),
                "cc_programs": h.get("cc_programs") or [],
            })
        user_msg = (
            f"City: {city}\nCheck-in: {check_in}\nCheck-out: {check_out}\n\n"
            f"Question: {question}\n\n"
            f"Hotels JSON:\n{json.dumps(compact)}"
        )
        return SYSTEM_PROMPT_HOTELS, user_msg

    flights = payload.get("flights", [])[:40]
    origin = payload.get("origin", "?")
    direction = payload.get("direction", "?")
    active_date = payload.get("active_date", "")
    compact = []
    for f in flights:
        row = {
            "airline": f.get("primary_airline"),
            "date": f.get("search_date"),
            "dep": (f.get("departure_time") or "")[:16],
            "arr": (f.get("arrival_time") or "")[:16],
            "stops": f.get("stops"),
            "price": f.get("price"),
        }
        if f.get("type") == "multi_city":
            row["mc_return"] = {
                "airline": f.get("return_airline"),
                "date": f.get("return_date"),
                "stops": f.get("return_stops"),
            }
        compact.append(row)
    user_msg = (
        f"Origin: {origin}\nDirection: {direction}\n"
        f"Active date filter: {active_date or 'none'}\n\n"
        f"Question: {question}\n\n"
        f"Flights JSON:\n{json.dumps(compact)}"
    )
    return SYSTEM_PROMPT_FLIGHTS, user_msg


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            if not OPENAI_API_KEY:
                return self._respond(500, {"error": "OPENAI_API_KEY not configured"})

            ip = self.headers.get("x-forwarded-for", "anon").split(",")[0].strip()
            if not _check_rate_limit(ip):
                return self._respond(429, {
                    "error": "Too many questions. Wait a few minutes and try again."
                })
            if not _check_daily_cap():
                return self._respond(429, {
                    "error": "Daily question limit reached. Try again tomorrow."
                })

            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            payload = json.loads(body)

            context = payload.get("context", "flights")
            is_restaurants = context == "restaurants"
            is_day = context == "day_summary"

            question = (payload.get("question") or "").strip()
            if not is_day:  # day_summary is fact-driven, no free-text question
                if not question:
                    return self._respond(400, {"error": "Missing question"})
                if len(question) > 500:
                    return self._respond(400, {"error": "Question too long (500 char max)"})

            system_prompt, user_msg = build_messages(question, payload)

            body = {
                "model": MODEL,
                "max_tokens": 320 if (is_restaurants or is_day) else 180,
                "temperature": 0.4 if is_day else 0.3,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_msg},
                ],
            }
            if is_restaurants or is_day:
                body["response_format"] = {"type": "json_object"}

            resp = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=15,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"].strip()

            if is_day:
                try:
                    parsed = json.loads(content)
                    return self._respond(200, {"summary": {
                        "breakfast": (parsed.get("breakfast") or "").strip(),
                        "lunch": (parsed.get("lunch") or "").strip(),
                        "dinner": (parsed.get("dinner") or "").strip(),
                    }})
                except (ValueError, TypeError):
                    return self._respond(200, {"summary": {}})

            if is_restaurants:
                # Map the model's short refs (1-based) back to real restaurant ids.
                slice_ = payload.get("restaurants", [])[:80]
                ref_to_id = {i + 1: r.get("id") for i, r in enumerate(slice_)}
                try:
                    parsed = json.loads(content)
                    picks = []
                    for ref in (parsed.get("picks") or [])[:5]:
                        try:
                            rid = ref_to_id.get(int(ref))
                        except (ValueError, TypeError):
                            rid = None
                        if rid and rid not in picks:
                            picks.append(rid)
                    return self._respond(200, {
                        "answer": (parsed.get("answer") or "").strip(),
                        "picks": picks,
                    })
                except (ValueError, TypeError):
                    return self._respond(200, {"answer": content, "picks": []})

            return self._respond(200, {"answer": content})

        except requests.HTTPError as exc:
            return self._respond(502, {
                "error": f"AI service error: {exc.response.status_code if exc.response else '?'}"
            })
        except Exception as exc:
            return self._respond(500, {"error": str(exc)[:200]})

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _respond(self, status: int, body: dict):
        out = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(out)

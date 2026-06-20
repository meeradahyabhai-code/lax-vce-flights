#!/usr/bin/env python3
"""Structured 'how do I choose' profile for each restaurant, from real reviews.

For the card expand: a short vibe descriptor ("beachy chic", "traditional
taverna"), formality (casual/fine dining), whether you need a reservation, the
dishes people actually rave about, and a vegetarian note. Popularity is computed
from rating x review count (not AI). Grounded ONLY in the review material — the
model is told not to invent. Also fills the prose `vibe` line for any restaurant
that doesn't already have a hand-written one (e.g. the new Michelin entries).

Needs a working OPENAI_API_KEY (run scripts/check_keys.py first).
  python3 scripts/enrich_profiles.py [--limit N] [--only-missing]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import hotel_agent  # noqa: F401,E402  load_dotenv

ROOT = Path(__file__).resolve().parent.parent
PATHS = [ROOT / "data" / "restaurants.json", ROOT / "public" / "restaurants.json",
         ROOT / "web" / "restaurants.json"]
MODEL = "gpt-4o-mini"
TIMEOUT = 30

SYS = (
    "You profile a restaurant for a refined family travel app, using ONLY the provided "
    "reviews and summary. Never invent. Return STRICT JSON with keys:\n"
    '  "descriptor": 2-4 word vibe tag, lowercase unless proper noun (e.g. "beachy chic", '
    '"traditional taverna", "rooftop fine dining", "lively meze spot")\n'
    '  "formality": one of "Casual","Smart casual","Fine dining"\n'
    '  "reservation": one of "Walk-in friendly","Reservation recommended","Reservation required"\n'
    '  "best_dishes": array of up to 3 specific dishes reviewers praise (lowercase), [] if none named\n'
    '  "veg_note": short phrase on vegetarian options (e.g. "good vegetarian options", '
    '"limited for vegetarians", "fully vegetarian"); "" if unknown\n'
    '  "cuisine": the primary CUISINE in 1-2 words (e.g. "Greek", "Seafood", "Italian", '
    '"Turkish", "Indian", "Mediterranean", "Steakhouse", "Cafe"). NEVER a price or formality '
    'level like "fine dining" or "casual" — that is the formality field, not cuisine.\n'
    '  "vibe": one warm, quiet, concierge-voice sentence (max 22 words, no emoji/exclamation)\n'
    "Ground every field in the material."
)


def popularity(rating: float, reviews: int) -> str:
    if reviews >= 3000:
        return "Iconic"
    if reviews >= 1000:
        return "Very popular"
    if reviews >= 300:
        return "Well-loved"
    return "Under the radar" if rating and rating >= 4.4 else "Quieter spot"


def enrich(r: dict, key: str, keep_vibe: bool) -> bool:
    material = (r.get("editorial", "") + "\n" + "\n".join(r.get("quotes", []))).strip()
    extra = f"Cuisine: {r.get('cuisine')}. Price: {r.get('price') or 'n/a'}. " \
            f"Michelin: {r.get('michelin_award') or r.get('michelin') or 'none'}."
    try:
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": MODEL, "temperature": 0.5, "max_tokens": 220,
                  "response_format": {"type": "json_object"},
                  "messages": [{"role": "system", "content": SYS},
                               {"role": "user", "content": f"{r['name']}. {extra}\nMaterial:\n{material or '(none)'}"}]},
            timeout=TIMEOUT)
        resp.raise_for_status()
        data = json.loads(resp.json()["choices"][0]["message"]["content"])
    except Exception as e:  # noqa: BLE001
        print(f"  ! {r['name'][:30]}: {e}", file=sys.stderr)
        return False
    ai_cuisine = (data.get("cuisine") or "").strip()
    if ai_cuisine:
        r["cuisine"] = ai_cuisine  # real cuisine, replacing Google's messy type/level
    r["profile"] = {
        "descriptor": (data.get("descriptor") or "").strip(),
        "formality": data.get("formality") or "",
        "reservation": data.get("reservation") or "",
        "best_dishes": [d for d in (data.get("best_dishes") or []) if d][:3],
        "veg_note": (data.get("veg_note") or "").strip(),
        "popularity": popularity(r.get("rating") or 0, r.get("reviews") or 0),
    }
    if not (keep_vibe and r.get("vibe")):
        r["vibe"] = (data.get("vibe") or r.get("vibe") or "").strip().strip('"')
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only-missing", action="store_true", help="only rows without a profile")
    args = ap.parse_args()
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        print("ERROR: OPENAI_API_KEY missing", file=sys.stderr)
        return 1

    cat = json.loads(PATHS[0].read_text())
    rows = cat["restaurants"]
    todo = [r for r in rows if not (args.only_missing and r.get("profile"))]
    if args.limit:
        todo = todo[:args.limit]
    print(f"enriching {len(todo)} restaurants...")
    done = 0
    for r in todo:
        # preserve hand-written vibe for the original Google picks; let AI fill Michelin ones
        keep_vibe = "michelin" not in (r.get("source_tags") or [])
        if enrich(r, key, keep_vibe):
            done += 1
            if done % 20 == 0:
                print(f"  {done}/{len(todo)}")
        time.sleep(0.15)
    for p in PATHS:
        p.write_text(json.dumps(cat, ensure_ascii=False, indent=2))
    print(f"profiled {done}/{len(todo)}; "
          f"total with profile: {sum(1 for r in rows if r.get('profile'))}/{len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

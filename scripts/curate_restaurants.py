#!/usr/bin/env python3
"""Curate a per-port restaurant catalog for the cruise.

FREE: uses Google Places API (New) Text Search + Place geocoding (within the
free tier per CLAUDE.md) and an optional OpenAI "vibe" pass (<$10/mo band).
No SerpAPI. Output -> data/restaurants.json (+ public/ + web/ copies).

Each restaurant carries the fields the card/finder need:
  name, cuisine, price, rating, reviews, veg_options, fully_veg, michelin,
  nearest_landmark(+mi), walk_min_port, address, lat/lng, photo, vibe,
  quotes[], website, phone, maps_url, reservation_link, booking_channel,
  booking_url, instagram, tiktok_search_url, source_tags[].

Michelin is left null here; it's layered in from the (free, offline) Kaggle
dataset in a later pass. Run with --no-vibe to skip the OpenAI step while
validating the Places data.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import urllib.parse
from pathlib import Path

import requests

# Reuse the hardened key handling + URLs + distance helper from hotel_agent.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hotel_agent import _places_key, PLACES_SEARCH_URL, _haversine  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT_PATHS = [ROOT / "data" / "restaurants.json",
             ROOT / "public" / "restaurants.json",
             ROOT / "web" / "restaurants.json"]

MAX_PER_PORT = 12
REQ_TIMEOUT = 20

# Restaurant field mask (Places API New). Includes the real veg/booking/price
# signals the card uses.
SEARCH_FIELDS = ",".join([
    "places.id", "places.displayName", "places.rating", "places.userRatingCount",
    "places.formattedAddress", "places.priceLevel", "places.location",
    "places.primaryTypeDisplayName", "places.types", "places.editorialSummary",
    "places.reviews", "places.websiteUri", "places.nationalPhoneNumber",
    "places.googleMapsUri", "places.photos", "places.reservable",
    "places.servesVegetarianFood",
])

# Per-port dining area to search + famous landmarks to measure distance from.
# Landmarks are GEOCODED at runtime (no hand-typed coordinates), so distances
# are accurate. port_key matches the app's convention (day label first word,
# lowercased).
PORTS = [
    {"port_key": "venice", "label": "Venice (Ravenna), Italy",
     "query": "best restaurants in San Marco, Venice, Italy",
     "landmarks": ["Piazza San Marco, Venice", "Rialto Bridge, Venice"]},
    {"port_key": "ravenna", "label": "Ravenna, Italy",
     "query": "best restaurants in Ravenna, Italy",
     "landmarks": ["Basilica di San Vitale, Ravenna", "Piazza del Popolo, Ravenna",
                   "Mausoleo di Galla Placidia, Ravenna"]},
    {"port_key": "dubrovnik", "label": "Dubrovnik, Croatia",
     "query": "best restaurants in Dubrovnik Old Town, Croatia",
     "landmarks": ["Pile Gate, Dubrovnik", "Stradun, Dubrovnik", "Rector's Palace, Dubrovnik"]},
    {"port_key": "bar", "label": "Bar, Montenegro",
     "query": "best restaurants in Bar, Montenegro",
     "landmarks": ["Stari Bar, Montenegro", "Port of Bar, Montenegro", "King Nikola's Palace, Bar"]},
    {"port_key": "athens", "label": "Athens (Piraeus), Greece",
     "query": "best restaurants in Plaka, Athens, Greece",
     "landmarks": ["Acropolis, Athens", "Plaka, Athens", "Monastiraki Square, Athens"]},
    {"port_key": "kusadasi", "label": "Kusadasi, Turkey",
     "query": "best restaurants in Kusadasi, Turkey",
     "landmarks": ["Kusadasi Marina", "Kusadasi Port", "Pigeon Island, Kusadasi"]},
    {"port_key": "rhodes", "label": "Rhodes, Greece",
     "query": "best restaurants in Rhodes Old Town, Greece",
     "landmarks": ["Palace of the Grand Master, Rhodes", "Rhodes Old Town", "Mandraki Harbour, Rhodes"]},
    {"port_key": "santorini", "label": "Santorini, Greece",
     "query": "best restaurants in Santorini, Greece",
     "landmarks": ["Oia, Santorini", "Fira, Santorini", "Santorini Old Port"]},
    {"port_key": "istanbul", "label": "Istanbul, Turkey",
     "query": "best restaurants in Sultanahmet, Istanbul, Turkey",
     "landmarks": ["Hagia Sophia, Istanbul", "Blue Mosque, Istanbul", "Galata Tower, Istanbul"]},
]

PRICE_MAP = {
    "PRICE_LEVEL_INEXPENSIVE": "€",
    "PRICE_LEVEL_MODERATE": "€€",
    "PRICE_LEVEL_EXPENSIVE": "€€€",
    "PRICE_LEVEL_VERY_EXPENSIVE": "€€€€",
}

VEG_HINT = ("vegetarian", "vegan", "plant-based", "plant based")


def _post_search(query: str, fields: str, included_type: str | None = "restaurant",
                 max_results: int = 20) -> list[dict]:
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": _places_key(),
        "X-Goog-FieldMask": fields,
    }
    body = {"textQuery": query, "maxResultCount": max_results}
    if included_type:
        body["includedType"] = included_type
    try:
        r = requests.post(PLACES_SEARCH_URL, json=body, headers=headers, timeout=REQ_TIMEOUT)
        r.raise_for_status()
        return r.json().get("places", [])
    except Exception as e:  # noqa: BLE001
        print(f"  ! search failed for {query!r}: {e}", file=sys.stderr)
        return []


def geocode_landmark(name: str) -> dict | None:
    """Resolve a landmark name -> {name, lat, lng} via Places (free)."""
    places = _post_search(name, "places.displayName,places.location",
                          included_type=None, max_results=1)
    if not places:
        return None
    loc = places[0].get("location", {})
    if loc.get("latitude") is None:
        return None
    return {"name": name.split(",")[0], "lat": loc["latitude"], "lng": loc["longitude"]}


def _txt(node) -> str:
    if isinstance(node, dict):
        return node.get("text", "") or ""
    return str(node or "")


def _cuisine(p: dict) -> str:
    primary = _txt(p.get("primaryTypeDisplayName"))
    if primary:
        return primary
    for t in (p.get("types") or []):
        if t.endswith("_restaurant"):
            return t.replace("_restaurant", "").replace("_", " ").title()
    return "Restaurant"


def _booking_channel(website: str, phone: str, reservable: bool) -> tuple[str, str, str]:
    """Return (channel, booking_url, instagram). Detect IG-as-website."""
    ig = ""
    if website and "instagram.com" in website.lower():
        ig = website
        return "instagram", website, ig
    if website:
        return ("website", website, ig)
    if phone:
        return ("phone", f"tel:{phone.replace(' ', '')}", ig)
    return ("none", "", ig)


def _veg(p: dict) -> tuple[bool, bool]:
    """(has_veg_options, fully_vegetarian) — best effort from Google + text."""
    options = bool(p.get("servesVegetarianFood"))
    blob = (_txt(p.get("editorialSummary")) + " " + _txt(p.get("displayName"))).lower()
    fully = any(h in blob for h in VEG_HINT) and ("steak" not in blob and "seafood" not in blob)
    if fully:
        options = True
    return options, fully


def build_restaurant(p: dict, port: dict, landmarks: list[dict]) -> dict | None:
    name = _txt(p.get("displayName"))
    loc = p.get("location") or {}
    lat, lng = loc.get("latitude"), loc.get("longitude")
    if not name or lat is None:
        return None

    # nearest famous landmark
    nearest_name, nearest_mi = "", None
    for lm in landmarks:
        d_km = _haversine(lat, lng, lm["lat"], lm["lng"])
        d_mi = round(d_km * 0.621371, 1)
        if nearest_mi is None or d_mi < nearest_mi:
            nearest_mi, nearest_name = d_mi, lm["name"]

    website = p.get("websiteUri", "") or ""
    phone = p.get("nationalPhoneNumber", "") or ""
    channel, booking_url, instagram = _booking_channel(website, phone, bool(p.get("reservable")))
    veg_options, fully_veg = _veg(p)

    quotes = []
    for rev in (p.get("reviews") or [])[:3]:
        t = _txt(rev.get("text")).strip()
        if t:
            quotes.append(t[:200])

    return {
        "id": p.get("id", ""),
        "port": port["label"],
        "port_key": port["port_key"],
        "name": name,
        "cuisine": _cuisine(p),
        "price": PRICE_MAP.get(p.get("priceLevel", ""), ""),
        "rating": p.get("rating", 0) or 0,
        "reviews": p.get("userRatingCount", 0) or 0,
        "veg_options": veg_options,
        "fully_veg": fully_veg,
        "michelin": None,  # layered in from Kaggle later
        "nearest_landmark": nearest_name,
        "nearest_landmark_mi": nearest_mi,
        "walk_min_port": None,  # set in a later pass vs the cruise terminal
        "address": p.get("formattedAddress", ""),
        "lat": lat,
        "lng": lng,
        "photo": (p.get("photos") or [{}])[0].get("name", ""),  # Places photo resource
        "vibe": "",  # filled by the OpenAI pass
        "quotes": quotes,
        "website": website,
        "phone": phone,
        "maps_url": p.get("googleMapsUri", ""),
        "reservation_link": website if channel in ("website",) else "",
        "booking_channel": channel,
        "booking_url": booking_url,
        "instagram": instagram,
        "reservable": bool(p.get("reservable")),
        "tiktok_search_url": "https://www.tiktok.com/search?q=" +
            urllib.parse.quote(f"{name} {port['port_key']}"),
        "editorial": _txt(p.get("editorialSummary")),
        "source_tags": ["google"],
    }


def add_vibe(restaurants: list[dict]) -> None:
    """One short, on-voice 'what people say' line per restaurant, from real reviews."""
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        print("  (no OPENAI_API_KEY — skipping vibe pass)", file=sys.stderr)
        return
    sys_prompt = (
        "You write one warm, quietly luxurious sentence (max 22 words) capturing a "
        "restaurant's vibe and what to order, in the voice of a refined travel concierge "
        "(think Aman/Belmond). No emojis, no hype, no exclamation marks. Ground it ONLY in "
        "the provided reviews and summary; never invent specifics."
    )
    for r in restaurants:
        material = (r.get("editorial", "") + "\n" + "\n".join(r.get("quotes", []))).strip()
        if not material:
            continue
        try:
            resp = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={
                    "model": "gpt-4o-mini",
                    "temperature": 0.6,
                    "max_tokens": 60,
                    "messages": [
                        {"role": "system", "content": sys_prompt},
                        {"role": "user", "content": f"{r['name']} ({r['cuisine']}). Reviews:\n{material}"},
                    ],
                },
                timeout=REQ_TIMEOUT,
            )
            resp.raise_for_status()
            r["vibe"] = resp.json()["choices"][0]["message"]["content"].strip().strip('"')
            print(f"    vibe: {r['name']}")
        except Exception as e:  # noqa: BLE001
            print(f"    ! vibe failed for {r['name']}: {e}", file=sys.stderr)
        time.sleep(0.2)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-vibe", action="store_true", help="skip the OpenAI vibe pass")
    ap.add_argument("--port", help="only curate one port_key (debug)")
    ap.add_argument("--append", action="store_true",
                    help="merge into the existing catalog instead of overwriting")
    args = ap.parse_args()

    if not _places_key():
        print("ERROR: GOOGLE_PLACES_API_KEY not set", file=sys.stderr)
        return 1

    ports = [p for p in PORTS if (not args.port or p["port_key"] == args.port)]
    all_rows: list[dict] = []
    for port in ports:
        print(f"\n== {port['port_key']} ==")
        landmarks = [lm for lm in (geocode_landmark(n) for n in port["landmarks"]) if lm]
        print(f"  landmarks: {[lm['name'] for lm in landmarks]}")
        if not landmarks:
            print("  ! no landmarks geocoded — distances will be null", file=sys.stderr)
        places = _post_search(port["query"], SEARCH_FIELDS)
        # rank by a simple quality score: rating weighted by log(review count)
        places.sort(key=lambda p: (p.get("rating", 0) or 0) *
                    math.log10((p.get("userRatingCount", 0) or 0) + 10), reverse=True)
        rows = []
        for p in places:
            r = build_restaurant(p, port, landmarks)
            if r:
                rows.append(r)
            if len(rows) >= MAX_PER_PORT:
                break
        print(f"  restaurants: {len(rows)}")
        all_rows.extend(rows)

    if not args.no_vibe:
        print("\n== vibe pass ==")
        add_vibe(all_rows)

    if args.append and OUT_PATHS[0].exists():
        existing = json.loads(OUT_PATHS[0].read_text()).get("restaurants", [])
        have = {r["id"] for r in existing}
        merged = existing + [r for r in all_rows if r["id"] not in have]
        print(f"append: {len(existing)} existing + {len(merged) - len(existing)} new = {len(merged)}")
        all_rows = merged

    payload = {"count": len(all_rows), "restaurants": all_rows}
    for path in OUT_PATHS:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        tmp.replace(path)
    print(f"\nWrote {len(all_rows)} restaurants to {len(OUT_PATHS)} files.")
    # quick coverage report
    from collections import Counter
    by_port = Counter(r["port_key"] for r in all_rows)
    for k, v in by_port.items():
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

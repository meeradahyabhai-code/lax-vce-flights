"""Dynamic, distance-aware restaurant search (the hybrid's live layer).

The curated catalog (data/restaurants.json) stays the instant, hand-enriched base
with vibe/why/Michelin. THIS fetches more spots live from Google Places within a
radius of a port anchor — the same idea as the hotels flow, but Places-only (free,
no SerpAPI). Results carry a real distance from the anchor and a Places photo ref
(served via the photo proxy, so nothing is downloaded).

Pure logic, no HTTP/caching here — api/restaurants.py wraps it. Indian is always
pulled exhaustively (it skips the rating floor, family requirement); general spots
keep the >=4.0 rating AND >=100 reviews bar. Everything is hard-capped to the radius.
"""
from __future__ import annotations

import concurrent.futures
import json
import urllib.parse

from hotel_agent import _places_key, _haversine, PLACES_SEARCH_URL
import requests

MI_TO_M = 1609.34
REQ_TIMEOUT = 15
MAX_PAGES = 3                  # Places caps text search at 20/page (60 total)
GENERAL_MIN_RATING = 4.0
GENERAL_MIN_REVIEWS = 100
INDIAN_MIN_REVIEWS = 20        # Indian skips the rating floor, but a 2-review listing is noise
INDIAN_NEEDLES = ("indian", "biryani", "tandoor", "curry", "punjab", "masala",
                  "bombay", "mumbai", "delhi", "halal")

# Tourist/center anchor per port — distances are measured from here and labelled on
# the card, matching the curated cards' "X mi from <landmark>" convention.
ANCHORS = {
    "venice":    ("Venice (Ravenna), Italy", "Piazza San Marco, Venice, Italy"),
    "ravenna":   ("Ravenna, Italy",          "Piazza del Popolo, Ravenna, Italy"),
    "dubrovnik": ("Dubrovnik, Croatia",      "Pile Gate, Dubrovnik, Croatia"),
    "kotor":     ("Kotor, Montenegro",       "Kotor Old Town, Montenegro"),
    "athens":    ("Athens (Piraeus), Greece","Acropolis, Athens, Greece"),
    "kusadasi":  ("Kusadasi, Turkey",        "Kusadasi Cruise Port, Turkey"),
    "rhodes":    ("Rhodes, Greece",          "Rhodes Old Town, Greece"),
    "santorini": ("Santorini, Greece",       "Fira, Santorini, Greece"),
    "istanbul":  ("Istanbul, Turkey",        "Hagia Sophia, Istanbul, Turkey"),
}

PHOTO_FIELDS = ",".join([
    "places.id", "places.displayName", "places.rating", "places.userRatingCount",
    "places.formattedAddress", "places.priceLevel", "places.location",
    "places.primaryTypeDisplayName", "places.types", "places.editorialSummary",
    "places.reviews", "places.websiteUri", "places.nationalPhoneNumber",
    "places.googleMapsUri", "places.photos", "places.reservable",
    "places.servesVegetarianFood",
])

PRICE_MAP = {
    "PRICE_LEVEL_INEXPENSIVE": "€", "PRICE_LEVEL_MODERATE": "€€",
    "PRICE_LEVEL_EXPENSIVE": "€€€", "PRICE_LEVEL_VERY_EXPENSIVE": "€€€€",
}
VEG_HINT = ("vegetarian", "vegan", "plant-based", "plant based")


def _txt(node) -> str:
    if isinstance(node, dict):
        return node.get("text", "") or ""
    return node or ""


def _cuisine(p: dict) -> str:
    primary = _txt(p.get("primaryTypeDisplayName"))
    if primary:
        return primary.replace(" Restaurant", "").strip() or "Restaurant"
    for t in (p.get("types") or []):
        if t.endswith("_restaurant"):
            return t.replace("_restaurant", "").replace("_", " ").title()
    return "Restaurant"


def _veg(p: dict) -> tuple[bool, bool]:
    if p.get("servesVegetarianFood"):
        blob = (_txt(p.get("editorialSummary")) + " " +
                " ".join(_txt(r.get("text")) for r in (p.get("reviews") or []))).lower()
        fully = any(h in blob for h in ("fully vegetarian", "purely vegetarian", "100% veg", "vegetarian restaurant"))
        return True, fully
    return False, False


def _booking(website: str, phone: str) -> tuple[str, str]:
    if website:
        return "website", website
    if phone:
        return "phone", "tel:" + phone.replace(" ", "")
    return "none", ""


def _geocode(query: str, key: str) -> dict | None:
    """Resolve an anchor name -> {lat, lng, name} (free Places call)."""
    try:
        r = requests.post(PLACES_SEARCH_URL,
                          json={"textQuery": query, "maxResultCount": 1},
                          headers={"Content-Type": "application/json", "X-Goog-Api-Key": key,
                                   "X-Goog-FieldMask": "places.displayName,places.location"},
                          timeout=REQ_TIMEOUT)
        r.raise_for_status()
        places = r.json().get("places", [])
        if not places:
            return None
        loc = places[0]["location"]
        return {"lat": loc["latitude"], "lng": loc["longitude"],
                "name": _txt(places[0].get("displayName")) or query.split(",")[0]}
    except Exception:  # noqa: BLE001
        return None


def _search_circle(query: str, center: dict, radius_m: float, key: str, sleep=None,
                   max_pages: int = MAX_PAGES) -> list[dict]:
    """searchText biased to a circle, following nextPageToken up to max_pages.

    Pagination costs a ~2s wait per extra page, so callers single-page the broad
    'general' angles (one page is plenty after the radius cap + dedupe) and only
    paginate the Indian sweep, where catching every spot is the requirement.
    """
    headers = {"Content-Type": "application/json", "X-Goog-Api-Key": key,
               "X-Goog-FieldMask": PHOTO_FIELDS + ",nextPageToken"}
    out, token, pages = [], None, 0
    while pages < max_pages:
        body = {"textQuery": query, "maxResultCount": 20,
                "locationBias": {"circle": {
                    "center": {"latitude": center["lat"], "longitude": center["lng"]},
                    "radius": min(radius_m, 50000.0)}}}
        if token:
            body["pageToken"] = token
        try:
            r = requests.post(PLACES_SEARCH_URL, json=body, headers=headers, timeout=REQ_TIMEOUT)
            r.raise_for_status()
            data = r.json()
        except Exception:  # noqa: BLE001
            break
        out.extend(data.get("places", []))
        token = data.get("nextPageToken")
        pages += 1
        if not token:
            break
        if sleep:
            sleep(2.2)  # page token needs a moment to validate
    return out


def normalize(p: dict, port_key: str, port_label: str, center: dict) -> dict | None:
    """Google Places result -> client card dict (dynamic, no AI profile/vibe)."""
    name = _txt(p.get("displayName"))
    loc = p.get("location") or {}
    lat, lng = loc.get("latitude"), loc.get("longitude")
    if not name or lat is None:
        return None
    dist_mi = round(_haversine(lat, lng, center["lat"], center["lng"]) * 0.621371, 1)
    website = p.get("websiteUri") or ""
    phone = p.get("nationalPhoneNumber") or ""
    channel, booking_url = _booking(website, phone)
    veg_options, fully_veg = _veg(p)
    quotes = [t[:200] for t in (_txt(r.get("text")).strip() for r in (p.get("reviews") or [])[:3]) if t]
    return {
        "id": p.get("id", ""),
        "port": port_label,
        "port_key": port_key,
        "name": name,
        "cuisine": _cuisine(p),
        "price": PRICE_MAP.get(p.get("priceLevel", ""), ""),
        "rating": p.get("rating", 0) or 0,
        "reviews": p.get("userRatingCount", 0) or 0,
        "veg_options": veg_options,
        "fully_veg": fully_veg,
        "michelin": None,
        "nearest_landmark": center["name"],
        "nearest_landmark_mi": dist_mi,
        "distance_mi": dist_mi,
        "address": p.get("formattedAddress", ""),
        "lat": lat, "lng": lng,
        "photo_ref": (p.get("photos") or [{}])[0].get("name", ""),  # served via photo proxy
        "photo_local": "",
        "vibe": "",
        "profile": None,
        "quotes": quotes,
        "editorial": _txt(p.get("editorialSummary")),
        "website": website,
        "phone": phone,
        "maps_url": p.get("googleMapsUri", ""),
        "booking_channel": channel,
        "booking_url": booking_url,
        "reservation_link": website if channel == "website" else "",
        "tiktok_search_url": "https://www.tiktok.com/search?q=" +
            urllib.parse.quote(f"{name} {port_key}"),
        "source_tags": ["google", "dynamic"],
        "dynamic": True,
    }


# ---------------------------------------------------------------- enrichment ----
# Give the live "wider area" results the same concierge writeup the curated catalog
# has (vibe sentence + profile), grounded in the real Google review material we
# already pulled. Batched + parallel so it stays fast; the endpoint caches 30 days.
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
ENRICH_MODEL = "gpt-4o-mini"
ENRICH_SYS = (
    "You profile restaurants for a refined family travel app, using ONLY the provided "
    "reviews/summary for each. Never invent. You are given a JSON array under 'items'; "
    "for EACH item return an object with the SAME integer 'ref', plus:\n"
    '  "descriptor": 2-4 word vibe tag (e.g. "canalside trattoria", "lively meze spot")\n'
    '  "formality": one of "Casual","Smart casual","Fine dining"\n'
    '  "reservation": one of "Walk-in friendly","Reservation recommended","Reservation required"\n'
    '  "best_dishes": up to 3 specific dishes reviewers praise (lowercase), [] if none named\n'
    '  "veg_note": short phrase on vegetarian options; "" if unknown\n'
    '  "vibe": one warm, quiet, concierge-voice sentence (max 22 words, no emoji/exclamation)\n'
    "Voice: specific and grounded, never marketing. Avoid empty praise words like "
    "'delicious', 'amazing', 'fantastic', 'must-visit' — describe what the place actually "
    "is (setting, dish, who it suits). If the material is thin, stay plain and factual.\n"
    'Return JSON: {"results": [ ... ]}. Ground every field in the material; keep it honest if thin.'
)


def _popularity(rating, reviews) -> str:
    reviews = reviews or 0
    if reviews >= 3000:
        return "Iconic"
    if reviews >= 1000:
        return "Very popular"
    if reviews >= 300:
        return "Well-loved"
    return "Under the radar" if (rating or 0) >= 4.4 else "Quieter spot"


def _enrich_batch(batch: list[dict], openai_key: str) -> None:
    items = [{"ref": i, "name": r["name"], "cuisine": r.get("cuisine"),
              "price": r.get("price") or "", "rating": r.get("rating"), "reviews": r.get("reviews"),
              "material": ((r.get("editorial", "") + "\n" + "\n".join(r.get("quotes", []))).strip() or "(none)")[:1500]}
             for i, r in enumerate(batch)]
    try:
        resp = requests.post(
            OPENAI_URL,
            headers={"Authorization": f"Bearer {openai_key}", "Content-Type": "application/json"},
            json={"model": ENRICH_MODEL, "temperature": 0.5, "max_tokens": 1500,
                  "response_format": {"type": "json_object"},
                  "messages": [{"role": "system", "content": ENRICH_SYS},
                               {"role": "user", "content": json.dumps({"items": items})}]},
            timeout=30)
        resp.raise_for_status()
        data = json.loads(resp.json()["choices"][0]["message"]["content"])
    except Exception:  # noqa: BLE001
        return  # leave these rows un-enriched rather than fail the whole search
    by_ref = {}
    for x in (data.get("results") or []):
        try:
            by_ref[int(x.get("ref"))] = x
        except (ValueError, TypeError):
            pass
    for i, r in enumerate(batch):
        d = by_ref.get(i)
        if not d:
            continue
        r["profile"] = {
            "descriptor": (d.get("descriptor") or "").strip(),
            "formality": d.get("formality") or "",
            "reservation": d.get("reservation") or "",
            "best_dishes": [s for s in (d.get("best_dishes") or []) if s][:3],
            "veg_note": (d.get("veg_note") or "").strip(),
            "popularity": _popularity(r.get("rating"), r.get("reviews")),
        }
        v = (d.get("vibe") or "").strip().strip('"')
        if v:
            r["vibe"] = v


def enrich_rows(rows: list[dict], openai_key: str | None, batch_size: int = 6,
                max_workers: int = 16) -> list[dict]:
    """Fill profile + vibe on dynamic rows via batched, parallel OpenAI calls."""
    if not openai_key or not rows:
        return rows
    batches = [rows[i:i + batch_size] for i in range(0, len(rows), batch_size)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        list(ex.map(lambda b: _enrich_batch(b, openai_key), batches))
    return rows


def search_area(port_key: str, radius_mi: float = 10.0, key: str | None = None,
                sleep=None) -> dict:
    """Find restaurants within radius_mi of the port anchor.

    Returns {port_key, anchor, radius_mi, restaurants:[...]} sorted by distance.
    Indian is exhaustive (no rating floor); general keeps the quality bar; both capped
    to the radius and deduped by place id and by name.
    """
    key = key or _places_key()
    label, anchor_q = ANCHORS.get(port_key, (port_key.title(), port_key))
    center = _geocode(anchor_q, key)
    if not center:
        return {"port_key": port_key, "anchor": anchor_q, "radius_mi": radius_mi,
                "restaurants": [], "error": "anchor geocode failed"}
    radius_m = radius_mi * MI_TO_M

    seen_ids, seen_names, results = set(), set(), []

    def _add(p, *, indian):
        rid = p.get("id")
        nm = _txt(p.get("displayName")).lower()
        if not rid or rid in seen_ids or (port_key, nm) in seen_names:
            return
        row = normalize(p, port_key, label, center)
        if not row:
            return
        if row["distance_mi"] > radius_mi:
            return
        if indian:
            if (p.get("userRatingCount", 0) or 0) < INDIAN_MIN_REVIEWS:
                return
            row["cuisine"] = "Indian"
            row["source_tags"] = sorted(set(row["source_tags"] + ["cuisine:indian"]))
        else:
            if not ((p.get("rating", 0) or 0) >= GENERAL_MIN_RATING
                    and (p.get("userRatingCount", 0) or 0) >= GENERAL_MIN_REVIEWS):
                return
        seen_ids.add(rid); seen_names.add((port_key, nm))
        results.append(row)

    # Indian — exhaustive, no rating floor (paginate to catch every spot)
    for p in _search_circle("Indian restaurant in " + label, center, radius_m, key, sleep,
                            max_pages=MAX_PAGES):
        blob = (_txt(p.get("displayName")) + " " + _txt(p.get("primaryTypeDisplayName")) + " "
                + " ".join(p.get("types") or []) + " " + _txt(p.get("editorialSummary"))).lower()
        if any(n in blob for n in INDIAN_NEEDLES):
            _add(p, indian=True)

    # General — keep the quality bar (single page per angle: fast, plenty after dedupe)
    for ang in ("best restaurants", "seafood restaurants", "casual local restaurants",
                "fine dining", "popular restaurants"):
        for p in _search_circle(ang + " in " + label, center, radius_m, key, sleep, max_pages=1):
            _add(p, indian=False)

    results.sort(key=lambda r: (r["distance_mi"], -float(r["rating"] or 0)))
    return {"port_key": port_key, "anchor": center["name"], "radius_mi": radius_mi,
            "restaurants": results}

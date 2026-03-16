"""
Hotel search module: Venice hotels — combined SerpAPI + Google Places API.

SerpAPI Google Hotels → pricing ($/night, total), star class, photos, booking links
Google Places API     → user ratings (1-5), review text, editorial summary, loyalty links
"""

import math
import os
import sys

from dotenv import load_dotenv

load_dotenv()

import requests

SERPAPI_KEY = os.environ.get("SERPAPI_KEY", "")

# ---------------------------------------------------------------------------
# Landmarks — reference points for distance calculation
# ---------------------------------------------------------------------------

LANDMARKS = {
    "venice": {"name": "Piazza San Marco", "lat": 45.4341, "lng": 12.3388},
    "istanbul": {"name": "Galata Tower", "lat": 41.0256, "lng": 28.9741},
}


def _haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Return distance in km between two lat/lng points."""
    R = 6371  # Earth radius in km
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlng / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ---------------------------------------------------------------------------
# Credit card program data (one-time snapshot, Mar 2026)
# ---------------------------------------------------------------------------

AMEX_FHR_VENICE = {
    "aman venice", "baglioni hotel luna", "hotel cipriani, a belmond hotel, venice",
    "ca' di dio", "hotel danieli", "the gritti palace, a luxury collection hotel, venice",
    "jw marriott venice resort & spa", "londra palace venezia", "nolinski venezia",
    "palazzo venart luxury hotel", "the st. regis venice", "the venice venice hotel",
    "violino d'oro venezia",
}

AMEX_THC_VENICE = {
    "nh collection venice grand hotel palazzo dei dogi",
    "palazzo nani venice, a radisson collection hotel",
    "metropole hotel, venice", "hilton molino stucky venice",
    "sina centurion palace", "il palazzo experimental",
    "nh collection murano villa",
}

CC_PROGRAMS = {
    "venice": {"fhr": AMEX_FHR_VENICE, "thc": AMEX_THC_VENICE},
}


def _match_cc_program(hotel_name: str, city_key: str) -> list[str]:
    """Return list of CC program tags for a hotel (e.g. ['fhr'], ['thc'], [])."""
    programs = CC_PROGRAMS.get(city_key, {})
    name_lower = (hotel_name or "").lower().strip()
    matched = []
    for prog_key, hotel_set in programs.items():
        for known in hotel_set:
            # Fuzzy: check if either contains the other, or significant word overlap
            if known in name_lower or name_lower in known:
                matched.append(prog_key)
                break
            # Word overlap check
            known_words = set(known.split()) - {"hotel", "the", "a", ",", "di", "venice", "venezia"}
            name_words = set(name_lower.split()) - {"hotel", "the", "a", ",", "di", "venice", "venezia"}
            if known_words and name_words and len(known_words & name_words) >= 2:
                matched.append(prog_key)
                break
    return matched
def _places_key():
    return os.environ.get("GOOGLE_PLACES_API_KEY", "").strip()

PLACES_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
PLACES_DETAILS_URL = "https://places.googleapis.com/v1/places"

# Share the SerpAPI call log with flight_agent
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from flight_agent import _serpapi_call_log
except ImportError:
    _serpapi_call_log = []


# ---------------------------------------------------------------------------
# Brand detection
# ---------------------------------------------------------------------------

MARRIOTT_BRANDS = [
    "marriott", "sheraton", "westin", "w hotel", "ritz-carlton",
    "st. regis", "courtyard", "jw marriott", "autograph", "tribute",
    "le meridien", "le méridien", "renaissance", "aloft", "moxy",
    "four points", "fairfield",
]

HILTON_BRANDS = [
    "hilton", "doubletree", "hampton", "embassy suites",
    "waldorf astoria", "conrad", "canopy", "curio", "tapestry",
    "motto", "lxr",
]


def detect_brand(hotel_name: str) -> str:
    """Return 'marriott', 'hilton', or 'independent'."""
    name_lower = (hotel_name or "").lower()
    for brand in MARRIOTT_BRANDS:
        if brand in name_lower:
            return "marriott"
    for brand in HILTON_BRANDS:
        if brand in name_lower:
            return "hilton"
    return "independent"


# ---------------------------------------------------------------------------
# SerpAPI Google Hotels — pricing, star class, photos, booking links
# ---------------------------------------------------------------------------

def search_hotels_serpapi(city: str, check_in: str, check_out: str, adults: int = 2) -> dict:
    """Search Google Hotels via SerpAPI. Filters to 4-5 star, 4.0+ rated."""
    params = {
        "engine": "google_hotels",
        "q": f"{city} hotels",
        "check_in_date": check_in,
        "check_out_date": check_out,
        "adults": str(adults),
        "currency": "USD",
        "gl": "us",
        "hl": "en",
        "hotel_class": "4,5",
        "rating": "8",
        "api_key": SERPAPI_KEY,
    }

    _serpapi_call_log.append({
        "route": f"hotels:{city}",
        "check_in": check_in,
        "check_out": check_out,
    })

    resp = requests.get("https://serpapi.com/search", params=params, timeout=25)
    resp.raise_for_status()
    return resp.json()


def _parse_star_class(raw) -> int:
    """Parse hotel_class which may be int, '5', or '4-star hotel'."""
    if not raw:
        return 0
    if isinstance(raw, (int, float)):
        return int(raw)
    s = str(raw)
    # Extract first digit from strings like "4-star hotel"
    for c in s:
        if c.isdigit():
            return int(c)
    return 0


def _parse_price(price_str: str) -> int:
    """Parse '$189' or '189' to integer."""
    if not price_str:
        return 0
    cleaned = "".join(c for c in str(price_str) if c.isdigit() or c == ".")
    if not cleaned:
        return 0
    try:
        return round(float(cleaned))
    except ValueError:
        return 0


def _extract_image(prop: dict) -> str:
    """Get first image URL from SerpAPI property."""
    images = prop.get("images", [])
    if isinstance(images, list) and images:
        img = images[0]
        if isinstance(img, dict):
            return img.get("thumbnail", "") or img.get("original_image", "")
        if isinstance(img, str):
            return img
    return prop.get("thumbnail", "") or ""


# ---------------------------------------------------------------------------
# Google Places API — user ratings, reviews, editorial summary
# ---------------------------------------------------------------------------

def search_places(city: str) -> list[dict]:
    """Search hotels via Google Places API (New) Text Search."""
    if not _places_key():
        return []

    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": _places_key(),
        "X-Goog-FieldMask": (
            "places.displayName,places.rating,places.userRatingCount,"
            "places.id,places.formattedAddress,places.priceLevel,"
            "places.editorialSummary,places.reviews,places.websiteUri,"
            "places.googleMapsUri,places.location"
        ),
    }
    body = {
        "textQuery": f"best hotels in {city}",
        "includedType": "lodging",
        "maxResultCount": 20,
    }

    try:
        resp = requests.post(PLACES_SEARCH_URL, json=body, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return []

    places = data.get("places", [])
    # Normalize to a common format for merge
    results = []
    for p in places:
        dn = p.get("displayName", {})
        name = dn.get("text", "") if isinstance(dn, dict) else str(dn)
        es = p.get("editorialSummary", {})
        editorial = es.get("text", "") if isinstance(es, dict) else ""

        # Convert reviews from new format
        review_snippets = []
        for rev in (p.get("reviews", []) or [])[:3]:
            author = rev.get("authorAttribution", {})
            rev_text = rev.get("text", {})
            review_snippets.append({
                "author": author.get("displayName", "") if isinstance(author, dict) else "",
                "rating": rev.get("rating", 0),
                "text": (rev_text.get("text", "") if isinstance(rev_text, dict) else str(rev_text or ""))[:200],
                "time": rev.get("relativePublishTimeDescription", ""),
            })

        loc = p.get("location", {})
        results.append({
            "name": name,
            "rating": p.get("rating", 0) or 0,
            "user_ratings_total": p.get("userRatingCount", 0) or 0,
            "place_id": p.get("id", ""),
            "formatted_address": p.get("formattedAddress", ""),
            "editorial_summary": editorial,
            "review_snippets": review_snippets,
            "google_maps_url": p.get("googleMapsUri", ""),
            "website": p.get("websiteUri", ""),
            "latitude": loc.get("latitude"),
            "longitude": loc.get("longitude"),
        })

    return results


def get_place_details(place_id: str) -> dict:
    """Fetch reviews and details via Places API (New)."""
    if not _places_key() or not place_id:
        return {}

    headers = {
        "X-Goog-Api-Key": _places_key(),
        "X-Goog-FieldMask": "reviews,editorialSummary,websiteUri,googleMapsUri",
    }

    try:
        url = f"{PLACES_DETAILS_URL}/{place_id}"
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Normalize — merge SerpAPI + Places data
# ---------------------------------------------------------------------------

def _calc_nights(check_in: str, check_out: str) -> int:
    """Calculate number of nights between dates."""
    from datetime import date as dt_date
    ci = dt_date.fromisoformat(check_in)
    co = dt_date.fromisoformat(check_out)
    return max((co - ci).days, 1)


def _normalize_name(name: str) -> str:
    """Lowercase, strip common suffixes for fuzzy matching."""
    import re
    n = (name or "").lower().strip()
    n = re.sub(r"\s+", " ", n)
    # Remove trailing location qualifiers
    for suffix in [", a luxury collection hotel", ", autograph collection",
                   ", a tribute portfolio hotel"]:
        n = n.replace(suffix, "")
    return n


def normalize_serpapi(raw_response: dict, check_in: str, check_out: str) -> list[dict]:
    """Convert SerpAPI properties to hotel dicts (pricing, stars, photos, links)."""
    properties = raw_response.get("properties", [])
    nights = _calc_nights(check_in, check_out)
    hotels = []

    for prop in properties:
        name = prop.get("name", "")
        rate_info = prop.get("rate_per_night", {})
        total_info = prop.get("total_rate", {})

        rate_str = rate_info.get("lowest", "") if isinstance(rate_info, dict) else ""
        total_str = total_info.get("lowest", "") if isinstance(total_info, dict) else ""

        rate_per_night = _parse_price(rate_str)
        total_rate = _parse_price(total_str)

        if rate_per_night and not total_rate:
            total_rate = rate_per_night * nights
        if total_rate and not rate_per_night:
            rate_per_night = round(total_rate / nights)

        if not rate_per_night:
            continue

        gps = prop.get("gps_coordinates", {})
        hotel = {
            "name": name,
            "brand": detect_brand(name),
            "star_class": _parse_star_class(prop.get("hotel_class")),
            "rate_per_night": rate_per_night,
            "total_rate": total_rate,
            "nights": nights,
            "check_in": check_in,
            "check_out": check_out,
            "address": prop.get("description", ""),
            "image_url": _extract_image(prop),
            "amenities": prop.get("amenities", []) or [],
            "google_hotels_url": prop.get("link", ""),
            "latitude": gps.get("latitude"),
            "longitude": gps.get("longitude"),
            # Placeholders — filled by Places API merge
            "overall_rating": 0,
            "reviews": 0,
            "review_snippets": [],
            "editorial_summary": "",
            "google_maps_url": "",
            "website": "",
        }
        hotels.append(hotel)

    return hotels


def _build_places_lookup(places_results: list[dict]) -> dict:
    """Build a name→place dict for fuzzy matching with SerpAPI hotels."""
    lookup = {}
    for place in places_results:
        name = place.get("name", "")
        if name:
            lookup[_normalize_name(name)] = place
    return lookup


def _fuzzy_match_place(norm: str, lookup: dict) -> dict | None:
    """Find fuzzy match for a normalized hotel name in the places lookup."""
    if norm in lookup:
        return lookup[norm]

    STOP_WORDS = {"hotel", "the", "a", "di", "e", "in", "venice",
                  "venezia", "italy", "&", "-", "collection", "resort",
                  "spa", "boutique", "suites"}
    h_words = set(norm.split()) - STOP_WORDS
    best_match = None
    best_overlap = 0
    for pname, p in lookup.items():
        shorter = min(len(norm), len(pname))
        if shorter >= 8 and (norm in pname or pname in norm):
            return p
        p_words = set(pname.split()) - STOP_WORDS
        if not h_words or not p_words:
            continue
        overlap = len(h_words & p_words)
        min_words = min(len(h_words), len(p_words))
        if overlap >= 2 and overlap / min_words >= 0.5 and overlap > best_overlap:
            best_overlap = overlap
            best_match = p
    return best_match


def _apply_place_data(h: dict, place: dict) -> None:
    """Copy Places data into a hotel dict."""
    h["overall_rating"] = place.get("rating", 0) or 0
    h["reviews"] = place.get("user_ratings_total", 0) or 0
    h["place_id"] = place.get("place_id", "")
    if place.get("review_snippets"):
        h["review_snippets"] = place["review_snippets"]
    if place.get("editorial_summary"):
        h["editorial_summary"] = place["editorial_summary"]
    if place.get("google_maps_url"):
        h["google_maps_url"] = place["google_maps_url"]
    if place.get("website"):
        h["website"] = place["website"]
    places_addr = place.get("formatted_address", "")
    if places_addr and len(places_addr) > len(h.get("address", "")):
        h["address"] = places_addr
    # Fill coordinates from Places if missing
    if not h.get("latitude") and place.get("latitude"):
        h["latitude"] = place["latitude"]
        h["longitude"] = place["longitude"]


def merge_places_data(hotels: list[dict], places_results: list[dict]) -> list[dict]:
    """Enrich SerpAPI hotels with Google Places data, then add unmatched
    Places hotels (incl. Marriott/Hilton) so they appear in results too.
    """
    lookup = _build_places_lookup(places_results)
    matched_place_names = set()

    # Pass 1: enrich SerpAPI hotels with Places data
    for h in hotels:
        norm = _normalize_name(h["name"])
        place = _fuzzy_match_place(norm, lookup)
        if place:
            _apply_place_data(h, place)
            matched_place_names.add(_normalize_name(place.get("name", "")))

    # Pass 2: add unmatched Places hotels (branded + high-rated independents)
    for place in places_results:
        pname_norm = _normalize_name(place.get("name", ""))
        if pname_norm in matched_place_names:
            continue
        # Check if this place fuzzy-matched to an existing hotel
        already_matched = False
        for h in hotels:
            hn = _normalize_name(h["name"])
            if _fuzzy_match_place(hn, {pname_norm: place}) is not None:
                already_matched = True
                break
        if already_matched:
            continue

        name = place.get("name", "")
        brand = detect_brand(name)
        rating = place.get("rating", 0) or 0

        # Add branded hotels always; independents only if highly rated
        if brand == "independent" and rating < 4.0:
            continue

        hotel = {
            "name": name,
            "brand": brand,
            "star_class": 0,  # Places doesn't provide star class
            "rate_per_night": 0,
            "total_rate": 0,
            "nights": hotels[0].get("nights", 3) if hotels else 3,
            "check_in": hotels[0].get("check_in", "") if hotels else "",
            "check_out": hotels[0].get("check_out", "") if hotels else "",
            "address": place.get("formatted_address", ""),
            "image_url": "",
            "amenities": [],
            "google_hotels_url": "",
            "overall_rating": 0,
            "reviews": 0,
            "review_snippets": [],
            "editorial_summary": "",
            "google_maps_url": "",
            "website": "",
            "places_only": True,
        }
        _apply_place_data(hotel, place)
        hotels.append(hotel)

    return hotels


def enrich_with_details(hotels: list[dict], max_detail_calls: int = 10) -> list[dict]:
    """Fetch Place Details for hotels that didn't get reviews from search.

    The Places API (New) search already returns reviews for most results,
    so this only fills gaps for hotels that matched but lack review data.
    """
    for h in hotels[:max_detail_calls]:
        place_id = h.get("place_id", "")
        if not place_id or h.get("review_snippets"):
            continue  # already has reviews from search merge
        try:
            details = get_place_details(place_id)
            if details.get("googleMapsUri"):
                h["google_maps_url"] = details["googleMapsUri"]
            if details.get("websiteUri"):
                h["website"] = details["websiteUri"]
            es = details.get("editorialSummary", {})
            if es:
                h["editorial_summary"] = es.get("text", "") if isinstance(es, dict) else ""
            reviews = details.get("reviews", [])
            snippets = []
            for rev in reviews[:3]:
                author = rev.get("authorAttribution", {})
                rev_text = rev.get("text", {})
                snippets.append({
                    "author": author.get("displayName", "") if isinstance(author, dict) else "",
                    "rating": rev.get("rating", 0),
                    "text": (rev_text.get("text", "") if isinstance(rev_text, dict) else str(rev_text or ""))[:200],
                    "time": rev.get("relativePublishTimeDescription", ""),
                })
            if snippets:
                h["review_snippets"] = snippets
        except Exception:
            pass
    return hotels


# ---------------------------------------------------------------------------
# Distance computation
# ---------------------------------------------------------------------------

def compute_distances(hotels: list[dict], city_key: str = "venice") -> list[dict]:
    """Compute distance from landmark for each hotel with coordinates."""
    landmark = LANDMARKS.get(city_key)
    if not landmark:
        return hotels
    for h in hotels:
        lat, lng = h.get("latitude"), h.get("longitude")
        if lat and lng:
            km = _haversine(lat, lng, landmark["lat"], landmark["lng"])
            h["distance_km"] = round(km, 1)
            h["distance_mi"] = round(km * 0.621371, 1)
            h["landmark_name"] = landmark["name"]
        else:
            h["distance_km"] = None
            h["distance_mi"] = None
            h["landmark_name"] = landmark["name"]
    return hotels


# ---------------------------------------------------------------------------
# Credit card program tagging
# ---------------------------------------------------------------------------

def tag_cc_programs(hotels: list[dict], city_key: str = "venice") -> list[dict]:
    """Tag each hotel with matching CC programs."""
    for h in hotels:
        h["cc_programs"] = _match_cc_program(h.get("name", ""), city_key)
    return hotels


# ---------------------------------------------------------------------------
# Scoring — uses BOTH SerpAPI pricing + Places ratings
# ---------------------------------------------------------------------------

def score_hotels(hotels: list[dict]) -> list[dict]:
    """Apply blended scoring formula. Lower score = better.

    Primary drivers: price + hotel star class.
    Tiebreaker: user rating + review count from Google Places.

      score = rate_per_night                                       (primary)
      score += star_bonus     (5★: -200, 4★: -100, 3★: 0, 2★: +100) (primary)
      score += rating_bonus   (4.5+: -50, 4.25+: -35, 4.0+: -20, 3.75+: -10) (tiebreaker)
      score += reviews_bonus  (5000+: -25, 2000+: -15, 1000+: -10)  (tiebreaker)
      score += budget_penalty (if price > $350: +0.5 per $ over)
    """
    for h in hotels:
        # Places-only hotels have no price — use a neutral base score
        if h.get("places_only") or h["rate_per_night"] == 0:
            score = 200  # neutral base — will rank by star/rating bonuses
        else:
            score = h["rate_per_night"]

        # Star class bonus — primary (from SerpAPI)
        try:
            stars = int(h.get("star_class", 0) or 0)
        except (ValueError, TypeError):
            stars = 0
        if stars >= 5:
            score -= 200
        elif stars >= 4:
            score -= 100
        elif stars >= 3:
            score -= 0
        elif stars >= 2:
            score += 100

        # User rating — tiebreaker (from Google Places, 1-5 scale)
        rating = h.get("overall_rating", 0)
        if rating >= 4.5:
            score -= 50
        elif rating >= 4.25:
            score -= 35
        elif rating >= 4.0:
            score -= 20
        elif rating >= 3.75:
            score -= 10

        # Review count — tiebreaker (from Google Places)
        reviews = h.get("reviews", 0)
        if reviews >= 5000:
            score -= 25
        elif reviews >= 2000:
            score -= 15
        elif reviews >= 1000:
            score -= 10

        # Budget penalty (only for priced hotels)
        if h["rate_per_night"] > 350:
            score += 0.5 * (h["rate_per_night"] - 350)

        h["score"] = round(score, 1)

    hotels.sort(key=lambda h: h["score"])
    return hotels


# ---------------------------------------------------------------------------
# Categorize
# ---------------------------------------------------------------------------

def categorize_hotels(hotels: list[dict]) -> dict:
    """Split scored hotels into best_overall, best_marriott, best_hilton."""
    best_overall = hotels[:]
    best_marriott = [h for h in hotels if h["brand"] == "marriott"]
    best_hilton = [h for h in hotels if h["brand"] == "hilton"]

    return {
        "best_overall": best_overall,
        "best_marriott": best_marriott,
        "best_hilton": best_hilton,
    }


# ---------------------------------------------------------------------------
# Loyalty links
# ---------------------------------------------------------------------------

def loyalty_url(hotel: dict) -> str:
    """Generate loyalty booking URL with dates pre-filled."""
    ci = hotel.get("check_in", "")
    co = hotel.get("check_out", "")

    if hotel["brand"] == "marriott":
        return (
            f"https://www.marriott.com/search/default.mi?"
            f"fromDate={ci}&toDate={co}&"
            f"searchType=InCity&destinationAddress=Venice%20Italy"
        )
    elif hotel["brand"] == "hilton":
        return (
            f"https://www.hilton.com/en/search/?"
            f"query=Venice%20Italy&"
            f"arrivalDate={ci}&departureDate={co}"
        )
    return ""

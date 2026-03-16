"""
Flight search module: LAX → VCE via SerpAPI (Google Flights).
"""

import base64
import json
import os
import smtplib
import sys
import urllib.parse

from dotenv import load_dotenv

load_dotenv()
from collections import defaultdict
from datetime import date, datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

SERPAPI_KEY = os.environ.get("SERPAPI_KEY", "")
GMAIL_USER = os.environ.get("GMAIL_USER", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")

TRIP_DATE = date(2026, 7, 3)
TO_ADDRESS = "mdahya@gmail.com"

DEPARTURE_DATES = ["2026-06-28", "2026-06-29", "2026-06-30"]
RETURN_DATES = ["2026-07-13", "2026-07-14", "2026-07-15"]
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY", "")


# ---------------------------------------------------------------------------
# Source – Google Flights via SerpAPI (nonstop + 1-stop per date)
# ---------------------------------------------------------------------------

# Global SerpAPI call counter for logging/budgeting
_serpapi_call_log: list[dict] = []


def get_serpapi_call_log() -> list[dict]:
    """Return the call log and total count."""
    return list(_serpapi_call_log)


def reset_serpapi_call_log():
    """Reset the call log (call at start of each API request)."""
    _serpapi_call_log.clear()


def search_serpapi(
    origin: str = "LAX",
    destination: str = "VCE",
    dates: list[str] | None = None,
    travel_class: str = "1",
    max_stops: int = 1,
    min_stops: int = 0,
) -> list[dict]:
    """Query Google Flights for each departure date.

    Makes one call per stops level (nonstop, 1-stop, optionally 2-stop).
    SerpAPI stops param: "1"=nonstop, "2"=up to 1 stop, "3"=up to 2 stops.
    min_stops skips lower levels (e.g., min_stops=1 skips nonstop search).

    travel_class: "1"=economy, "2"=premium economy, "3"=business, "4"=first
    """
    if dates is None:
        dates = DEPARTURE_DATES

    class_labels = {"1": "economy", "2": "premium_economy", "3": "business", "4": "first"}
    # SerpAPI stops: "1"=nonstop, "2"=≤1 stop, "3"=≤2 stops
    stops_params = [str(i) for i in range(min_stops + 1, max_stops + 2)]

    all_results: list[dict] = []

    for dep_date in dates:
        for stops_param in stops_params:
            params = {
                "engine": "google_flights",
                "api_key": SERPAPI_KEY,
                "departure_id": origin,
                "arrival_id": destination,
                "outbound_date": dep_date,
                "type": "2",            # one-way
                "travel_class": travel_class,
                "adults": "1",
                "stops": stops_param,
                "currency": "USD",
            }

            resp = requests.get(
                "https://serpapi.com/search", params=params, timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()

            flights = data.get("best_flights", []) + data.get("other_flights", [])
            _serpapi_call_log.append({
                "route": f"{origin}→{destination}",
                "date": dep_date,
                "stops": stops_param,
                "class": class_labels.get(travel_class, "economy"),
                "results": len(flights),
            })
            print(f"[SerpAPI #{len(_serpapi_call_log)}] "
                  f"{origin}→{destination} {dep_date} "
                  f"stops={stops_param} → {len(flights)} flights")
            for flight in flights:
                flight["_source"] = "serpapi"
                flight["_search_date"] = dep_date
                flight["_travel_class"] = class_labels.get(travel_class, "economy")
            all_results.extend(flights)

    route = f"{origin}→{destination}"
    cls_label = class_labels.get(travel_class, travel_class)
    print(f"[SerpAPI] {route} ({cls_label}) returned {len(all_results)} results "
          f"({len(dates)} dates × {len(stops_params)} queries)")
    return all_results


def search_serpapi_multicity(
    origin: str = "LAX",
    dest: str = "VCE",
    return_from: str = "IST",
    outbound_date: str = "2026-06-29",
    return_date: str = "2026-07-14",
    max_stops: int = 1,
    min_stops: int = 0,
) -> list[dict]:
    """Search Google Flights for multi-city itineraries (origin→dest + return_from→origin).

    Uses SerpAPI type=3 (multi-city) with multi_city_json parameter.
    SerpAPI multi-city is a 2-step process:
      1. First call returns outbound options with total round-trip prices.
      2. Second call uses departure_token to get return leg options.
    We fetch returns for the top outbound picks to get full itinerary details.
    """
    all_results: list[dict] = []

    multi_city_json = json.dumps([
        {"departure_id": origin, "arrival_id": dest, "date": outbound_date},
        {"departure_id": return_from, "arrival_id": origin, "date": return_date},
    ])

    # Step 1: Get outbound options
    params = {
        "engine": "google_flights",
        "api_key": SERPAPI_KEY,
        "type": "3",
        "multi_city_json": multi_city_json,
        "travel_class": "1",
        "adults": "1",
        "currency": "USD",
    }

    resp = requests.get(
        "https://serpapi.com/search", params=params, timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    outbound_flights = data.get("best_flights", []) + data.get("other_flights", [])
    _serpapi_call_log.append({
        "route": f"{origin}→{dest} (multi-city outbound)",
        "date": f"{outbound_date}/{return_date}",
        "stops": "all",
        "class": "economy",
        "results": len(outbound_flights),
    })
    print(f"[SerpAPI #{len(_serpapi_call_log)}] "
          f"{origin}→{dest} (multi-city outbound) {outbound_date} "
          f"→ {len(outbound_flights)} flights")

    # Step 2: Get return options for top outbound picks (by price).
    # Use departure_token from best outbound to get return leg details.
    # We fetch returns for the cheapest outbound; all outbound options
    # are then paired with the best return for display.
    best_return_flights: list[dict] = []
    if outbound_flights:
        # Pick cheapest outbound to fetch returns for
        cheapest = min(outbound_flights, key=lambda f: f.get("price", 99999))
        token = cheapest.get("departure_token", "")
        if token:
            params2 = {
                "engine": "google_flights",
                "api_key": SERPAPI_KEY,
                "type": "3",
                "multi_city_json": multi_city_json,
                "departure_token": token,
                "currency": "USD",
            }
            try:
                resp2 = requests.get(
                    "https://serpapi.com/search", params=params2, timeout=30,
                )
                resp2.raise_for_status()
                data2 = resp2.json()
                best_return_flights = (
                    data2.get("best_flights", [])
                    + data2.get("other_flights", [])
                )
                _serpapi_call_log.append({
                    "route": f"{return_from}→{origin} (multi-city return)",
                    "date": f"{return_date}",
                    "stops": "all",
                    "class": "economy",
                    "results": len(best_return_flights),
                })
                print(f"[SerpAPI #{len(_serpapi_call_log)}] "
                      f"{return_from}→{origin} (multi-city return) {return_date} "
                      f"→ {len(best_return_flights)} flights")
            except Exception as exc:
                print(f"[SerpAPI] Return leg fetch failed: {exc}")

    # Pick the cheapest return option (best pairing)
    best_return = None
    if best_return_flights:
        best_return = min(best_return_flights, key=lambda f: f.get("price", 99999))

    # Tag all outbound flights with metadata and attach the best return
    for flight in outbound_flights:
        flight["_source"] = "serpapi_multicity"
        flight["_search_date"] = outbound_date
        flight["_outbound_date"] = outbound_date
        flight["_return_date"] = return_date
        flight["_dest"] = dest
        flight["_return_from"] = return_from
        flight["_return_raw"] = best_return  # attach best return leg data
    all_results.extend(outbound_flights)

    route = f"{origin}↔{dest}+{return_from}"
    print(f"[SerpAPI] {route} (multi-city) returned {len(all_results)} results "
          f"with {len(best_return_flights)} return options")
    return all_results


def _normalize_serpapi_multicity(flight: dict, outbound_date: str, return_date: str) -> dict:
    """Flatten a SerpAPI multi-city flight into a common schema with return_leg.

    SerpAPI multi-city type=3 returns outbound-only data in the first call.
    The return leg data comes from a separate call and is attached as _return_raw.
    We normalize each leg independently (no segment splitting needed).
    """
    out_segments = flight.get("flights", [])
    if not out_segments:
        return _normalize_serpapi(flight)

    # --- Outbound leg (all segments belong to outbound) ---
    out_first = out_segments[0]
    out_last = out_segments[-1]
    out_airlines = [seg.get("airline", "") for seg in out_segments]
    out_layovers = flight.get("layovers", [])
    out_total_layover = sum(lo.get("duration", 0) for lo in out_layovers)
    out_dep_time = out_first.get("departure_airport", {}).get("time", "")
    out_arr_time = out_last.get("arrival_airport", {}).get("time", "")
    out_duration = flight.get("total_duration", 0)

    google_flights_url = _build_google_flights_url(flight)

    # --- Return leg (from separate API call, attached as _return_raw) ---
    ret_raw = flight.get("_return_raw")
    ret_leg = _build_return_leg(ret_raw, return_date)

    return {
        "primary_airline": out_airlines[0] if out_airlines else "",
        "airlines": out_airlines,
        "departure_time": out_dep_time,
        "arrival_time": out_arr_time,
        "stops": len(out_layovers),
        "total_layover_min": out_total_layover,
        "total_duration_min": out_duration,
        "price": flight.get("price", 0),
        "source": "serpapi",
        "search_date": outbound_date,
        "google_flights_url": google_flights_url,
        "type": "multi_city",
        "return_leg": ret_leg,
        "raw": flight,
    }


def _build_return_leg(ret_raw: dict | None, return_date: str) -> dict:
    """Build a return_leg dict from the raw return flight data."""
    if not ret_raw:
        return {
            "primary_airline": "",
            "airlines": [],
            "departure_time": "",
            "arrival_time": "",
            "stops": 0,
            "total_layover_min": 0,
            "total_duration_min": 0,
            "layover_info": "",
            "search_date": return_date,
        }

    ret_segments = ret_raw.get("flights", [])
    ret_first = ret_segments[0] if ret_segments else {}
    ret_last = ret_segments[-1] if ret_segments else {}
    ret_airlines = [seg.get("airline", "") for seg in ret_segments]
    ret_layovers = ret_raw.get("layovers", [])
    ret_total_layover = sum(lo.get("duration", 0) for lo in ret_layovers)
    ret_dep_time = ret_first.get("departure_airport", {}).get("time", "")
    ret_arr_time = ret_last.get("arrival_airport", {}).get("time", "")
    ret_duration = ret_raw.get("total_duration", 0)

    return {
        "primary_airline": ret_airlines[0] if ret_airlines else "",
        "airlines": ret_airlines,
        "departure_time": ret_dep_time,
        "arrival_time": ret_arr_time,
        "stops": len(ret_layovers),
        "total_layover_min": ret_total_layover,
        "total_duration_min": ret_duration,
        "layover_info": _layover_info_from_layovers(
            [{"name": lo.get("name", lo.get("id", "")), "id": lo.get("id", ""),
              "duration": lo.get("duration", 0)} for lo in ret_layovers],
            len(ret_layovers),
        ),
        "search_date": return_date,
    }


def _layover_info_from_layovers(layovers: list[dict], stops: int) -> str:
    """Build layover info string from a list of layover dicts."""
    if stops == 0:
        return "Nonstop"
    if layovers:
        parts = []
        for lo in layovers:
            name = lo.get("name", lo.get("id", ""))
            dur = _fmt_duration(lo.get("duration", 0))
            parts.append(f"{dur} in {name}" if name else dur)
        return " · ".join(parts)
    return "Connection"


def search_premium_business_prices(
    origin: str,
    destination: str,
    dates: list[str],
) -> dict[str, dict[tuple[str, str], int]]:
    """Search for premium economy and business class prices.

    Runs premium and business searches in parallel.
    Returns a dict with keys 'premium' and 'business', each mapping
    (airline_lower, search_date) -> cheapest_price for that class.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    result: dict[str, dict[tuple[str, str], int]] = {
        "premium": {},
        "business": {},
    }

    def _search_class(class_key: str, travel_class: str) -> tuple[str, list[dict]]:
        raw = search_serpapi(origin, destination, dates, travel_class=travel_class)
        return class_key, raw

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(_search_class, "premium", "2"),
            pool.submit(_search_class, "business", "3"),
        ]
        for fut in as_completed(futures):
            try:
                class_key, raw = fut.result()
                for flight_raw in raw:
                    segments = flight_raw.get("flights", [])
                    airline = (segments[0].get("airline", "") if segments else "").lower().strip()
                    date = flight_raw.get("_search_date", "")
                    price = flight_raw.get("price", 0)
                    if airline and date and price:
                        key = (airline, date)
                        if key not in result[class_key] or price < result[class_key][key]:
                            result[class_key][key] = price
            except Exception as exc:
                print(f"[SerpAPI] premium/business search failed for {origin}→{destination}: {exc}")

    prem_count = len(result["premium"])
    biz_count = len(result["business"])
    print(f"[Premium/Business] {origin}→{destination}: {prem_count} premium, {biz_count} business prices")
    return result


def merge_premium_business_prices(
    flights: list[dict],
    price_lookup: dict[str, dict[tuple[str, str], int]],
) -> list[dict]:
    """Merge premium economy and business prices into economy flights."""
    for f in flights:
        airline = f["primary_airline"].lower().strip()
        date = f.get("search_date", "")
        key = (airline, date)

        if key in price_lookup.get("premium", {}):
            f["premium_economy_price"] = price_lookup["premium"][key]
        if key in price_lookup.get("business", {}):
            f["business_price"] = price_lookup["business"][key]

    prem = sum(1 for f in flights if f.get("premium_economy_price"))
    biz = sum(1 for f in flights if f.get("business_price"))
    print(f"[Merge] {prem}/{len(flights)} flights got premium, {biz}/{len(flights)} got business prices")
    return flights


# ---------------------------------------------------------------------------
# Source – Skyscanner via RapidAPI (return flights only)
# ---------------------------------------------------------------------------

def search_skyscanner(
    origin: str = "IST",
    destination: str = "LAX",
    dates: list[str] | None = None,
) -> list[dict]:
    """Search Skyscanner via RapidAPI for one-way flights."""
    if not RAPIDAPI_KEY:
        print("[Skyscanner] RAPIDAPI_KEY not set — skipping")
        return []

    if dates is None:
        dates = RETURN_DATES

    all_results: list[dict] = []
    headers = {
        "X-RapidAPI-Key": RAPIDAPI_KEY,
        "X-RapidAPI-Host": "sky-scanner3.p.rapidapi.com",
    }

    for dep_date in dates:
        try:
            resp = requests.get(
                "https://sky-scanner3.p.rapidapi.com/flights/search-one-way",
                headers=headers,
                params={
                    "fromEntityId": origin,
                    "toEntityId": destination,
                    "departDate": dep_date,
                },
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()

            itineraries = data.get("data", {}).get("itineraries", [])
            for itin in itineraries:
                itin["_source"] = "skyscanner"
                itin["_search_date"] = dep_date
            all_results.extend(itineraries)
        except Exception as exc:
            print(f"[Skyscanner] Error for {dep_date}: {exc}")

    route = f"{origin}→{destination}"
    print(f"[Skyscanner] {route} returned {len(all_results)} results "
          f"({len(dates)} dates)")
    return all_results


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

def _pb_varint(value: int) -> bytes:
    """Encode an integer as a protobuf varint."""
    parts = []
    while value > 0x7F:
        parts.append((value & 0x7F) | 0x80)
        value >>= 7
    parts.append(value)
    return bytes(parts)


def _pb_field(field_num: int, wire_type: int, data) -> bytes:
    """Encode a single protobuf field."""
    tag = _pb_varint((field_num << 3) | wire_type)
    if wire_type == 0:  # varint
        return tag + _pb_varint(data)
    # wire_type == 2: length-delimited
    if isinstance(data, str):
        data = data.encode()
    return tag + _pb_varint(len(data)) + data


def _build_google_flights_url(flight: dict) -> str:
    """Build a Google Flights deep link URL for a specific flight itinerary."""
    segments = flight.get("flights", [])
    if not segments:
        return ""

    # Map airline names to IATA codes
    AIRLINE_CODES = {
        "delta": "DL", "united": "UA", "american": "AA",
        "british airways": "BA", "air france": "AF", "lufthansa": "LH",
        "klm": "KL", "ita airways": "AZ", "ita": "AZ", "alaska": "AS",
        "virgin atlantic": "VS", "tap air portugal": "TP", "iberia": "IB",
        "swiss": "LX", "austrian": "OS", "condor": "DE", "finnair": "AY",
        "air canada": "AC", "aer lingus": "EI", "turkish airlines": "TK",
        "qatar airways": "QR", "emirates": "EK", "etihad": "EY",
        "singapore airlines": "SQ", "cathay pacific": "CX",
        "scandinavian airlines": "SK", "lot polish": "LO", "norse": "N0",
        "icelandair": "FI", "jetblue": "B6", "frontier": "F9",
        "spirit": "NK", "sun country": "SY", "play": "OG",
    }

    seg_list = []
    for seg in segments:
        dep = seg.get("departure_airport", {})
        arr = seg.get("arrival_airport", {})
        origin = dep.get("id", "")
        dest = arr.get("id", "")
        dep_date = dep.get("time", "")[:10]  # YYYY-MM-DD

        # Extract airline code from flight_number (e.g., "DL 290") or airline name
        fn = seg.get("flight_number", "")
        if fn and " " in fn:
            airline_code = fn.split()[0]
            flight_num = fn.split()[-1]
        else:
            airline_name = seg.get("airline", "").lower().strip()
            airline_code = AIRLINE_CODES.get(airline_name, "")
            flight_num = fn

        if not all([origin, dest, dep_date, airline_code, flight_num]):
            return ""
        seg_list.append((origin, dep_date, dest, airline_code, flight_num))

    # Build protobuf
    itin = _pb_field(2, 2, seg_list[0][1])  # departure date
    for origin, dep_date, dest, ac, fnum in seg_list:
        seg_pb = (
            _pb_field(1, 2, origin) +
            _pb_field(2, 2, dep_date) +
            _pb_field(3, 2, dest) +
            _pb_field(5, 2, ac) +
            _pb_field(6, 2, fnum)
        )
        itin += _pb_field(4, 2, seg_pb)

    first_origin = seg_list[0][0]
    final_dest = seg_list[-1][2]
    itin += _pb_field(13, 2, _pb_field(1, 0, 1) + _pb_field(2, 2, first_origin))
    itin += _pb_field(14, 2, _pb_field(1, 0, 1) + _pb_field(2, 2, final_dest))

    tfs = (
        _pb_field(1, 0, 28) +   # unknown constant
        _pb_field(2, 0, 2) +    # one-way
        _pb_field(3, 2, itin) +
        _pb_field(8, 2, b"\x01") +
        _pb_field(9, 0, 1) +
        _pb_field(14, 0, 1) +
        _pb_field(19, 0, 2)     # economy
    )

    tfs_b64 = base64.b64encode(tfs).decode().rstrip("=")
    return f"https://www.google.com/travel/flights?tfs={tfs_b64}&tfu=EgIIAQ"


def _normalize_serpapi(flight: dict) -> dict:
    """Flatten a SerpAPI flight object into a common schema."""
    segments = flight.get("flights", [])
    first = segments[0] if segments else {}
    last = segments[-1] if segments else {}

    airlines = [seg.get("airline", "") for seg in segments]
    primary_airline = airlines[0] if airlines else ""

    dep_time_str = first.get("departure_airport", {}).get("time", "")
    arr_time_str = last.get("arrival_airport", {}).get("time", "")

    layovers = flight.get("layovers", [])
    total_layover_min = sum(lo.get("duration", 0) for lo in layovers)

    google_flights_url = _build_google_flights_url(flight)

    return {
        "primary_airline": primary_airline,
        "airlines": airlines,
        "departure_time": dep_time_str,
        "arrival_time": arr_time_str,
        "stops": len(layovers),
        "total_layover_min": total_layover_min,
        "total_duration_min": flight.get("total_duration", 0),
        "price": flight.get("price", 0),
        "source": "serpapi",
        "search_date": flight.get("_search_date", ""),
        "google_flights_url": google_flights_url,
        "raw": flight,
    }


def _normalize_skyscanner(itin: dict) -> dict | None:
    """Flatten a Skyscanner itinerary into the common schema."""
    legs = itin.get("legs", [])
    if not legs:
        return None

    leg = legs[0]  # one-way, only one leg
    price_info = itin.get("price", {})
    price = price_info.get("raw", 0)
    if not price:
        return None

    segments = leg.get("segments", [])
    carriers = leg.get("carriers", {}).get("marketing", [])
    airlines = [c.get("name", "") for c in carriers]
    primary_airline = airlines[0] if airlines else ""

    dep_time = leg.get("departure", "")
    arr_time = leg.get("arrival", "")
    stops = leg.get("stopCount", 0)
    duration = leg.get("durationInMinutes", 0)

    # Build layover info from segments (compatible with _layover_info)
    layovers = []
    if stops > 0 and len(segments) > 1:
        for i in range(len(segments) - 1):
            arr_seg = segments[i].get("arrival", "")
            dep_seg = segments[i + 1].get("departure", "")
            conn_airport = segments[i].get("destination", {}).get(
                "name",
                segments[i].get("destination", {}).get("flightPlaceId", ""),
            )
            try:
                arr_dt = datetime.fromisoformat(arr_seg)
                dep_dt = datetime.fromisoformat(dep_seg)
                layover_min = int((dep_dt - arr_dt).total_seconds() / 60)
            except (ValueError, TypeError):
                layover_min = 0
            layovers.append({"name": conn_airport, "duration": layover_min})

    total_layover_min = sum(lo["duration"] for lo in layovers)

    return {
        "primary_airline": primary_airline,
        "airlines": airlines,
        "departure_time": dep_time,
        "arrival_time": arr_time,
        "stops": stops,
        "total_layover_min": total_layover_min,
        "total_duration_min": duration,
        "price": int(price),
        "source": "skyscanner",
        "search_date": itin.get("_search_date", ""),
        "google_flights_url": "",
        "raw": {"layovers": layovers},
    }


def normalize(raw_results: list[dict]) -> list[dict]:
    """Convert raw results from any source into a unified list."""
    normalized = []
    for r in raw_results:
        if r.get("_source") == "skyscanner":
            n = _normalize_skyscanner(r)
            if n:
                normalized.append(n)
        elif r.get("_source") == "serpapi_multicity":
            normalized.append(_normalize_serpapi_multicity(
                r, r.get("_outbound_date", ""), r.get("_return_date", ""),
            ))
        else:
            normalized.append(_normalize_serpapi(r))
    return normalized


# ---------------------------------------------------------------------------
# Fare-type labelling
# ---------------------------------------------------------------------------

BASIC_ECONOMY_CARRIERS = {"american", "delta", "united"}
BASIC_TO_MAIN_ADDER = {
    "delta": 120,
    "united": 100,
    "american": 100,
}


def _extract_fare_prices_from_raw(raw: dict) -> tuple:
    """Extract premium economy and business/first prices from SerpAPI raw data.

    SerpAPI may include fare info in several places:
    - extensions: list of strings like "Premium economy from $1,234"
    - price_insights.fare_options: list of {fare_class, price}
    - flights[].fare_category or fare_info fields
    Returns (premium_price, business_price) or (None, None).
    """
    premium_price = None
    business_price = None

    # 1. Check extensions array for fare price strings
    extensions = raw.get("extensions", [])
    if isinstance(extensions, list):
        for ext in extensions:
            if not isinstance(ext, str):
                continue
            ext_lower = ext.lower()
            # Look for patterns like "Premium economy from $1,234"
            if "premium" in ext_lower and "economy" in ext_lower:
                premium_price = _parse_price_from_string(ext)
            elif "business" in ext_lower or "first" in ext_lower:
                business_price = _parse_price_from_string(ext)

    # 2. Check price_insights.fare_options
    price_insights = raw.get("price_insights", {})
    if isinstance(price_insights, dict):
        fare_options = price_insights.get("fare_options", [])
        if isinstance(fare_options, list):
            for opt in fare_options:
                if not isinstance(opt, dict):
                    continue
                fc = (opt.get("fare_class") or opt.get("cabin") or "").lower()
                p = opt.get("price")
                if p and "premium" in fc:
                    premium_price = premium_price or p
                elif p and ("business" in fc or "first" in fc):
                    business_price = business_price or p

    # 3. Check top-level premium_economy_price / business_price (direct fields)
    if not premium_price and raw.get("premium_economy_price"):
        premium_price = raw["premium_economy_price"]
    if not business_price and raw.get("business_price"):
        business_price = raw["business_price"]

    # 4. Check nested flight segments for fare_category
    for seg in raw.get("flights", []):
        if not isinstance(seg, dict):
            continue
        fc = (seg.get("fare_category") or seg.get("travel_class") or "").lower()
        if "premium" in fc and not premium_price:
            premium_price = raw.get("price")
        elif ("business" in fc or "first" in fc) and not business_price:
            business_price = raw.get("price")

    return premium_price, business_price


def _parse_price_from_string(s: str):
    """Extract a numeric price from a string like 'Premium economy from $1,234'."""
    import re
    match = re.search(r"\$[\d,]+", s)
    if match:
        try:
            return int(match.group().replace("$", "").replace(",", ""))
        except ValueError:
            pass
    return None


def label_fare_types(flights: list[dict]) -> list[dict]:
    """Label fare types and populate all fare-class price fields.

    Google Flights returns the cheapest economy fare. For the US Big 3
    (American, Delta, United) this is Basic Economy — economy_main_price
    is estimated as basic + per-carrier adder (BASIC_TO_MAIN_ADDER).

    Non-Big-3 carriers' base fare is Economy Main (standard cabin), so
    basic_economy_price and economy_main_price are both set to the search price.
    """
    for f in flights:
        carrier = f["primary_airline"].lower().strip()
        raw = f.get("raw", {})

        # Extract premium/business prices from raw SerpAPI data
        premium_price, business_price = _extract_fare_prices_from_raw(raw)

        if carrier in BASIC_ECONOMY_CARRIERS:
            f["fare_type"] = "Economy Main"
            f["basic_economy_price"] = f["price"]
            f["economy_main_price"] = f["price"] + BASIC_TO_MAIN_ADDER.get(carrier, 100)
        else:
            f["fare_type"] = "Economy Main"
            f["basic_economy_price"] = f["price"]
            f["economy_main_price"] = f["price"]

        f["premium_economy_price"] = premium_price
        f["business_price"] = business_price

    big3 = sum(1 for f in flights if f["primary_airline"].lower().strip() in BASIC_ECONOMY_CARRIERS)
    prem = sum(1 for f in flights if f.get("premium_economy_price"))
    biz = sum(1 for f in flights if f.get("business_price"))
    print(f"[Fare] {big3} Big 3 (Basic Economy), {len(flights) - big3} Economy, "
          f"{prem} w/ premium, {biz} w/ business")
    return flights


# ---------------------------------------------------------------------------
# 1. Filter
# ---------------------------------------------------------------------------

BLOCKED_AIRLINES = {
    "spirit", "frontier", "allegiant", "sun country",
    "ryanair", "easyjet", "wizz air", "norwegian",
    "vueling", "transavia", "volotea",
}


def filter_flights(flights: list[dict], max_stops: int = 1) -> list[dict]:
    """Remove budget carriers and anything with more than max_stops stops."""
    kept: list[dict] = []
    for f in flights:
        if f["stops"] > max_stops:
            continue
        if any(a.lower() in BLOCKED_AIRLINES for a in f["airlines"]):
            continue
        kept.append(f)

    removed = len(flights) - len(kept)
    print(f"[Filter] Kept {len(kept)} flights, removed {removed} (max_stops={max_stops})")
    return kept


# ---------------------------------------------------------------------------
# 2. Dedup
# ---------------------------------------------------------------------------

def _dedup_key(f: dict) -> str:
    """Build a key from primary airline + departure date/hour/minute."""
    airline = f["primary_airline"].lower().strip()
    dep = f["departure_time"]
    # Normalise to minute-level: keep only "YYYY-MM-DD HH:MM"
    dep_clean = dep.replace("T", " ")[:16]
    return f"{airline}|{dep_clean}"


def dedup_flights(flights: list[dict]) -> list[dict]:
    """Merge near-identical flights across sources, keeping the lowest price."""
    best: dict[str, dict] = {}
    for f in flights:
        key = _dedup_key(f)
        if key not in best or f["price"] < best[key]["price"]:
            best[key] = f

    deduped = list(best.values())
    removed = len(flights) - len(deduped)
    print(f"[Dedup] {len(deduped)} unique flights ({removed} duplicates merged)")
    return deduped


# ---------------------------------------------------------------------------
# 3. Scoring
# ---------------------------------------------------------------------------

# Scoring rationale — sources for all bonus values
SCORING_RATIONALE = {
    "sources": [
        "Skytrax World Airline Awards 2025",
        "Cirium On-Time Performance Review 2025",
        "AirHelp Score 2025",
        "AirlineRatings Safest Airlines 2026",
    ],
    "last_updated": "March 2026",
    "lufthansa_note": (
        "Bonus reduced due to active pilot strikes and labor disputes "
        "through end of 2026"
    ),
    "emirates_etihad_note": (
        "Independent airlines — not in Star Alliance, Oneworld or SkyTeam. "
        "Points do not transfer from Amex/Chase/United to these airlines."
    ),
    "turkish_note": (
        "Istanbul Airport ranked #1 globally for punctuality by Cirium 2025. "
        "Turkish nonstop IST-LAX and IST-ATL are auto TOP PICK."
    ),
}

# ---------------------------------------------------------------------------
# LAX → VCE (outbound)
# ---------------------------------------------------------------------------
LAX_VCE_BONUSES: dict[str, int] = {
    "delta": -280,
    "air france": -200,
    "virgin atlantic": -190,
    "british airways": -180,
    "united": -170,
    "turkish airlines": -160,
    "klm": -150,
    "swiss": -140,
    "swiss international": -140,
    "american": -130,
    "american airlines": -130,
    "lufthansa": -80,
}
LAX_VCE_AUTO_TOP_PICK: set[str] = set()

# ---------------------------------------------------------------------------
# IST → LAX (return)
# ---------------------------------------------------------------------------
IST_LAX_BONUSES: dict[str, int] = {
    "turkish airlines": -270,
    "air france": -190,
    "virgin atlantic": -180,
    "klm": -170,
    "british airways": -160,
    "united": -150,
    "swiss": -140,
    "swiss international": -140,
    "lufthansa": -70,
}
IST_LAX_AUTO_TOP_PICK: set[str] = {"turkish airlines"}

# ---------------------------------------------------------------------------
# ATL → VCE (outbound)
# ---------------------------------------------------------------------------
ATL_VCE_BONUSES: dict[str, int] = {
    "delta": -320,
    "virgin atlantic": -200,
    "turkish airlines": -190,
    "air france": -180,
    "british airways": -170,
    "klm": -150,
    "united": -140,
    "swiss": -130,
    "swiss international": -130,
    "american": -120,
    "american airlines": -120,
    "lufthansa": -70,
}
ATL_VCE_AUTO_TOP_PICK: set[str] = set()

# ---------------------------------------------------------------------------
# IST → ATL (return)
# ---------------------------------------------------------------------------
IST_ATL_BONUSES: dict[str, int] = {
    "turkish airlines": -270,
    "delta": -250,
    "virgin atlantic": -190,
    "air france": -180,
    "klm": -160,
    "british airways": -160,
    "lufthansa": -70,
}
IST_ATL_AUTO_TOP_PICK: set[str] = {"turkish airlines"}

# ---------------------------------------------------------------------------
# AKL → VCE (outbound)
# ---------------------------------------------------------------------------
AKL_VCE_BONUSES: dict[str, int] = {
    "qatar airways": -320,
    "singapore airlines": -300,
    "cathay pacific": -280,
    "emirates": -270,
    "etihad": -240,
    "etihad airways": -240,
    "air new zealand": -180,
    "qantas": -160,
    "ana": -150,
    "all nippon airways": -150,
}
AKL_VCE_AUTO_TOP_PICK: set[str] = set()

# ---------------------------------------------------------------------------
# IST → AKL (return)
# ---------------------------------------------------------------------------
IST_AKL_BONUSES: dict[str, int] = {
    "emirates": -300,
    "qatar airways": -290,
    "singapore airlines": -270,
    "etihad": -250,
    "etihad airways": -250,
    "cathay pacific": -220,
    "turkish airlines": -160,
    "air new zealand": -150,
}
IST_AKL_AUTO_TOP_PICK: set[str] = set()

# ---------------------------------------------------------------------------
# YVR → VCE (outbound)
# ---------------------------------------------------------------------------
YVR_VCE_BONUSES: dict[str, int] = {
    "air canada": -300,
    "british airways": -180,
    "klm": -180,
    "air france": -180,
    "swiss": -160,
    "swiss international": -160,
    "turkish airlines": -160,
    "lufthansa": -80,
}
YVR_VCE_AUTO_TOP_PICK: set[str] = set()

# ---------------------------------------------------------------------------
# IST → YVR (return)
# ---------------------------------------------------------------------------
IST_YVR_BONUSES: dict[str, int] = {
    "turkish airlines": -280,
    "air canada": -260,
    "british airways": -180,
    "lufthansa": -80,
}
IST_YVR_AUTO_TOP_PICK: set[str] = {"turkish airlines"}

# Backward-compatible aliases used by tests and score_flights()
AIRLINE_BONUSES = LAX_VCE_BONUSES
RETURN_AIRLINE_BONUSES = IST_LAX_BONUSES
AUTO_TOP_PICK_NONSTOP = LAX_VCE_AUTO_TOP_PICK
RETURN_AUTO_TOP_PICK_NONSTOP = IST_LAX_AUTO_TOP_PICK

# ---------------------------------------------------------------------------
# Route definitions
# ---------------------------------------------------------------------------

ROUTES = [
    {
        "origin": "LAX",
        "outbound": {"from": "LAX", "to": "VCE", "dates": DEPARTURE_DATES,
                      "bonuses": LAX_VCE_BONUSES, "auto_top": LAX_VCE_AUTO_TOP_PICK},
        "return": {"from": "IST", "to": "LAX", "dates": RETURN_DATES,
                   "bonuses": IST_LAX_BONUSES, "auto_top": IST_LAX_AUTO_TOP_PICK},
    },
    {
        "origin": "AKL",
        "outbound": {"from": "AKL", "to": "VCE", "dates": DEPARTURE_DATES,
                      "bonuses": AKL_VCE_BONUSES, "auto_top": AKL_VCE_AUTO_TOP_PICK,
                      "max_stops": 2, "min_stops": 1},
        "return": {"from": "IST", "to": "AKL", "dates": RETURN_DATES,
                   "bonuses": IST_AKL_BONUSES, "auto_top": IST_AKL_AUTO_TOP_PICK,
                   "max_stops": 2, "min_stops": 1},
    },
    {
        "origin": "ATL",
        "outbound": {"from": "ATL", "to": "VCE", "dates": DEPARTURE_DATES,
                      "bonuses": ATL_VCE_BONUSES, "auto_top": ATL_VCE_AUTO_TOP_PICK},
        "return": {"from": "IST", "to": "ATL", "dates": RETURN_DATES,
                   "bonuses": IST_ATL_BONUSES, "auto_top": IST_ATL_AUTO_TOP_PICK},
    },
    {
        "origin": "YVR",
        "outbound": {"from": "YVR", "to": "VCE", "dates": DEPARTURE_DATES,
                      "bonuses": YVR_VCE_BONUSES, "auto_top": YVR_VCE_AUTO_TOP_PICK,
                      "min_stops": 1},
        "return": {"from": "IST", "to": "YVR", "dates": RETURN_DATES,
                   "bonuses": IST_YVR_BONUSES, "auto_top": IST_YVR_AUTO_TOP_PICK,
                   "min_stops": 1},
    },
]


def _departure_hour(f: dict) -> int | None:
    """Extract the departure hour (0-23) from a flight."""
    dep = f["departure_time"]
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(dep[:len(fmt.replace("%", "x"))], fmt).hour
        except ValueError:
            continue
    # Last-ditch: look for HH:MM anywhere in the string
    parts = dep.split()
    for part in parts:
        if ":" in part:
            try:
                return int(part.split(":")[0])
            except ValueError:
                pass
    return None


def score_flights(
    flights: list[dict],
    test_mode: bool = False,
    airline_bonuses: dict[str, int] | None = None,
    auto_top_picks: set[str] | None = None,
) -> list[dict]:
    """Assign a score to each flight (lower = better) and return sorted."""
    if airline_bonuses is None:
        airline_bonuses = AIRLINE_BONUSES
    if auto_top_picks is None:
        auto_top_picks = AUTO_TOP_PICK_NONSTOP

    breakdowns = []

    for f in flights:
        primary = f["primary_airline"].lower().strip()

        # Auto TOP PICK: nonstop flights by these airlines get score 0
        if primary in auto_top_picks and f["stops"] == 0:
            f["score"] = 0
            breakdowns.append((f, {
                "price": float(f["price"]), "airline": 0, "time": 0,
                "layover": 0, "speed": 0, "nonstop": 0,
                "total": 0, "auto_top": True,
            }))
            continue

        bd = {}  # breakdown dict for --test mode
        score = float(f["price"])
        bd["price"] = float(f["price"])

        # Airline bonus — use first leg's primary airline
        airline_bonus = airline_bonuses.get(primary, 0)
        score += airline_bonus
        bd["airline"] = airline_bonus

        # Time-of-day bonus: morning 6-10am or evening 5-9pm
        hour = _departure_hour(f)
        time_bonus = -80 if hour is not None and (6 <= hour <= 10 or 17 <= hour <= 21) else 0
        score += time_bonus
        bd["time"] = time_bonus

        # Layover penalty: +0.5 per minute
        layover_penalty = f["total_layover_min"] * 0.5
        score += layover_penalty
        bd["layover"] = round(layover_penalty, 1)

        # Speed bonus: total journey duration tiers
        duration = f["total_duration_min"]
        if duration < 960:        # under 16h
            speed_bonus = -60
        elif duration <= 1080:    # 16-18h
            speed_bonus = -30
        elif duration > 1200:     # over 20h
            speed_bonus = 50
        else:                     # 18-20h: no bonus/penalty
            speed_bonus = 0
        score += speed_bonus
        bd["speed"] = speed_bonus

        # Nonstop bonus
        nonstop_bonus = -100 if f["stops"] == 0 else 0
        score += nonstop_bonus
        bd["nonstop"] = nonstop_bonus

        f["score"] = round(score, 2)
        bd["total"] = f["score"]
        breakdowns.append((f, bd))

    flights.sort(key=lambda f: f["score"])
    print(f"[Score] Top score: {flights[0]['score']}  Worst: {flights[-1]['score']}" if flights else "[Score] No flights")

    if test_mode and breakdowns:
        breakdowns.sort(key=lambda x: x[1]["total"])
        print(f"\n{'SCORE BREAKDOWN':=^110}")
        print(f"  {'Airline':<20} {'Dep Time':<17} {'Price':>6} {'AirBonus':>9} {'Time':>6} "
              f"{'Layover':>8} {'Speed':>6} {'Nonstop':>8} {'TOTAL':>8}")
        print(f"  {'-'*20} {'-'*17} {'-'*6} {'-'*9} {'-'*6} {'-'*8} {'-'*6} {'-'*8} {'-'*8}")
        for f, bd in breakdowns:
            print(f"  {f['primary_airline']:<20} {f['departure_time'][:16]:<17} "
                  f"{bd['price']:>6.0f} {bd['airline']:>+9.0f} {bd['time']:>+6.0f} "
                  f"{bd['layover']:>+8.1f} {bd['speed']:>+6.0f} {bd['nonstop']:>+8.0f} "
                  f"{bd['total']:>8.1f}")
        print(f"{'':=^110}")
        # Google Flights deep links
        print(f"\n{'GOOGLE FLIGHTS DEEP LINKS':=^110}")
        for f, bd in breakdowns:
            url = f.get("google_flights_url", "")
            status = url[:90] + "…" if url else "MISSING"
            print(f"  {f['primary_airline']:<20} {f['departure_time'][:16]:<17} {status}")
        print(f"{'':=^110}\n")

    return flights


# ---------------------------------------------------------------------------
# 4. HTML email builder — design system
# ---------------------------------------------------------------------------
# Fonts : Cormorant Garamond (serif 300/400), DM Sans (300/400/500)
# Palette: ink #0a0a0f, azure #1a3a6b, lagoon #0d6e8a,
#          gold #b8953a, gold-light #d4af6a, fog #eeeef4, paper #f8f8fc

_FONT_LINK = (
    '<link href="https://fonts.googleapis.com/css2?family='
    'Cormorant+Garamond:ital,wght@0,300;0,400;0,600;1,300;1,400&'
    'family=DM+Sans:wght@300;400;500&display=swap" rel="stylesheet">'
)
_SERIF = "'Cormorant Garamond',Georgia,'Times New Roman',serif"
_SANS = "'DM Sans',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif"


def _fmt_time(raw: str) -> str:
    """Extract 'HH:MM' and return '12:30 PM' style."""
    clean = raw.replace("T", " ")[:16]
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(clean, fmt).strftime("%-I:%M %p")
        except ValueError:
            continue
    return raw[:16]


def _fmt_duration(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    return f"{h}h {m}m" if h else f"{m}m"


def _layover_info(f: dict) -> str:
    """Return layover airport + duration string from raw data."""
    if f["stops"] == 0:
        return "Nonstop"

    raw = f.get("raw", {})
    layovers = raw.get("layovers", [])
    if layovers:
        parts = []
        for lo in layovers:
            name = lo.get("name", lo.get("id", ""))
            dur = _fmt_duration(lo.get("duration", 0))
            parts.append(f"{dur} in {name}" if name else dur)
        return " · ".join(parts)

    return f"{f['total_layover_min']}m layover"


def _book_buttons(f: dict) -> str:
    """Two side-by-side pill buttons linking directly to booking.

    Email cannot run JavaScript, so click tracking happens only on the
    web page (via the book modal). Email buttons link straight to Google
    Flights to avoid phantom entries on email open.
    """
    gf_url = f.get("google_flights_url", "")
    if gf_url:
        book_url = gf_url
    else:
        search_date = f.get("search_date", "")
        book_url = f"https://www.google.com/flights#search;f=LAX;t=VCE;d={search_date};tt=o;c=e;s=1"

    book_btn = (
        f'<a href="{book_url}" target="_blank" style="display:inline-block;'
        f"background:#1a3a6b;color:#ffffff;font-family:{_SANS};"
        f'font-size:12px;font-weight:500;text-decoration:none;'
        f'padding:8px 16px;border-radius:9999px;letter-spacing:0.3px;'
        f'margin-right:6px;">'
        f'Select Flight</a>'
    )
    details_btn = (
        f'<a href="{book_url}" target="_blank" style="display:inline-block;'
        f"background:#eeeef4;color:#0a0a0f;font-family:{_SANS};"
        f'font-size:12px;font-weight:500;text-decoration:none;'
        f'padding:8px 16px;border-radius:9999px;letter-spacing:0.3px;">'
        f'More Details</a>'
    )

    # "See who's interested" link — points to web page with flight param
    flight_id = f"{f.get('primary_airline', '')}|{f.get('search_date', '')}|{f.get('price', '')}"
    web_url = f"https://lax-vce-flights.vercel.app/?flight={urllib.parse.quote(flight_id, safe='')}"
    interest_link = (
        f'<br><a href="{web_url}" target="_blank" style="'
        f"font-family:{_SANS};font-size:12px;font-weight:400;"
        f'color:#2a5298;text-decoration:none;">'
        f'See who\'s interested</a>'
    )

    return book_btn + details_btn + interest_link


def _score_badge(f: dict) -> str:
    """Return a badge-styled score label based on thresholds."""
    score = f.get("score", 999)
    if score < 200:
        text = "&#10022; Excellent Choice"
    elif score <= 400:
        text = "Solid Pick"
    else:
        text = "Fair Option"
    return (
        f'<span style="display:inline-block;background:#b8953a;color:#f8f8fc;'
        f"font-family:{_SANS};font-size:10px;font-weight:500;"
        f'padding:2px 8px;border-radius:9999px;letter-spacing:0.4px;'
        f'vertical-align:middle;">{text}</span>'
    )


def _fare_badge(f: dict) -> str:
    """Economy Main badge for all flights."""
    return (
        f'<span style="display:inline-block;background:#eeeef4;color:#0a0a0f;'
        f"font-family:{_SANS};font-size:10px;font-weight:500;"
        f'padding:2px 8px;border-radius:9999px;letter-spacing:0.3px;'
        f'vertical-align:middle;">Economy Main</span>'
    )


def _price_block(f: dict) -> str:
    """Price block — Economy Main as primary, Basic Economy underneath for Big 3."""
    be_price = f.get("basic_economy_price")
    main_est = f.get("economy_main_price")

    # For Big 3: show estimated Main as primary, actual BE underneath
    if be_price and main_est:
        price_html = (
            f'<span style="font-family:{_SERIF};font-size:38px;font-weight:600;'
            f'color:#1a3a6b;letter-spacing:-1px;line-height:1.1;">'
            f'~${main_est:,.0f}</span>'
            f'<br>'
            f'<span style="font-family:{_SANS};font-size:11px;font-weight:300;'
            f'color:#94a3b8;">Est.&nbsp;Main&nbsp;Cabin</span>'
            f'<br>'
            f'<span style="font-family:{_SERIF};font-size:18px;font-weight:300;'
            f'color:#64748b;">${be_price:,.0f}</span>'
            f'&nbsp;<span style="font-family:{_SANS};font-size:11px;font-weight:400;'
            f'color:#b8953a;">Basic Economy</span>'
        )
    else:
        # Non-Big-3: price IS Economy Main
        price_html = (
            f'<span style="font-family:{_SERIF};font-size:38px;font-weight:600;'
            f'color:#1a3a6b;letter-spacing:-1px;line-height:1.1;">'
            f'${f["price"]:,.0f}</span>'
        )

    return price_html


def _flight_card(f: dict, rank: int, is_top_pick: bool) -> str:
    """Render one flight card as an HTML table (email-safe)."""
    border_color = "#d4af6a" if is_top_pick else "#eeeef4"
    border_width = "2px" if is_top_pick else "1px"

    top_badge = (
        f'<span style="display:inline-block;'
        f'background:linear-gradient(135deg,#b8953a,#d4af6a);color:#fff;'
        f"font-family:{_SANS};font-size:10px;font-weight:500;"
        f'padding:3px 12px;border-radius:9999px;letter-spacing:0.4px;'
        f'margin-bottom:8px;">&#11088; TOP PICK</span><br>'
        if is_top_pick else ""
    )

    dep = _fmt_time(f["departure_time"])
    arr = _fmt_time(f["arrival_time"])
    duration = _fmt_duration(f["total_duration_min"])
    stops_label = "Nonstop" if f["stops"] == 0 else f'{f["stops"]} stop'
    layover = _layover_info(f)

    return f"""
    <table width="100%" cellpadding="0" cellspacing="0" border="0"
           style="background:#ffffff;border:{border_width} solid {border_color};
                  border-radius:14px;margin-bottom:14px;border-collapse:separate;">
      <tr><td style="padding:22px 24px;">
        {top_badge}
        <table width="100%" cellpadding="0" cellspacing="0" border="0">
          <tr>
            <td style="vertical-align:top;width:58%;">
              <span style="font-family:{_SANS};font-size:16px;font-weight:500;
                           color:#0a0a0f;">{f["primary_airline"]}</span>
              &nbsp;{_score_badge(f)}
              &nbsp;{_fare_badge(f)}
              <br>
              <span style="font-family:{_SERIF};font-size:26px;font-weight:400;
                           color:#0a0a0f;letter-spacing:-0.5px;line-height:1.7;">
                {dep} &rarr;<br>{arr}</span>
              <br>
              <span style="font-family:{_SANS};font-size:13px;font-weight:300;
                           color:#0d6e8a;">{duration} &middot; {stops_label}</span>
              <br>
              <span style="font-family:{_SANS};font-size:12px;font-weight:300;
                           color:#94a3b8;">{layover}</span>
            </td>
            <td style="vertical-align:top;text-align:right;width:42%;">
              {_price_block(f)}
              <br><br>
              {_book_buttons(f)}
            </td>
          </tr>
        </table>
      </td></tr>
    </table>"""


def _today_pst() -> date:
    """Return today's date in Pacific time."""
    return datetime.now(ZoneInfo("America/Los_Angeles")).date()


def _build_date_sections(flights: list[dict], direction: str = "outbound") -> str:
    """Build the per-date card sections for a list of flights."""
    by_date: dict[str, list[dict]] = defaultdict(list)
    for f in flights:
        by_date[f["search_date"]].append(f)

    top_picks: dict[str, int] = {}
    for dt, group in by_date.items():
        if group:
            top_picks[dt] = id(group[0])

    date_sections = ""
    for dt in sorted(by_date.keys()):
        group = by_date[dt]
        nice_date = datetime.strptime(dt, "%Y-%m-%d").strftime("%A, %B %-d")
        visible_cards = ""
        for i, f in enumerate(group):
            is_top = id(f) == top_picks.get(dt)
            card = _flight_card(f, i + 1, is_top)
            if i < 3:
                visible_cards += card

        show_more = ""
        if len(group) > 3:
            dir_param = f"&dir={direction}" if direction == "return" else ""
            web_url = f"https://lax-vce-flights.vercel.app/?date={dt}{dir_param}"
            show_more = f"""
            <table width="100%" cellpadding="0" cellspacing="0" border="0">
              <tr><td style="padding:4px 0 12px 0;">
                <a href="{web_url}" target="_blank"
                   style="font-family:{_SANS};font-size:13px;font-weight:500;
                          color:#b8953a;text-decoration:none;">
                  View all {len(group)} flights for {nice_date} &rarr;</a>
              </td></tr>
            </table>"""

        date_sections += f"""
        <table width="100%" cellpadding="0" cellspacing="0" border="0"
               style="margin-bottom:4px;">
          <tr><td style="padding:28px 0 10px 0;">
            <span style="font-family:{_SERIF};font-size:24px;font-weight:400;
                         color:#0a0a0f;letter-spacing:-0.3px;">{nice_date}</span>
            <span style="font-family:{_SANS};font-size:13px;font-weight:300;
                         color:#94a3b8;margin-left:10px;">
              {len(group)} flight{"s" if len(group) != 1 else ""}</span>
          </td></tr>
        </table>
        {visible_cards}
        {show_more}
        """

    return date_sections


def build_email_html(
    outbound: list[dict],
    return_flights: list[dict] | None = None,
) -> str:
    """Build the full HTML email body from scored, sorted flights."""
    today = _today_pst()
    days_to_go = (TRIP_DATE - today).days

    outbound_sections = _build_date_sections(outbound, "outbound")

    return_section_html = ""
    if return_flights:
        return_date_sections = _build_date_sections(return_flights, "return")
        return_section_html = f"""
        <!-- Return section divider -->
        <table width="100%" cellpadding="0" cellspacing="0" border="0"
               style="margin-top:40px;margin-bottom:8px;">
          <tr><td style="padding:0;">
            <table width="80" cellpadding="0" cellspacing="0" border="0" align="center">
              <tr><td style="height:1px;background:#b8953a;font-size:1px;line-height:1px;">&nbsp;</td></tr>
            </table>
          </td></tr>
          <tr><td align="center" style="padding:24px 0 4px 0;">
            <span style="font-family:{_SERIF};font-size:32px;font-weight:300;
                         font-style:italic;color:#1a3a6b;letter-spacing:-0.02em;">
              Return Flights &mdash; IST to LAX</span>
          </td></tr>
        </table>
        {return_date_sections}
        """

    total_flights = len(outbound) + len(return_flights or [])

    return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>LAX to VCE Flights</title>
{_FONT_LINK}
</head>
<body style="margin:0;padding:0;background:#f8f8fc;
             font-family:{_SANS};color:#0a0a0f;">

<!-- Hero -->
<table width="100%" cellpadding="0" cellspacing="0" border="0">
  <tr><td align="center" style="background:#faf8f3;padding:100px 24px;">
    <!-- Countdown pill -->
    <span style="display:inline-block;
                 background:rgba(184,149,58,0.06);border:1px solid rgba(184,149,58,0.35);
                 border-radius:9999px;padding:5px 18px;
                 font-family:{_SANS};font-size:10px;font-weight:500;
                 color:#b8953a;letter-spacing:1.5px;text-transform:uppercase;">
      &#128336; {days_to_go} DAYS UNTIL JULY 3, 2026</span>
    <br><br>
    <!-- Headline -->
    <span style="font-family:{_SERIF};font-size:56px;font-weight:300;
                 font-style:italic;color:#1a3a6b;
                 letter-spacing:-0.02em;line-height:1.1;">
      Cruise Bound</span>
    <br>
    <!-- Subtitle -->
    <span style="font-family:{_SERIF};font-size:26px;font-weight:300;
                 font-style:italic;color:#2a5298;line-height:1.5;">
      First stop &mdash; Venice, Italy</span>
    <br><br>
    <!-- Gold rule -->
    <table width="80" cellpadding="0" cellspacing="0" border="0" align="center">
      <tr><td style="height:1px;background:#b8953a;font-size:1px;line-height:1px;">&nbsp;</td></tr>
    </table>
  </td></tr>
</table>

<!-- Body -->
<table width="100%" cellpadding="0" cellspacing="0" border="0">
  <tr><td align="center" style="padding:0 12px;">
    <table width="640" cellpadding="0" cellspacing="0" border="0"
           style="max-width:640px;width:100%;">
      <tr><td style="padding:8px 0 48px 0;">
        {outbound_sections}
        {return_section_html}

        <!-- Footer -->
        <table width="100%" cellpadding="0" cellspacing="0" border="0"
               style="margin-top:36px;border-top:1px solid #eeeef4;">
          <tr><td style="padding:20px 0;text-align:center;">
            <span style="font-family:{_SANS};font-size:11px;font-weight:300;
                         color:#94a3b8;">
              Generated {today.strftime("%B %-d, %Y")} &middot;
              {total_flights} flights after filter &amp; dedup &middot;
              Prices in USD
            </span>
          </td></tr>
        </table>

      </td></tr>
    </table>
  </td></tr>
</table>

</body>
</html>"""


# ---------------------------------------------------------------------------
# 5. JSON export for web dashboard
# ---------------------------------------------------------------------------

def _flight_to_dict(f: dict) -> dict:
    """Convert a scored flight to a JSON-serializable dict."""
    # Extract flight numbers from raw segments
    flight_numbers = []
    raw = f.get("raw", {})
    for seg in raw.get("flights", []):
        fn = seg.get("flight_number", "")
        if fn:
            flight_numbers.append(fn)

    d = {
        "primary_airline": f["primary_airline"],
        "airlines": f["airlines"],
        "flight_numbers": flight_numbers,
        "departure_time": f["departure_time"],
        "arrival_time": f["arrival_time"],
        "stops": f["stops"],
        "total_layover_min": f["total_layover_min"],
        "total_duration_min": f["total_duration_min"],
        "price": f["price"],
        "score": f["score"],
        "search_date": f["search_date"],
        "fare_type": f.get("fare_type", "Economy Main"),
        "economy_main_price": f.get("economy_main_price"),
        "basic_economy_price": f.get("basic_economy_price"),
        "premium_economy_price": f.get("premium_economy_price"),
        "business_price": f.get("business_price"),
        "google_flights_url": f.get("google_flights_url", ""),
        "layover_info": _layover_info(f),
    }
    if f.get("type") == "multi_city":
        d["type"] = "multi_city"
        ret = f["return_leg"]
        d["return_leg"] = {
            "primary_airline": ret["primary_airline"],
            "airlines": ret["airlines"],
            "departure_time": ret["departure_time"],
            "arrival_time": ret["arrival_time"],
            "stops": ret["stops"],
            "total_layover_min": ret["total_layover_min"],
            "total_duration_min": ret["total_duration_min"],
            "layover_info": ret.get("layover_info", ""),
            "search_date": ret.get("search_date", ""),
        }
    return d


def export_flights_json(
    results: dict[str, dict[str, list[dict]]],
) -> None:
    """Write scored flights to web/ and public/ as flights.json (multi-origin)."""
    root = Path(__file__).parent
    today = _today_pst()

    export: dict = {}
    for origin, directions in results.items():
        export[origin] = {}
        for direction, flights in directions.items():
            export[origin][direction] = {
                "generated": today.isoformat(),
                "days_to_go": (TRIP_DATE - today).days,
                "trip_date": TRIP_DATE.isoformat(),
                "flights": [_flight_to_dict(f) for f in flights],
            }

    for dirname in ("web", "public"):
        out_dir = root / dirname
        out_dir.mkdir(exist_ok=True)
        out_path = out_dir / "flights.json"
        with open(out_path, "w") as fp:
            json.dump(export, fp, indent=2)

    total = sum(len(fl) for d in results.values() for fl in d.values())
    print(f"[Export] Wrote {total} flights across {len(results)} origins to web/ and public/")


# ---------------------------------------------------------------------------
# 6. Gmail sender
# ---------------------------------------------------------------------------

def send_email(html: str) -> None:
    """Send the flight report via Gmail SMTP over SSL."""
    today = _today_pst()
    days_to_go = (TRIP_DATE - today).days

    subject = (
        f"\u2708\ufe0f LAX\u2192VCE \u00b7 {days_to_go} days to go | "
        f"{today.strftime('%B %-d, %Y')}"
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = GMAIL_USER
    msg["To"] = TO_ADDRESS

    plain = (
        f"LAX → VCE flight report – {days_to_go} days to go.\n"
        "View this email in an HTML-capable client for the full report."
    )
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        smtp.sendmail(GMAIL_USER, TO_ADDRESS, msg.as_string())

    print(f"[Email] Sent to {TO_ADDRESS} — \"{subject}\"")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _print_summary(label: str, flights: list[dict]) -> None:
    """Print a summary table for a flight list."""
    print(f"\n{'='*70}")
    print(f"  {label}")
    print(f"{'='*70}")
    for i, f in enumerate(flights[:10], 1):
        be = f.get("basic_economy_price")
        main = f.get("economy_main_price")
        price_str = f"${f['price']}" if not main else f"~${main} (BE ${be})"
        print(
            f"  {i}. {f['primary_airline']:<20} "
            f"dep {f['departure_time'][:16]:<17} "
            f"stops={f['stops']}  "
            f"layover={f['total_layover_min']}m  "
            f"{price_str:<22} "
            f"score={f['score']}"
        )
    print(f"{'='*70}")


if __name__ == "__main__":
    if not SERPAPI_KEY:
        print("Error: SERPAPI_KEY not set")
        raise SystemExit(1)

    test_mode = "--test" in sys.argv
    all_results: dict[str, dict[str, list]] = {}

    for route in ROUTES:
        origin = route["origin"]
        all_results[origin] = {}

        # --- Outbound ---
        out_cfg = route["outbound"]
        out_ms = out_cfg.get("max_stops", 1)
        out_mins = out_cfg.get("min_stops", 0)
        raw_out = search_serpapi(out_cfg["from"], out_cfg["to"], out_cfg["dates"],
                                 max_stops=out_ms, min_stops=out_mins)
        outbound = normalize(raw_out)
        outbound = filter_flights(outbound, max_stops=out_ms)
        outbound = dedup_flights(outbound)
        outbound = label_fare_types(outbound)
        outbound = score_flights(
            outbound, test_mode=test_mode,
            airline_bonuses=out_cfg["bonuses"],
            auto_top_picks=out_cfg["auto_top"],
        )
        all_results[origin]["outbound"] = outbound
        _print_summary(f"OUTBOUND — {out_cfg['from']} → {out_cfg['to']}", outbound)

        # --- Return ---
        ret_cfg = route["return"]
        ret_ms = ret_cfg.get("max_stops", 1)
        ret_mins = ret_cfg.get("min_stops", 0)
        raw_ret = search_serpapi(ret_cfg["from"], ret_cfg["to"], ret_cfg["dates"],
                                 max_stops=ret_ms, min_stops=ret_mins)
        if origin == "LAX":
            raw_ret += search_skyscanner(ret_cfg["from"], ret_cfg["to"], ret_cfg["dates"])
        ret = normalize(raw_ret)
        ret = filter_flights(ret, max_stops=ret_ms)
        ret = dedup_flights(ret)
        ret = label_fare_types(ret)
        ret = score_flights(
            ret, test_mode=test_mode,
            airline_bonuses=ret_cfg["bonuses"],
            auto_top_picks=ret_cfg["auto_top"],
        )
        all_results[origin]["return"] = ret
        _print_summary(f"RETURN — {ret_cfg['from']} → {ret_cfg['to']}", ret)

    # Export JSON for web dashboard
    export_flights_json(all_results)

    # Build and send email (LAX only)
    lax_out = all_results.get("LAX", {}).get("outbound", [])
    lax_ret = all_results.get("LAX", {}).get("return", [])
    if lax_out or lax_ret:
        html = build_email_html(lax_out, lax_ret)
        if GMAIL_USER and GMAIL_APP_PASSWORD:
            send_email(html)
        else:
            print("Warning: GMAIL_USER / GMAIL_APP_PASSWORD not set – skipping send")
            with open("report.html", "w") as fp:
                fp.write(html)
            print("Wrote report.html for local preview")

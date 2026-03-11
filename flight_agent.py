"""
Flight search module: LAX → VCE via SerpAPI (Google Flights).
"""

import base64
import json
import os
import smtplib
import sys

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

DEPARTURE_DATES = ["2026-06-29", "2026-06-30", "2026-07-01"]


# ---------------------------------------------------------------------------
# Source – Google Flights via SerpAPI (nonstop + 1-stop per date)
# ---------------------------------------------------------------------------

def search_serpapi() -> list[dict]:
    """Query Google Flights for each departure date with two calls each:
    one for nonstop only (stops=1) and one for max 1 stop (stops=2).
    Merge all raw results and return them for downstream dedup."""
    all_results: list[dict] = []

    for dep_date in DEPARTURE_DATES:
        for stops_param in ("1", "2"):  # 1=nonstop, 2=1 stop or fewer
            params = {
                "engine": "google_flights",
                "api_key": SERPAPI_KEY,
                "departure_id": "LAX",
                "arrival_id": "VCE",
                "outbound_date": dep_date,
                "type": "2",            # one-way
                "travel_class": "1",    # economy
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
            for flight in flights:
                flight["_source"] = "serpapi"
                flight["_search_date"] = dep_date
            all_results.extend(flights)

    print(f"[SerpAPI] Google Flights returned {len(all_results)} results "
          f"({len(DEPARTURE_DATES)} dates × 2 queries)")
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


def normalize(raw_results: list[dict]) -> list[dict]:
    """Convert raw SerpAPI results into a unified list."""
    return [_normalize_serpapi(f) for f in raw_results]


# ---------------------------------------------------------------------------
# Fare-type labelling
# ---------------------------------------------------------------------------

BASIC_ECONOMY_CARRIERS = {"american", "delta", "united"}
BASIC_TO_MAIN_ADDER = 100  # Economy Main ≈ Basic Economy + $100 for intl flights


def label_fare_types(flights: list[dict]) -> list[dict]:
    """Label fare types and estimate Economy Main pricing.

    Google Flights always shows the cheapest fare. For the US Big 3
    (American, Delta, United) this is Basic Economy. We estimate
    Economy Main by adding a flat $100 — typical for international
    routes — and show it as the primary price, with the actual Basic
    Economy price shown underneath.

    Non-Big-3 carriers' base fare is Economy Main (standard cabin).
    """
    for f in flights:
        carrier = f["primary_airline"].lower().strip()
        if carrier in BASIC_ECONOMY_CARRIERS:
            f["fare_type"] = "Economy Main"
            f["basic_economy_price"] = f["price"]
            f["economy_main_price"] = f["price"] + BASIC_TO_MAIN_ADDER
        else:
            f["fare_type"] = "Economy Main"
            f["basic_economy_price"] = None
            f["economy_main_price"] = None

    big3 = sum(1 for f in flights if f.get("basic_economy_price"))
    print(f"[Fare] {big3} Big 3 (BE→Main est.), {len(flights) - big3} Economy Main")
    return flights


# ---------------------------------------------------------------------------
# 1. Filter
# ---------------------------------------------------------------------------

BLOCKED_AIRLINES = {
    "spirit", "frontier", "allegiant", "sun country",
    "ryanair", "easyjet", "wizz air", "norwegian",
    "vueling", "transavia", "volotea",
}


def filter_flights(flights: list[dict]) -> list[dict]:
    """Remove budget carriers and anything with more than 1 stop."""
    kept: list[dict] = []
    for f in flights:
        # Check stop count
        if f["stops"] > 1:
            continue
        # Check all airlines on the itinerary
        if any(a.lower() in BLOCKED_AIRLINES for a in f["airlines"]):
            continue
        kept.append(f)

    removed = len(flights) - len(kept)
    print(f"[Filter] Kept {len(kept)} flights, removed {removed}")
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

AIRLINE_BONUSES: dict[str, int] = {
    "delta": -300,
    "united": -220,
    "british airways": -180,
    "american": -180,
    "air france": -160,
    "lufthansa": -160,
    "klm": -160,
    "ita airways": -120,
    "alaska": -120,
}



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


def score_flights(flights: list[dict], test_mode: bool = False) -> list[dict]:
    """Assign a score to each flight (lower = better) and return sorted."""
    breakdowns = []

    for f in flights:
        bd = {}  # breakdown dict for --test mode
        score = float(f["price"])
        bd["price"] = float(f["price"])

        # Airline bonus — use first leg's primary airline
        primary = f["primary_airline"].lower().strip()
        airline_bonus = AIRLINE_BONUSES.get(primary, 0)
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
    'Cormorant+Garamond:ital,wght@0,300;0,400;1,300;1,400&'
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
        f'Book Now</a>'
    )
    details_btn = (
        f'<a href="{book_url}" target="_blank" style="display:inline-block;'
        f"background:#eeeef4;color:#0a0a0f;font-family:{_SANS};"
        f'font-size:12px;font-weight:500;text-decoration:none;'
        f'padding:8px 16px;border-radius:9999px;letter-spacing:0.3px;">'
        f'More Details</a>'
    )
    return book_btn + details_btn


def _source_badge(f: dict) -> str:
    return (
        f'<span style="display:inline-block;background:#1a3a6b;color:#eeeef4;'
        f"font-family:{_SANS};font-size:10px;font-weight:500;"
        f'padding:2px 8px;border-radius:9999px;letter-spacing:0.4px;'
        f'vertical-align:middle;">Google Flights</span>'
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
            f'<span style="font-family:{_SERIF};font-size:40px;font-weight:400;'
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
            f'<span style="font-family:{_SERIF};font-size:40px;font-weight:400;'
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
              &nbsp;{_source_badge(f)}
              &nbsp;{_fare_badge(f)}
              <br>
              <span style="font-family:{_SERIF};font-size:26px;font-weight:400;
                           color:#0a0a0f;letter-spacing:-0.5px;line-height:1.7;">
                {dep} &rarr; {arr}</span>
              <br>
              <span style="font-family:{_SANS};font-size:13px;font-weight:300;
                           color:#0d6e8a;">{duration} &middot; {stops_label}</span>
              <br>
              <span style="font-family:{_SANS};font-size:12px;font-weight:300;
                           color:#94a3b8;">{layover}</span>
            </td>
            <td style="vertical-align:top;text-align:right;width:42%;">
              {_price_block(f)}
              <br>
              <span style="font-family:{_SANS};font-size:11px;font-weight:300;
                           color:#94a3b8;">score {f.get("score", "")}</span>
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


def build_email_html(flights: list[dict]) -> str:
    """Build the full HTML email body from scored, sorted flights."""
    today = _today_pst()
    days_to_go = (TRIP_DATE - today).days

    # Group by search_date
    by_date: dict[str, list[dict]] = defaultdict(list)
    for f in flights:
        by_date[f["search_date"]].append(f)

    # Per-date top pick = rank 1 within that date
    top_picks: dict[str, int] = {}
    for dt, group in by_date.items():
        if group:
            top_picks[dt] = id(group[0])

    # --- Date sections ---
    date_sections = ""
    for dt_idx, dt in enumerate(sorted(by_date.keys())):
        group = by_date[dt]
        nice_date = datetime.strptime(dt, "%Y-%m-%d").strftime("%A, %B %-d")
        visible_cards = ""
        collapsed_cards = ""
        for i, f in enumerate(group):
            is_top = id(f) == top_picks.get(dt)
            card = _flight_card(f, i + 1, is_top)
            if i < 3:
                visible_cards += card
            else:
                collapsed_cards += card

        show_more = ""
        if len(group) > 3:
            web_url = f"https://lax-vce-flights.vercel.app/?date={dt}"
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
        {date_sections}

        <!-- Footer -->
        <table width="100%" cellpadding="0" cellspacing="0" border="0"
               style="margin-top:36px;border-top:1px solid #eeeef4;">
          <tr><td style="padding:20px 0;text-align:center;">
            <span style="font-family:{_SANS};font-size:11px;font-weight:300;
                         color:#94a3b8;">
              Generated {today.strftime("%B %-d, %Y")} &middot;
              {len(flights)} flights after filter &amp; dedup &middot;
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

def export_flights_json(flights: list[dict]) -> None:
    """Write scored flights to web/ and public/ as flights.json."""
    root = Path(__file__).parent

    today = _today_pst()
    export = {
        "generated": today.isoformat(),
        "days_to_go": (TRIP_DATE - today).days,
        "trip_date": TRIP_DATE.isoformat(),
        "flights": [
            {
                "primary_airline": f["primary_airline"],
                "airlines": f["airlines"],
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
                "google_flights_url": f.get("google_flights_url", ""),
                "layover_info": _layover_info(f),
            }
            for f in flights
        ],
    }

    for dirname in ("web", "public"):
        out_dir = root / dirname
        out_dir.mkdir(exist_ok=True)
        out_path = out_dir / "flights.json"
        with open(out_path, "w") as fp:
            json.dump(export, fp, indent=2)

    print(f"[Export] Wrote {len(flights)} flights to web/ and public/")


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

if __name__ == "__main__":
    if not SERPAPI_KEY:
        print("Error: SERPAPI_KEY not set")
        raise SystemExit(1)

    raw = search_serpapi()
    flights = normalize(raw)
    flights = filter_flights(flights)
    flights = dedup_flights(flights)
    flights = label_fare_types(flights)
    test_mode = "--test" in sys.argv
    flights = score_flights(flights, test_mode=test_mode)

    print(f"\n{'='*70}")
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

    # Export JSON for web dashboard
    if flights:
        export_flights_json(flights)

    # Build and send email
    if flights:
        html = build_email_html(flights)
        if GMAIL_USER and GMAIL_APP_PASSWORD:
            send_email(html)
        else:
            print("Warning: GMAIL_USER / GMAIL_APP_PASSWORD not set – skipping send")
            with open("report.html", "w") as fp:
                fp.write(html)
            print("Wrote report.html for local preview")

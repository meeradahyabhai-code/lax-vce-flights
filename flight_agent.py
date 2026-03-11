"""
Flight search module: LAX → VCE via SerpAPI (Google Flights).
"""

import json
import os
import smtplib

from dotenv import load_dotenv

load_dotenv()
from collections import defaultdict
from datetime import date, datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

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
        "raw": flight,
    }


def normalize(raw_results: list[dict]) -> list[dict]:
    """Convert raw SerpAPI results into a unified list."""
    return [_normalize_serpapi(f) for f in raw_results]


# ---------------------------------------------------------------------------
# Fare-type labelling
# ---------------------------------------------------------------------------

BASIC_ECONOMY_CARRIERS = {"american", "delta", "united"}
BASIC_ECONOMY_MARKUP = 0.08  # estimated Economy Main = Basic + 8%


def label_fare_types(flights: list[dict]) -> list[dict]:
    """Label each flight as Basic Economy or Economy Main.

    Heuristic: US Big 3 carriers sell Basic Economy as their cheapest fare.
    For each airline + date, the lowest price is tagged Basic Economy;
    higher-priced flights from the same airline on the same date are Economy Main.
    Non-Big-3 carriers are always Economy Main.
    """
    # Find the minimum price per (airline, date) for Big 3 carriers
    min_price: dict[tuple[str, str], float] = {}
    for f in flights:
        carrier = f["primary_airline"].lower().strip()
        if carrier not in BASIC_ECONOMY_CARRIERS:
            continue
        key = (carrier, f["search_date"])
        if key not in min_price or f["price"] < min_price[key]:
            min_price[key] = f["price"]

    for f in flights:
        carrier = f["primary_airline"].lower().strip()
        key = (carrier, f["search_date"])

        if carrier in BASIC_ECONOMY_CARRIERS and f["price"] == min_price.get(key):
            f["fare_type"] = "Basic Economy"
            f["economy_main_price"] = round(f["price"] * (1 + BASIC_ECONOMY_MARKUP))
        else:
            f["fare_type"] = "Economy Main"
            f["economy_main_price"] = None

    basic = sum(1 for f in flights if f["fare_type"] == "Basic Economy")
    print(f"[Fare] {basic} Basic Economy, {len(flights) - basic} Economy Main")
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
    "delta": -250,
    "united": -220,
    "british airways": -180,
    "american": -180,
    "air france": -160,
    "lufthansa": -160,
    "klm": -160,
    "ita airways": -120,
    "alaska": -120,
}

LONG_FLIGHT_THRESHOLD = 840  # 14 hours in minutes


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


def score_flights(flights: list[dict]) -> list[dict]:
    """Assign a score to each flight (lower = better) and return sorted."""
    for f in flights:
        score = float(f["price"])

        # Airline bonus — use first leg's primary airline
        primary = f["primary_airline"].lower().strip()
        score += AIRLINE_BONUSES.get(primary, 0)

        # Time-of-day bonus: morning 6-10am or evening 6-9pm
        hour = _departure_hour(f)
        if hour is not None and (6 <= hour <= 10 or 18 <= hour <= 21):
            score -= 80

        # Layover penalty: +0.5 per minute
        score += f["total_layover_min"] * 0.5

        # Long-flight penalty: +0.3 per minute over 14h
        duration = f["total_duration_min"]
        if duration > LONG_FLIGHT_THRESHOLD:
            score += (duration - LONG_FLIGHT_THRESHOLD) * 0.3

        f["score"] = round(score, 2)

    flights.sort(key=lambda f: f["score"])
    print(f"[Score] Top score: {flights[0]['score']}  Worst: {flights[-1]['score']}" if flights else "[Score] No flights")
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


def _book_button(f: dict) -> str:
    """Dark 'Book' button linking to Google Flights + Skyscanner."""
    search_date = f.get("search_date", "")
    sky_date = search_date[2:].replace("-", "") if search_date else ""

    gf_url = (
        f"https://www.google.com/flights#search;"
        f"f=LAX;t=VCE;d={search_date};tt=o"
    )
    sky_url = (
        f"https://www.skyscanner.com/transport/flights/lax/vce/{sky_date}/"
    )

    btn = (
        f'<a href="{gf_url}" target="_blank" style="display:inline-block;'
        f"background:#0a0a0f;color:#f8f8fc;font-family:{_SANS};"
        f'font-size:13px;font-weight:500;text-decoration:none;'
        f'padding:8px 18px;border-radius:8px;letter-spacing:0.3px;'
        f'margin-right:8px;">Book &rarr;</a>'
    )
    link = (
        f'<a href="{sky_url}" target="_blank" style="'
        f"font-family:{_SANS};font-size:12px;font-weight:400;"
        f'color:#0d6e8a;text-decoration:none;">Skyscanner</a>'
    )
    return btn + link


def _source_badge(f: dict) -> str:
    return (
        f'<span style="display:inline-block;background:#1a3a6b;color:#eeeef4;'
        f"font-family:{_SANS};font-size:10px;font-weight:500;"
        f'padding:2px 8px;border-radius:9999px;letter-spacing:0.4px;'
        f'vertical-align:middle;">Google Flights</span>'
    )


def _fare_badge(f: dict) -> str:
    """Amber pill for Basic Economy, muted fog pill for Economy Main."""
    if f.get("fare_type") == "Basic Economy":
        return (
            f'<span style="display:inline-block;background:#b8953a;color:#fff;'
            f"font-family:{_SANS};font-size:10px;font-weight:500;"
            f'padding:2px 8px;border-radius:9999px;letter-spacing:0.3px;'
            f'vertical-align:middle;">Basic Economy</span>'
        )
    return (
        f'<span style="display:inline-block;background:#eeeef4;color:#0a0a0f;'
        f"font-family:{_SANS};font-size:10px;font-weight:500;"
        f'padding:2px 8px;border-radius:9999px;letter-spacing:0.3px;'
        f'vertical-align:middle;">Economy Main</span>'
    )


def _price_block(f: dict) -> str:
    """Price in Cormorant Garamond 36px, with est. main line for Basic."""
    is_basic = f.get("fare_type") == "Basic Economy"
    main_price = f.get("economy_main_price")

    price_html = (
        f'<span style="font-family:{_SERIF};font-size:36px;font-weight:400;'
        f'color:#0a0a0f;letter-spacing:-1px;line-height:1.1;">'
        f'${f["price"]:,.0f}</span>'
    )

    if is_basic and main_price:
        price_html += (
            f'<br>'
            f'<span style="font-family:{_SANS};font-size:12px;font-weight:400;'
            f'color:#b8953a;">Basic Economy</span>'
            f'<br>'
            f'<span style="font-family:{_SERIF};font-size:18px;font-weight:300;'
            f'color:#64748b;">~${main_price:,.0f}</span>'
            f'&nbsp;<span style="font-family:{_SANS};font-size:11px;'
            f'font-weight:300;color:#94a3b8;">Est.&nbsp;Main</span>'
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
              {_book_button(f)}
            </td>
          </tr>
        </table>
      </td></tr>
    </table>"""


def build_email_html(flights: list[dict]) -> str:
    """Build the full HTML email body from scored, sorted flights."""
    today = date.today()
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

        # Gmail-compatible toggle using checkbox + label + CSS sibling selector
        show_more = ""
        if collapsed_cards:
            remaining = len(group) - 3
            plural = "s" if remaining != 1 else ""
            cb_id = f"toggle-{dt_idx}"
            show_more = f"""
            <style>
              #{cb_id}:checked ~ .more-{dt_idx} {{
                display: block !important;
              }}
              #{cb_id}:checked ~ .lbl-{dt_idx} .show-txt {{
                display: none !important;
              }}
              #{cb_id}:checked ~ .lbl-{dt_idx} .hide-txt {{
                display: inline !important;
              }}
            </style>
            <input type="checkbox" id="{cb_id}"
                   style="display:none !important;max-height:0;visibility:hidden;">
            <label for="{cb_id}" class="lbl-{dt_idx}"
                   style="display:inline-block;cursor:pointer;color:#1a3a6b;
                          font-family:{_SANS};font-weight:500;font-size:13px;
                          padding:10px 0;">
              <span class="show-txt"
                    style="display:inline;">Show {remaining} more flight{plural} &#9660;</span>
              <span class="hide-txt"
                    style="display:none;">Hide extra flights &#9650;</span>
            </label>
            <div class="more-{dt_idx}"
                 style="display:none;">
              {collapsed_cards}
            </div>"""

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
  <tr><td align="center"
          style="background:linear-gradient(160deg,#0a0a2e 0%,#1a3a6b 45%,#0d6e8a 100%);
                 padding:56px 24px 48px 24px;">
    <span style="font-size:40px;line-height:1;">&#128674;</span>
    <br>
    <span style="font-family:{_SERIF};font-size:42px;font-weight:300;
                 font-style:italic;color:#ffffff;
                 letter-spacing:-0.5px;line-height:1.3;">
      Venice Bound</span>
    <br>
    <span style="font-family:{_SERIF};font-size:56px;font-weight:400;
                 color:#d4af6a;letter-spacing:-2px;line-height:1.4;">
      {days_to_go} days</span>
    <span style="font-family:{_SANS};font-size:18px;font-weight:300;
                 color:#d4af6a;">&nbsp;&middot;&nbsp;July 3, 2026</span>
    <br><br>
    <span style="display:inline-block;
                 background:rgba(255,255,255,0.08);
                 border:1px solid rgba(255,255,255,0.15);
                 border-radius:9999px;padding:7px 22px;
                 font-family:{_SANS};font-size:12px;font-weight:400;
                 color:rgba(255,255,255,0.7);letter-spacing:0.6px;">
      LAX &rarr; VCE &nbsp;&middot;&nbsp; Economy &nbsp;&middot;&nbsp; Max 1 Stop
    </span>
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

    today = date.today()
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
    today = date.today()
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
    flights = score_flights(flights)

    print(f"\n{'='*70}")
    for i, f in enumerate(flights[:10], 1):
        fare = "BE" if f["fare_type"] == "Basic Economy" else "EM"
        main = f"→${f['economy_main_price']}" if f["economy_main_price"] else ""
        print(
            f"  {i}. {f['primary_airline']:<20} "
            f"dep {f['departure_time'][:16]:<17} "
            f"stops={f['stops']}  "
            f"layover={f['total_layover_min']}m  "
            f"${f['price']:<6} {fare}{main:<10} "
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

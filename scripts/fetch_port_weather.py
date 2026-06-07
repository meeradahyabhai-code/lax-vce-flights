#!/usr/bin/env python3
"""
Build data/port_climate.json for each cruise port-day.

Hybrid source, chosen per port-day based on how far out it is from "today":
  - Inside the ~16-day forecast window  -> Open-Meteo FORECAST API (real-time).
  - Beyond the window                   -> 10-year July climate NORMALS
                                           (Open-Meteo archive API).

This means the dashboard automatically switches each port-day from "typical
July weather" to a real forecast as the trip approaches, with no manual flip.
Run daily (see .github/workflows/refresh-weather.yml) so forecasts stay fresh.

Both APIs are free and need no key.

Output schema (keyed by ISO date in the cruise itinerary):
  {
    "2026-07-03": {"high": 82, "low": 71, "rain_chance": 8,
                   "icon": "sun", "source": "normal"},
    ...
  }
  source is "forecast" (live) or "normal" (climate average).
"""
import json
import sys
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_PATHS = [
    ROOT / "data" / "port_climate.json",
    ROOT / "public" / "port_climate.json",
    ROOT / "web" / "port_climate.json",
]

# (iso_date, lat, lon) for each port-day in the 2026 itinerary that has a coord.
# "At sea" days are skipped — no port to query.
PORTS = [
    ("2026-07-03", 44.4926, 12.2502),  # Ravenna
    ("2026-07-04", 42.6507, 18.0944),  # Dubrovnik
    ("2026-07-05", 42.0931, 19.0904),  # Bar
    ("2026-07-07", 37.9420, 23.6464),  # Athens (Piraeus)
    ("2026-07-08", 37.8579, 27.2610),  # Kusadasi
    ("2026-07-09", 36.4516, 28.2275),  # Rhodes
    ("2026-07-10", 36.3804, 25.4309),  # Santorini
    ("2026-07-12", 41.0082, 28.9784),  # Istanbul
    ("2026-07-13", 41.0082, 28.9784),  # Istanbul (disembark)
]

YEARS = list(range(2015, 2025))  # 2015-2024 inclusive, for climate normals
RAIN_THRESHOLD_MM = 1.0  # a past day counts as "wet" if precip >= this
ICON_RAIN_PCT = 50
ICON_CLOUD_PCT = 20

# Open-Meteo forecast horizon. The API serves up to 16 days; stay just inside.
FORECAST_WINDOW_DAYS = 15
# Stop doing anything once the trip is over (the action keeps the last data).
LAST_TRIP_DAY = date(2026, 7, 14)


def _icon_for(rain_pct: int) -> str:
    if rain_pct >= ICON_RAIN_PCT:
        return "rain"
    if rain_pct >= ICON_CLOUD_PCT:
        return "cloud"
    return "sun"


def _get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.load(r)


def fetch_port_normal(lat: float, lon: float, month: int, day: int) -> dict:
    """10-year average for this calendar day (climate normal)."""
    url = (
        "https://archive-api.open-meteo.com/v1/archive?"
        + urllib.parse.urlencode({
            "latitude": lat,
            "longitude": lon,
            "start_date": f"{YEARS[0]}-{month:02d}-{day:02d}",
            "end_date": f"{YEARS[-1]}-{month:02d}-{day:02d}",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum",
            "temperature_unit": "fahrenheit",
            "timezone": "auto",
        })
    )
    payload = _get_json(url)
    dates = payload["daily"]["time"]
    tmax = payload["daily"]["temperature_2m_max"]
    tmin = payload["daily"]["temperature_2m_min"]
    precip = payload["daily"]["precipitation_sum"]

    highs, lows, wet_days, total = [], [], 0, 0
    for iso, hi, lo, pr in zip(dates, tmax, tmin, precip):
        _, mo, dy = iso.split("-")
        if int(mo) != month or int(dy) != day:
            continue
        if hi is None or lo is None:
            continue
        highs.append(hi)
        lows.append(lo)
        if pr is not None and pr >= RAIN_THRESHOLD_MM:
            wet_days += 1
        total += 1

    if not highs:
        raise RuntimeError(f"No archive data for {lat},{lon} on {month:02d}-{day:02d}")

    rain_pct = round(100 * wet_days / total)
    return {
        "high": round(sum(highs) / len(highs)),
        "low": round(sum(lows) / len(lows)),
        "rain_chance": rain_pct,
        "icon": _icon_for(rain_pct),
        "source": "normal",
    }


def fetch_port_forecast(lat: float, lon: float, iso_date: str) -> dict | None:
    """Real forecast for iso_date, or None if it isn't in the returned window."""
    url = (
        "https://api.open-meteo.com/v1/forecast?"
        + urllib.parse.urlencode({
            "latitude": lat,
            "longitude": lon,
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
            "temperature_unit": "fahrenheit",
            "timezone": "auto",
            "forecast_days": 16,
        })
    )
    payload = _get_json(url)
    daily = payload.get("daily", {})
    times = daily.get("time", [])
    if iso_date not in times:
        return None
    i = times.index(iso_date)
    hi = daily["temperature_2m_max"][i]
    lo = daily["temperature_2m_min"][i]
    pop = daily.get("precipitation_probability_max", [None] * len(times))[i]
    if hi is None or lo is None:
        return None
    rain_pct = int(round(pop)) if pop is not None else 0
    return {
        "high": round(hi),
        "low": round(lo),
        "rain_chance": rain_pct,
        "icon": _icon_for(rain_pct),
        "source": "forecast",
    }


def build(today: date) -> dict:
    out: dict[str, dict] = {}
    for iso_date, lat, lon in PORTS:
        target = date.fromisoformat(iso_date)
        delta = (target - today).days
        data = None
        if 0 <= delta <= FORECAST_WINDOW_DAYS:
            try:
                data = fetch_port_forecast(lat, lon, iso_date)
            except Exception as e:
                print(f"forecast failed {iso_date} ({lat},{lon}): {e}", file=sys.stderr)
        if data is None:
            mo, dy = target.month, target.day
            data = fetch_port_normal(lat, lon, mo, dy)
        out[iso_date] = data
        print(f"{iso_date}  {data['high']:>3}/{data['low']:<3}  "
              f"rain {data['rain_chance']:>2}%  {data['icon']:<5}  {data['source']}")
    return out


def main() -> int:
    today = date.today()
    if today > LAST_TRIP_DAY:
        print(f"{today} is past the trip ({LAST_TRIP_DAY}); leaving data as-is.")
        return 0

    try:
        out = build(today)
    except Exception as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return 1

    payload = json.dumps(out, indent=2) + "\n"
    for p in OUT_PATHS:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(payload)
        print(f"wrote {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

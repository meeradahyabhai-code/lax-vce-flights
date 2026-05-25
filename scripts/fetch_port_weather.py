#!/usr/bin/env python3
"""
Fetch climate normals for each cruise port and write data/port_climate.json.

Source: Open-Meteo archive API (free, no key). Pulls daily max/min temp and
precipitation for July 3-13 across the past 10 years, averages them.

Output schema (keyed by ISO date in the cruise itinerary):
  {
    "2026-07-03": {"high": 82, "low": 71, "rain_chance": 8, "icon": "sun"},
    ...
  }
"""
import json
import sys
import urllib.parse
import urllib.request
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

YEARS = list(range(2015, 2025))  # 2015-2024 inclusive
RAIN_THRESHOLD_MM = 1.0  # day counts as "wet" if precip >= this
ICON_RAIN_PCT = 50
ICON_CLOUD_PCT = 20


def fetch_port(lat: float, lon: float, mm_dd: tuple[int, int]) -> dict:
    """Pull all daily values for the cruise day across YEARS, return averages."""
    month, day = mm_dd
    highs, lows, wet_days = [], [], 0
    total_days = 0

    # One call per port covering all years; filter to the exact day Python-side.
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
    with urllib.request.urlopen(url, timeout=30) as r:
        payload = json.load(r)

    dates = payload["daily"]["time"]
    tmax = payload["daily"]["temperature_2m_max"]
    tmin = payload["daily"]["temperature_2m_min"]
    precip = payload["daily"]["precipitation_sum"]

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
        total_days += 1

    if not highs:
        raise RuntimeError(f"No data for {lat},{lon} on {month:02d}-{day:02d}")

    rain_pct = round(100 * wet_days / total_days)
    return {
        "high": round(sum(highs) / len(highs)),
        "low": round(sum(lows) / len(lows)),
        "rain_chance": rain_pct,
        "icon": "rain" if rain_pct >= ICON_RAIN_PCT else ("cloud" if rain_pct >= ICON_CLOUD_PCT else "sun"),
    }


def main() -> int:
    out: dict[str, dict] = {}
    for iso_date, lat, lon in PORTS:
        _, mo, dy = iso_date.split("-")
        try:
            out[iso_date] = fetch_port(lat, lon, (int(mo), int(dy)))
        except Exception as e:
            print(f"FAIL {iso_date} ({lat},{lon}): {e}", file=sys.stderr)
            return 1
        d = out[iso_date]
        print(f"{iso_date}  {d['high']:>3}/{d['low']:<3}  rain {d['rain_chance']:>2}%  {d['icon']}")

    payload = json.dumps(out, indent=2) + "\n"
    for p in OUT_PATHS:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(payload)
        print(f"wrote {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

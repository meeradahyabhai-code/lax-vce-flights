#!/usr/bin/env python3
"""
Refresh flight data from SerpAPI and save to data/flights_cache.json.

This script costs 42 SerpAPI calls. Safe to run on a 48h schedule (automated).
For ad-hoc runs, get user approval first.

Usage:
    python3 scripts/refresh_flights.py
    python3 scripts/refresh_flights.py --force   # skip cooldown check
"""

import json
import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from zoneinfo import ZoneInfo

# Allow imports from project root
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from flight_agent import (
    ROUTES,
    TRIP_DATE,
    _flight_to_dict,
    _today_pst,
    dedup_flights,
    filter_flights,
    get_serpapi_call_log,
    label_fare_types,
    normalize,
    reset_serpapi_call_log,
    search_serpapi,
    search_skyscanner,
)

OUTPUT_DIR = os.path.join(ROOT, "data")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "flights_cache.json")

# Safeguards
EXPECTED_ORIGINS = {"LAX", "AKL", "ATL", "YVR"}
EXPECTED_CALL_COUNT = 42
MIN_FLIGHTS_PER_DIRECTION = 3
COOLDOWN_HOURS = 47  # refuse to run again within this window


def _check_cooldown(force: bool) -> None:
    """Refuse to run if last refresh was less than COOLDOWN_HOURS ago."""
    if force:
        return
    if not os.path.exists(OUTPUT_FILE):
        return
    try:
        with open(OUTPUT_FILE, "r") as f:
            data = json.load(f)
        refreshed = data.get("_serpapi_usage", {}).get("refreshed_at")
        if refreshed:
            last = datetime.fromisoformat(refreshed)
            now = datetime.now(ZoneInfo("America/Los_Angeles"))
            hours_ago = (now - last).total_seconds() / 3600
            if hours_ago < COOLDOWN_HOURS:
                print(f"BLOCKED: Last refresh was {hours_ago:.1f}h ago (cooldown: {COOLDOWN_HOURS}h).")
                print(f"Use --force to override.")
                sys.exit(1)
    except (json.JSONDecodeError, KeyError, ValueError):
        pass  # corrupt file, allow refresh


def _run_direction(origin, direction, cfg, today):
    """Run the pipeline for one origin+direction."""
    ms = cfg.get("max_stops", 1)
    mins = cfg.get("min_stops", 0)
    raw = search_serpapi(cfg["from"], cfg["to"], cfg["dates"], max_stops=ms, min_stops=mins)
    if origin == "LAX" and direction == "return":
        raw += search_skyscanner(cfg["from"], cfg["to"], cfg["dates"])
    flights = normalize(raw)
    flights = filter_flights(flights, max_stops=ms)
    flights = dedup_flights(flights)
    flights = label_fare_types(flights)

    return direction, {
        "generated": today.isoformat(),
        "days_to_go": (TRIP_DATE - today).days,
        "trip_date": TRIP_DATE.isoformat(),
        "flights": [_flight_to_dict(f) for f in flights],
    }


def _validate_payload(payload: dict) -> list[str]:
    """Validate the payload before writing. Returns list of errors."""
    errors = []

    # Check all expected origins are present
    found_origins = set(payload.keys()) - {"_serpapi_usage"}
    missing = EXPECTED_ORIGINS - found_origins
    if missing:
        errors.append(f"Missing origins: {missing}")

    # Check each origin has both directions with enough flights
    for origin in EXPECTED_ORIGINS:
        if origin not in payload:
            continue
        for direction in ("outbound", "return"):
            if direction not in payload[origin]:
                errors.append(f"{origin} missing {direction}")
                continue
            flight_count = len(payload[origin][direction].get("flights", []))
            if flight_count < MIN_FLIGHTS_PER_DIRECTION:
                errors.append(
                    f"{origin} {direction}: only {flight_count} flights "
                    f"(min: {MIN_FLIGHTS_PER_DIRECTION})"
                )

    return errors


def main():
    force = "--force" in sys.argv

    print("=" * 60)
    print("FLIGHT DATA REFRESH")
    print(f"Expected SerpAPI calls: {EXPECTED_CALL_COUNT}")
    print("=" * 60)

    _check_cooldown(force)

    today = _today_pst()
    reset_serpapi_call_log()
    payload = {}

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {}
        for route in ROUTES:
            origin = route["origin"]
            payload[origin] = {}
            for direction in ("outbound", "return"):
                cfg = route[direction]
                fut = pool.submit(_run_direction, origin, direction, cfg, today)
                futures[fut] = origin

        for fut in as_completed(futures):
            origin = futures[fut]
            direction, result = fut.result()
            payload[origin][direction] = result
            flight_count = len(result["flights"])
            print(f"  {origin} {direction}: {flight_count} flights")

    call_log = get_serpapi_call_log()
    total_calls = len(call_log)
    by_route = {}
    for c in call_log:
        by_route[c["route"]] = by_route.get(c["route"], 0) + 1

    # Alert if call count exceeds expected
    if total_calls > EXPECTED_CALL_COUNT:
        print(f"\nWARNING: {total_calls} SerpAPI calls exceeds expected {EXPECTED_CALL_COUNT}!")
        print("ROUTES may have been modified. Update EXPECTED_CALL_COUNT if intentional.")

    payload["_serpapi_usage"] = {
        "total_calls": total_calls,
        "by_route": by_route,
        "refreshed_at": datetime.now(ZoneInfo("America/Los_Angeles")).isoformat(),
    }

    # Validate before writing
    errors = _validate_payload(payload)
    if errors:
        print(f"\nVALIDATION FAILED — not writing file:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)

    # Atomic write: temp file → rename (prevents corrupt partial writes)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=OUTPUT_DIR, suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp_path, OUTPUT_FILE)
    except Exception:
        os.unlink(tmp_path)
        raise

    # Also update the static fallback files (web/flights.json + public/flights.json)
    for fallback in [
        os.path.join(ROOT, "web", "flights.json"),
        os.path.join(ROOT, "public", "flights.json"),
    ]:
        try:
            fd2, tmp2 = tempfile.mkstemp(dir=os.path.dirname(fallback), suffix=".json")
            with os.fdopen(fd2, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            os.replace(tmp2, fallback)
        except Exception:
            try:
                os.unlink(tmp2)
            except OSError:
                pass

    print(f"\nSerpAPI calls used: {total_calls}")
    for route, count in sorted(by_route.items()):
        print(f"  {route}: {count}")
    print(f"\nSaved to {OUTPUT_FILE}")
    print(f"  + web/flights.json (fallback)")
    print(f"  + public/flights.json (fallback)")
    print(f"File size: {os.path.getsize(OUTPUT_FILE):,} bytes")


if __name__ == "__main__":
    main()

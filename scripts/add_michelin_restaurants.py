#!/usr/bin/env python3
"""Pull the real MICHELIN star / Bib Gourmand spots near our ports into the catalog.

Our Google-popular picks barely overlap with Michelin, so the Michelin filter is
empty without this. For each port that actually has Michelin presence
(Venice, Athens, Istanbul, Dubrovnik — the others have none), take every Star /
Bib entry within range, enrich it through Google Places (so it gets the same
rating/photo/booking/veg fields as the rest), tag the award, and append.

FREE (Google Places) + skips anything already in the catalog. Run photo fetch +
vibe pass afterwards.
"""
from __future__ import annotations

import csv
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hotel_agent import _haversine  # noqa: E402
from curate_restaurants import (  # noqa: E402
    _post_search, SEARCH_FIELDS, build_restaurant, geocode_landmark, OUT_PATHS,
)

ROOT = Path(__file__).resolve().parent.parent
MICHELIN_CSV = ROOT / "data" / "michelin_my_maps.csv"
RANGE_KM = 25.0
KEEP_AWARDS = {"1 Star", "2 Stars", "3 Stars", "Bib Gourmand"}

# Only the ports with Michelin presence. Landmarks reused from curate's list.
MICH_PORTS = {
    "venice":   {"label": "Venice (Ravenna), Italy",
                 "landmarks": ["Piazza San Marco, Venice", "Rialto Bridge, Venice"]},
    "ravenna":  {"label": "Ravenna, Italy",
                 "landmarks": ["Basilica di San Vitale, Ravenna", "Piazza del Popolo, Ravenna",
                               "Mausoleo di Galla Placidia, Ravenna"]},
    "dubrovnik":{"label": "Dubrovnik, Croatia",
                 "landmarks": ["Pile Gate, Dubrovnik", "Stradun, Dubrovnik", "Rector's Palace, Dubrovnik"]},
    "athens":   {"label": "Athens (Piraeus), Greece",
                 "landmarks": ["Acropolis, Athens", "Plaka, Athens", "Monastiraki Square, Athens"]},
    "istanbul": {"label": "Istanbul, Turkey",
                 "landmarks": ["Hagia Sophia, Istanbul", "Blue Mosque, Istanbul", "Galata Tower, Istanbul"]},
}

AWARD_TIER = {"1 Star": "star", "2 Stars": "star", "3 Stars": "star", "Bib Gourmand": "bib"}


MICHELIN_URL = "https://raw.githubusercontent.com/ngshiheng/michelin-my-maps/main/data/michelin_my_maps.csv"


def load_michelin():
    if not MICHELIN_CSV.exists():
        print("Michelin CSV missing — downloading the public dataset...")
        import requests
        r = requests.get(MICHELIN_URL, timeout=60)
        r.raise_for_status()
        MICHELIN_CSV.write_bytes(r.content)
    rows = []
    for r in csv.DictReader(open(MICHELIN_CSV)):
        try:
            rows.append({"name": r["Name"], "lat": float(r["Latitude"]),
                         "lng": float(r["Longitude"]), "award": r["Award"]})
        except Exception:  # noqa: BLE001
            pass
    return rows


def main() -> int:
    catalog = json.loads(OUT_PATHS[0].read_text())
    existing = catalog["restaurants"]
    have_ids = {r["id"] for r in existing}
    have_names = {(r["port_key"], r["name"].lower()) for r in existing}

    # port centroids from existing catalog
    cent = {}
    for pk in MICH_PORTS:
        pts = [(r["lat"], r["lng"]) for r in existing if r["port_key"] == pk]
        if pts:
            cent[pk] = (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))

    michelin = load_michelin()
    added = 0
    for pk, meta in MICH_PORTS.items():
        if pk not in cent:
            continue
        cla, clo = cent[pk]
        landmarks = [lm for lm in (geocode_landmark(n) for n in meta["landmarks"]) if lm]
        targets = [m for m in michelin
                   if m["award"] in KEEP_AWARDS and _haversine(cla, clo, m["lat"], m["lng"]) < RANGE_KM]
        print(f"\n== {pk}: {len(targets)} michelin star/bib in range ==")
        port = {"port_key": pk, "label": meta["label"]}
        for m in targets:
            if (pk, m["name"].lower()) in have_names:
                print(f"  skip (already have): {m['name']}")
                continue
            # enrich via Places by name + port
            country = meta["label"].split(",")[-1].strip()
            places = _post_search(f"{m['name']}, {pk}, {country}", SEARCH_FIELDS, max_results=1)
            if not places:
                print(f"  ! no Places match: {m['name']}")
                continue
            row = build_restaurant(places[0], port, landmarks)
            if not row or row["id"] in have_ids:
                continue
            row["michelin"] = AWARD_TIER[m["award"]]
            row["michelin_award"] = m["award"]
            row["source_tags"] = list(set(row.get("source_tags", []) + ["michelin"]))
            existing.append(row)
            have_ids.add(row["id"]); have_names.add((pk, row["name"].lower()))
            added += 1
            print(f"  + {row['name'][:34]:34} {m['award']:13} {row['rating']}* ({row['reviews']})")

    catalog["count"] = len(existing)
    for p in OUT_PATHS:
        p.write_text(json.dumps(catalog, ensure_ascii=False, indent=2))
    print(f"\nAdded {added} Michelin restaurants. Catalog now {len(existing)}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

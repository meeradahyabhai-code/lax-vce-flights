#!/usr/bin/env python3
"""Widen each port's restaurant search to a real RADIUS (default 10 mi) so we stop
missing metro spots that the old text-only queries (biased to the old town) skipped
— e.g. Bombay Spice in mainland Marghera, 3.1 mi from San Marco.

Uses Places searchText with a `locationRestriction` circle around each port center
and paginates to capture everything in the circle. Two passes per port:

  INDIAN  — exhaustive, NO rating floor (family always wants Indian), every genuine
            Indian match in the radius is added.
  GENERAL — the normal angles, kept at the >=4.0 rating AND >=100 reviews bar.

Both dedupe against the existing catalog (by place id and by port+name). New rows
have empty profile/vibe and a Places photo ref; run enrich + photo fetch after.

  python3 scripts/expand_radius_pull.py --radius-mi 10
  python3 scripts/expand_radius_pull.py --radius-mi 10 --port venice --dry-run
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, ROOT.as_posix())
sys.path.insert(0, (ROOT / "scripts").as_posix())
from curate_restaurants import (  # noqa: E402
    SEARCH_FIELDS, build_restaurant, geocode_landmark, OUT_PATHS, PORTS,
    MIN_RATING, MIN_REVIEWS, REQ_TIMEOUT, _txt,
)
from hotel_agent import _places_key, PLACES_SEARCH_URL, _haversine  # noqa: E402

MI_TO_M = 1609.34
MAX_PAGES = 3          # Places caps text search at 20/page, 60 total
INDIAN_MIN_REVIEWS = 20  # Indian skips the 4.0 bar, but a 2-review listing is noise, not a pick
INDIAN_NEEDLES = ("indian", "biryani", "tandoor", "curry", "punjab", "masala", "bombay", "mumbai", "delhi")


def _search_circle(query: str, center: dict, radius_m: float, want_max: int = 60) -> list[dict]:
    """searchText restricted to a circle, following nextPageToken up to MAX_PAGES."""
    headers = {"Content-Type": "application/json", "X-Goog-Api-Key": _places_key(),
               "X-Goog-FieldMask": SEARCH_FIELDS + ",nextPageToken"}
    out, token, pages = [], None, 0
    while pages < MAX_PAGES and len(out) < want_max:
        # searchText only restricts by rectangle; use a circle BIAS for coverage and
        # hard-cap distance from center in the caller (so 10 mi is truly enforced).
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
        except Exception as e:  # noqa: BLE001
            print(f"  ! search failed {query!r}: {e}", file=sys.stderr)
            break
        out.extend(data.get("places", []))
        token = data.get("nextPageToken")
        pages += 1
        if not token:
            break
        time.sleep(2.2)  # page token needs a moment to become valid
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--radius-mi", type=float, default=10.0)
    ap.add_argument("--port", default="")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    radius_m = args.radius_mi * MI_TO_M

    catalog = json.loads(OUT_PATHS[0].read_text())
    rows = catalog["restaurants"]
    have_ids = {r["id"] for r in rows}
    have_names = {(r["port_key"], r["name"].lower()) for r in rows}

    ports = [p for p in PORTS if (not args.port or p["port_key"] == args.port)]
    added_indian = added_general = 0
    new_rows: list[dict] = []

    for port in ports:
        landmarks = [lm for lm in (geocode_landmark(n) for n in port["landmarks"]) if lm]
        if not landmarks:
            print(f"  ! {port['port_key']}: no center geocoded, skipping", file=sys.stderr)
            continue
        center = landmarks[0]
        print(f"\n=== {port['port_key']} (center {center['name']}, {args.radius_mi}mi) ===")

        def _consider(p, *, cuisine_override=None, floor=True):
            nonlocal added_indian, added_general
            name = _txt(p.get("displayName"))
            if not name:
                return
            nkey = (port["port_key"], name.lower())
            if p.get("id") in have_ids or nkey in have_names:
                return
            # hard radius cap: drop anything beyond the circle (bias alone can leak)
            loc = p.get("location") or {}
            if loc.get("latitude") is not None:
                d_mi = _haversine(loc["latitude"], loc["longitude"], center["lat"], center["lng"]) * 0.621371
                if d_mi > args.radius_mi:
                    return
            if floor and not ((p.get("rating", 0) or 0) >= MIN_RATING
                              and (p.get("userRatingCount", 0) or 0) >= MIN_REVIEWS):
                return
            row = build_restaurant(p, port, landmarks)
            if not row or row["id"] in have_ids:
                return
            if cuisine_override:
                row["cuisine"] = cuisine_override
                row["source_tags"] = sorted(set(row.get("source_tags", []) + ["cuisine:indian", "radius"]))
            else:
                row["source_tags"] = sorted(set(row.get("source_tags", []) + ["radius"]))
            rows.append(row); new_rows.append(row)
            have_ids.add(row["id"]); have_names.add(nkey)
            if cuisine_override:
                added_indian += 1
            else:
                added_general += 1
            tag = "INDIAN" if cuisine_override else "gen"
            print(f"  + [{tag}] {name}  ({row['rating']}/{row['reviews']}, {row['nearest_landmark_mi']}mi)")

        # 1) Indian — exhaustive, no rating floor
        indian = _search_circle("Indian restaurant in " + port["label"], center, radius_m)
        for p in indian:
            blob = (_txt(p.get("displayName")) + " " + _txt(p.get("primaryTypeDisplayName")) + " "
                    + " ".join(p.get("types") or []) + " " + _txt(p.get("editorialSummary"))).lower()
            if any(n in blob for n in INDIAN_NEEDLES) and (p.get("userRatingCount", 0) or 0) >= INDIAN_MIN_REVIEWS:
                _consider(p, cuisine_override="Indian", floor=False)

        # 2) General — widen the normal angles to the full radius, keep the quality bar
        for ang in ("best restaurants", "seafood restaurants", "casual local restaurants",
                    "fine dining", "popular restaurants"):
            for p in _search_circle(ang + " in " + port["label"], center, radius_m):
                _consider(p, floor=True)

    print(f"\n{'(dry run) ' if args.dry_run else ''}added {added_indian} Indian + {added_general} general "
          f"= {len(new_rows)} new; catalog now {len(rows)}")
    if new_rows and not args.dry_run:
        for p in OUT_PATHS:
            p.write_text(json.dumps(catalog, ensure_ascii=False, indent=2))
        # leave a manifest of new ids so enrich/photo passes can target just these
        (ROOT / "data" / "_new_radius_ids.json").write_text(
            json.dumps([r["id"] for r in new_rows], indent=2))
        print(f"wrote catalogs + data/_new_radius_ids.json ({len(new_rows)} ids)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

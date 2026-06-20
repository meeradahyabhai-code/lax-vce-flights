#!/usr/bin/env python3
"""Add restaurants of a specific cuisine per port (e.g. Indian) to the catalog.

Some cuisines the family wants (Indian) aren't in the rating-sorted picks at all.
This searches Google Places for that cuisine at each port, keeps only genuine
matches (so we don't mislabel a fallback result), Places-enriches them, and
appends. Run photo fetch + enrich_profiles afterwards.

  python3 scripts/add_cuisine.py "Indian" --per-port 4
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from curate_restaurants import (  # noqa: E402
    _post_search, SEARCH_FIELDS, build_restaurant, geocode_landmark, OUT_PATHS, PORTS, _txt,
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cuisine")
    ap.add_argument("--per-port", type=int, default=4)
    args = ap.parse_args()
    cuisine = args.cuisine.strip()
    needle = cuisine.lower()

    catalog = json.loads(OUT_PATHS[0].read_text())
    rows = catalog["restaurants"]
    have_ids = {r["id"] for r in rows}
    have_names = {(r["port_key"], r["name"].lower()) for r in rows}

    added = 0
    for port in PORTS:
        landmarks = [lm for lm in (geocode_landmark(n) for n in port["landmarks"]) if lm]
        places = _post_search("best " + cuisine + " restaurants in " + port["label"],
                              SEARCH_FIELDS, max_results=12)
        kept = 0
        for p in places:
            if kept >= args.per_port:
                break
            # only keep genuine matches for this cuisine
            blob = (_txt(p.get("displayName")) + " " + _txt(p.get("primaryTypeDisplayName")) + " " +
                    " ".join(p.get("types") or []) + " " + _txt(p.get("editorialSummary"))).lower()
            if needle not in blob:
                continue
            name_key = (port["port_key"], _txt(p.get("displayName")).lower())
            if name_key in have_names:
                continue
            row = build_restaurant(p, port, landmarks)
            if not row or row["id"] in have_ids:
                continue
            row["cuisine"] = cuisine
            row["source_tags"] = list(set(row.get("source_tags", []) + ["cuisine:" + needle]))
            rows.append(row)
            have_ids.add(row["id"]); have_names.add(name_key)
            kept += 1; added += 1
        print(f"  {port['port_key']}: +{kept} {cuisine}")

    catalog["count"] = len(rows)
    for pth in OUT_PATHS:
        pth.write_text(json.dumps(catalog, ensure_ascii=False, indent=2))
    print(f"Added {added} {cuisine} restaurants. Catalog now {len(rows)}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

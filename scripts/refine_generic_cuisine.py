#!/usr/bin/env python3
"""Second pass: give a real cuisine to rows still tagged the generic "Restaurant".

The first enrich pass (enrich_profiles.py) leaves ~83 unbranded local spots as
"Restaurant" because their reviews never name a cuisine. This re-asks the model
for JUST the cuisine, grounded in the review material, and — when the material is
genuinely unbranded — falls to the port's LOCAL cuisine (a taverna in Athens is
Greek, an osteria in Venice is Venetian). It never answers "Restaurant" and never
returns a formality level. Cheap: gpt-4o-mini, one call per generic row.

  python3 scripts/refine_generic_cuisine.py            # all generic rows
  python3 scripts/refine_generic_cuisine.py --limit 5  # smoke test
  python3 scripts/refine_generic_cuisine.py --dry-run  # print, don't write
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import hotel_agent  # noqa: F401,E402  (loads .env)

PATHS = [ROOT / "data" / "restaurants.json",
         ROOT / "public" / "restaurants.json",
         ROOT / "web" / "restaurants.json"]
MODEL = "gpt-4o-mini"
GENERIC = {"restaurant", ""}

# Local cuisine to fall back to when the reviews don't name a specific one.
LOCAL_CUISINE = {
    "venice": "Venetian", "ravenna": "Italian", "dubrovnik": "Dalmatian",
    "bar": "Montenegrin", "athens": "Greek", "kusadasi": "Turkish",
    "rhodes": "Greek", "santorini": "Greek", "istanbul": "Turkish",
}

SYS = (
    "You assign the single best CUISINE label to a restaurant, in 1-2 words. "
    "Use the reviews/summary first: if they clearly point to a specific cuisine "
    "(seafood, steakhouse, sushi, pizzeria, meze, trattoria, etc.), use it. "
    "If the material does NOT name a cuisine, the place is an unbranded local spot — "
    "answer with the LOCAL cuisine you are told. "
    "NEVER answer 'Restaurant' and NEVER answer a price or formality level "
    "(no 'fine dining', 'casual', 'upscale'). Reply with ONLY the cuisine words."
)


def refine(r: dict, key: str) -> str | None:
    local = LOCAL_CUISINE.get(r.get("port_key"), "")
    material = (r.get("editorial", "") + "\n" + "\n".join(r.get("quotes", []))).strip()
    user = (f"Restaurant: {r['name']} (in {r.get('port', r.get('port_key'))}).\n"
            f"Local cuisine if unbranded: {local or 'local'}.\n"
            f"Reviews/summary:\n{material or '(none provided)'}")
    try:
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": MODEL, "temperature": 0.2, "max_tokens": 12,
                  "messages": [{"role": "system", "content": SYS},
                               {"role": "user", "content": user}]},
            timeout=30)
        resp.raise_for_status()
        out = resp.json()["choices"][0]["message"]["content"].strip().strip('".')
    except Exception as e:  # noqa: BLE001
        print(f"  ! {r['name'][:30]}: {e}", file=sys.stderr)
        return None
    # Guard: never accept a generic/level answer; fall back to the local cuisine.
    bad = {"restaurant", "fine dining", "casual", "smart casual", "upscale", ""}
    if out.lower() in bad or len(out) > 24:
        out = local or ""
    return out or None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        print("ERROR: OPENAI_API_KEY missing", file=sys.stderr)
        return 1

    cat = json.loads(PATHS[0].read_text())
    rows = cat["restaurants"]
    todo = [r for r in rows if (r.get("cuisine") or "").strip().lower() in GENERIC]
    if args.limit:
        todo = todo[:args.limit]
    print(f"refining cuisine for {len(todo)} generic rows...")
    changed = 0
    for r in todo:
        new = refine(r, key)
        if new and new != r.get("cuisine"):
            print(f"  {r['name'][:34]:34} [{r['port_key']}]  Restaurant -> {new}")
            if not args.dry_run:
                r["cuisine"] = new
            changed += 1
        time.sleep(0.12)

    if not args.dry_run:
        for p in PATHS:
            p.write_text(json.dumps(cat, ensure_ascii=False, indent=2))
    still = sum(1 for r in rows if (r.get("cuisine") or "").strip().lower() in GENERIC)
    print(f"\n{'(dry run) ' if args.dry_run else ''}updated {changed} rows; "
          f"generic remaining: {still}/{len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

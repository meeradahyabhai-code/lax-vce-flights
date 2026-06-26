#!/usr/bin/env python3
"""Look up Natasha's specific restaurant recs via Google Places (free) and
report matches. DRY RUN by default — pass --write to append to restaurants.json.
"""
import argparse, json, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
# Load .env (simple KEY=VALUE) so GOOGLE_PLACES_API_KEY / OPENAI_API_KEY are set.
envf = ROOT/".env"
if envf.exists():
    for line in envf.read_text().splitlines():
        line=line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k,v=line.split("=",1); os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
sys.path.insert(0, str(ROOT))
from scripts.curate_restaurants import _post_search, geocode_landmark, build_restaurant, SEARCH_FIELDS, PORTS
from restaurant_finder import enrich_rows  # fills BOTH profile + vibe (curate's add_vibe only does vibe)

PORT_BY_KEY = {p["port_key"]: p for p in PORTS}

# (port_key, label, query, must_contain_in_addr_or_name, forbid_id)
TARGETS = [
  ("venice", "Caffè Florian",      "Caffè Florian, Piazza San Marco, Venice, Italy", "", ""),
  ("venice", "Pasticceria Dal Mas","Pasticceria Dal Mas, Lista di Spagna, Venice, Italy", "", ""),
  ("venice", "Da Mamo",            "Da Mamo Restaurant, Venice, Italy", "", ""),
  ("venice", "Dal Moro's To Go",   "Dal Moro's Fresh Pasta To Go, Venice, Italy", "", "ChIJPSXhncuxfkcRcUc_CheC_AU"),
  ("venice", "I Tre Mercanti",     "I Tre Mercanti, Venice, Italy", "mercanti", ""),
  ("venice", "Gelatoteca Suso",    "Gelatoteca Suso, Venice, Italy", "", ""),
  ("venice", "Skyline Rooftop Bar","Skyline Rooftop Bar, Hilton Molino Stucky, Venice, Italy", "", ""),
  ("venice", "Harry's Bar",        "Harry's Bar, Calle Vallaresso, Venice, Italy", "", ""),
  ("venice", "Terrazza Danieli",   "Terrazza Danieli rooftop restaurant, Hotel Danieli, Venice, Italy", "", ""),
  ("athens", "Indian Haveli",      "Indian Haveli, Leoforos Andrea Siggrou 12, Athens 117 42, Greece", "syggrou", "ChIJV6n5ZTy9oRQRd1YgbdjGKq4"),
  ("athens", "Yogu Lab",           "Yogu Lab frozen yogurt, Athens, Greece", "", ""),
  ("santorini","Rastoni",          "Rastoni, Fira, Santorini, Greece", "", ""),
  ("santorini","Pitagram",         "Pitagram gyros, Fira, Santorini, Greece", "", ""),
]

def lookup(query, forbid_id):
    places = _post_search(query, SEARCH_FIELDS, included_type=None, max_results=4)
    for p in places:
        if forbid_id and p.get("id")==forbid_id:
            continue
        return p, places
    return (None, places)

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--write",action="store_true"); ap.add_argument("--vibe",action="store_true")
    args=ap.parse_args()
    # geocode landmarks per touched port once
    lm_cache={}
    for pk in {t[0] for t in TARGETS}:
        port=PORT_BY_KEY[pk]
        lm_cache[pk]=[lm for lm in (geocode_landmark(n) for n in port["landmarks"]) if lm]
    rows=[]
    print(f"{'LABEL':22} {'MATCHED NAME':38} {'RATING':>6} {'REVIEWS':>8}  ADDRESS / id")
    print("-"*130)
    for pk,label,query,must,forbid in TARGETS:
        p,allp=lookup(query,forbid)
        if not p:
            print(f"{label:22} !! NO MATCH (query={query!r})"); continue
        name=(p.get('displayName') or {}).get('text','')
        addr=p.get('formattedAddress','')
        warn=""
        if must and must not in (addr+name).lower(): warn=f"  <-- CHECK: '{must}' not in addr/name"
        print(f"{label:22} {name[:38]:38} {p.get('rating','?'):>6} {p.get('userRatingCount','?'):>8}  {addr[:55]} | {p.get('id','')}{warn}")
        row=build_restaurant(p, PORT_BY_KEY[pk], lm_cache[pk])
        if row:
            row['source_tags']=['google','natasha-rec']
            rows.append((label,row))
    out=ROOT/"scripts"/"_natasha_lookup.json"
    out.write_text(json.dumps([{"label":l,**r} for l,r in rows], ensure_ascii=False, indent=2))
    print(f"\nBuilt {len(rows)} rows -> {out}")
    if args.vibe:
        # enrich_rows fills profile + vibe (profile is required by test_profile_and_vibe_present)
        enrich_rows([r for _,r in rows], os.environ.get("OPENAI_API_KEY"))
        out.write_text(json.dumps([{"label":l,**r} for l,r in rows], ensure_ascii=False, indent=2))
    if args.write:
        catalog=ROOT/"data"/"restaurants.json"
        data=json.loads(catalog.read_text()); existing=data["restaurants"]
        have={r["id"] for r in existing}
        added=0
        for _,r in rows:
            if r["id"] in have:
                print(f"  skip (already in catalog): {r['name']} {r['id']}"); continue
            existing.append(r); have.add(r["id"]); added+=1
        data["count"]=len(existing)
        for path in [ROOT/"data"/"restaurants.json", ROOT/"public"/"restaurants.json", ROOT/"web"/"restaurants.json"]:
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        print(f"WROTE {added} new rows to catalog (now {len(existing)}).")
    return 0
if __name__=="__main__": raise SystemExit(main())

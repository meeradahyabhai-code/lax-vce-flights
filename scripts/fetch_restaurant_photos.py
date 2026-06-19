#!/usr/bin/env python3
"""Download each restaurant's top photo once -> static files (no client key).

FREE: Google Places Photo (New) media endpoint, within the free tier.
Reads restaurants.json, saves public/media/restaurants/<id>.jpg, and writes a
`photo_local` path back into the catalog (data/ + public/ + web/ copies).
Re-runnable: skips images already on disk unless --force.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hotel_agent import _places_key  # noqa: E402  (triggers load_dotenv)

ROOT = Path(__file__).resolve().parent.parent
CATALOGS = [ROOT / "data" / "restaurants.json",
            ROOT / "public" / "restaurants.json",
            ROOT / "web" / "restaurants.json"]
IMG_DIR = ROOT / "public" / "media" / "restaurants"
REL_PREFIX = "/media/restaurants"  # how the app references it (public/ is web root)
MAX_PX = 900


def fetch_photo(photo_name: str, dest: Path) -> bool:
    """photo_name like 'places/XXX/photos/YYY' -> jpg bytes on disk."""
    url = f"https://places.googleapis.com/v1/{photo_name}/media"
    try:
        r = requests.get(url, params={"maxHeightPx": MAX_PX, "maxWidthPx": MAX_PX},
                         headers={"X-Goog-Api-Key": _places_key()},
                         timeout=30, allow_redirects=True)
        r.raise_for_status()
        if not r.content or not r.headers.get("Content-Type", "").startswith("image"):
            return False
        dest.write_bytes(r.content)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"  ! {dest.name}: {e}", file=sys.stderr)
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="re-download even if file exists")
    args = ap.parse_args()
    if not _places_key():
        print("ERROR: GOOGLE_PLACES_API_KEY not set", file=sys.stderr)
        return 1

    IMG_DIR.mkdir(parents=True, exist_ok=True)
    catalog = json.loads(CATALOGS[0].read_text())
    rows = catalog["restaurants"]

    ok = skipped = failed = 0
    for r in rows:
        rid = r.get("id", "")
        photo_ref = r.get("photo", "")
        if not rid or not photo_ref:
            r["photo_local"] = ""
            failed += 1
            continue
        dest = IMG_DIR / f"{rid}.jpg"
        if dest.exists() and not args.force:
            r["photo_local"] = f"{REL_PREFIX}/{rid}.jpg"
            skipped += 1
            continue
        if fetch_photo(photo_ref, dest):
            r["photo_local"] = f"{REL_PREFIX}/{rid}.jpg"
            ok += 1
            print(f"  ok  {r['name'][:40]}")
        else:
            r["photo_local"] = ""
            failed += 1

    for path in CATALOGS:
        path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2))

    total_mb = sum(p.stat().st_size for p in IMG_DIR.glob("*.jpg")) / 1e6
    print(f"\ndownloaded {ok}, skipped {skipped}, failed {failed}; "
          f"{len(list(IMG_DIR.glob('*.jpg')))} images, {total_mb:.1f} MB total")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

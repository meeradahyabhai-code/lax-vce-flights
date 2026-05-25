"""Parse NCL shorex print-page HTML into data/excursions.json."""
import json
import re
from pathlib import Path
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
HTML_PATH = ROOT / "data" / "ncl_shorex.html"
OUT_PATH = ROOT / "data" / "excursions.json"
PUBLIC_OUT_PATH = ROOT / "public" / "excursions.json"
WEB_OUT_PATH = ROOT / "web" / "excursions.json"

PORT_TO_KEY = {
    "dubrovnik": "dubrovnik",
    "kotor": "kotor",
    "bar": "bar",
    "athens": "athens",
    "piraeus": "athens",
    "kusadasi": "kusadasi",
    "rhodes": "rhodes",
    "santorini": "santorini",
    "istanbul": "istanbul",
    "venice": "venice",
    "ravenna": "venice",
}

PRICE_RE = re.compile(r"\$([\d,.]+)")


def port_key(port_text: str) -> str:
    t = port_text.lower()
    for needle, key in PORT_TO_KEY.items():
        if needle in t:
            return key
    return "other"


def parse_price(el):
    if not el:
        return None
    m = PRICE_RE.search(el.get_text(" ", strip=True))
    return float(m.group(1).replace(",", "")) if m else None


def main():
    soup = BeautifulSoup(HTML_PATH.read_text(), "html.parser")
    rows = soup.select("tr.resultRow")
    excursions = []
    for row in rows:
        img = row.select_one(".imgHolder img")
        img_src = img.get("ng-src") or img.get("src") if img else None
        if img_src and img_src.startswith("/"):
            img_src = "https://www.ncl.com" + img_src
        title_a = row.select_one(".ratesHolder h3 a")
        title = title_a.get_text(strip=True) if title_a else None
        detail_url = title_a.get("href") if title_a else None
        code = None
        if detail_url:
            m = re.search(r"/shorex-detail/([A-Z0-9]+)", detail_url)
            code = m.group(1) if m else None
        port_el = row.select_one(".ratesHolder h4")
        port_text = port_el.get_text(strip=True) if port_el else ""
        desc_el = row.select_one(".description")
        description = desc_el.get_text(" ", strip=True) if desc_el else ""
        prices = row.select(".priceTag li")
        adult_from = parse_price(prices[0]) if len(prices) > 0 else None
        child_from = parse_price(prices[1]) if len(prices) > 1 else None
        duration_el = None
        activity_level = None
        for li in row.select(".rating .listItem"):
            label = li.get_text(" ", strip=True).lower()
            if "duration" in label:
                duration_el = li.find("strong")
        act_el = row.select_one(".rating .activityLevelNo")
        if act_el:
            try:
                activity_level = int(act_el.get_text(strip=True))
            except ValueError:
                activity_level = None
        duration = duration_el.get_text(strip=True) if duration_el else None

        excursions.append({
            "code": code,
            "title": title,
            "port": port_text,
            "port_key": port_key(port_text),
            "description": description,
            "adult_from": adult_from,
            "child_from": child_from,
            "duration": duration,
            "activity_level": activity_level,
            "image": img_src,
            "detail_url": detail_url,
        })

    by_port = {}
    for e in excursions:
        by_port.setdefault(e["port_key"], []).append(e)

    payload = json.dumps({
        "count": len(excursions),
        "excursions": excursions,
    }, indent=2, ensure_ascii=False)
    OUT_PATH.write_text(payload)
    PUBLIC_OUT_PATH.write_text(payload)
    WEB_OUT_PATH.write_text(payload)
    print(f"Wrote {len(excursions)} excursions to:")
    print(f"  {OUT_PATH}")
    print(f"  {PUBLIC_OUT_PATH}")
    print(f"  {WEB_OUT_PATH}")
    for k in sorted(by_port):
        print(f"  {k}: {len(by_port[k])}")


if __name__ == "__main__":
    main()

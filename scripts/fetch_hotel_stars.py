#!/usr/bin/env python3
"""
Fetch Venice hotel star ratings from Regione del Veneto open data.

Downloads the official CSV dump of all tourist accommodation in the Veneto region,
filters to Venice hotels (alberghi), and extracts star classifications.

Output: data/hotel_stars_venice.json
"""

import csv
import io
import json
import os
import re
import sys
from datetime import date
from urllib.request import urlopen

CSV_URL = (
    "http://dati.veneto.it/SpodCkanApi/api/3/datastore/dump/"
    "65180fdd-7752-4d11-9152-063fa0995634.csv"
)

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "hotel_stars_venice.json")


def parse_stars(classificazione: str) -> tuple[int, str]:
    """Parse CLASSIFICAZIONE field to (stars, classification_text).

    Returns:
        (stars, original_text) where stars is 1-5.
    """
    text = (classificazione or "").strip()
    if not text:
        return (0, text)

    # Extract leading digit
    m = re.match(r"(\d)", text)
    if m:
        return (int(m.group(1)), text)
    return (0, text)


def fetch_and_parse() -> list[dict]:
    """Download CSV and extract Venice hotel data."""
    print(f"Downloading CSV from {CSV_URL} ...")
    with urlopen(CSV_URL, timeout=30) as resp:
        raw = resp.read()

    # Decode — the CSV is typically UTF-8 or Latin-1
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")

    print(f"Downloaded {len(raw):,} bytes")

    reader = csv.DictReader(io.StringIO(text), delimiter=";", quotechar='"')

    hotels = []
    for row in reader:
        comune = (row.get("COMUNE") or "").strip().upper()
        tipologia = (row.get("TIPOLOGIA_SECONDARIA") or "").strip()

        if comune != "VENEZIA" or tipologia != "Albergo":
            continue

        name = (row.get("DENOMINAZIONE") or "").strip()
        classificazione = (row.get("CLASSIFICAZIONE") or "").strip()
        indirizzo = (row.get("INDIRIZZO") or "").strip()
        numero = (row.get("NUMERO_CIVICO") or "").strip()
        cap = (row.get("CAP") or "").strip()

        stars, classification_text = parse_stars(classificazione)
        if stars == 0:
            continue

        # Build address string
        address = indirizzo
        if numero:
            address = f"{indirizzo}, {numero}"

        hotel = {
            "name": name,
            "stars": stars,
            "classification": classification_text,
            "address": address,
            "cap": cap,
        }

        # Add flags for special classifications
        lower_class = classification_text.lower()
        if "superior" in lower_class:
            hotel["superior"] = True
        if "lusso" in lower_class:
            hotel["lusso"] = True

        hotels.append(hotel)

    # Sort alphabetically by name
    hotels.sort(key=lambda h: h["name"].upper())
    return hotels


def print_summary(hotels: list[dict]) -> None:
    """Print breakdown by star rating."""
    from collections import Counter
    star_counts = Counter(h["stars"] for h in hotels)
    print(f"\nTotal Venice hotels found: {len(hotels)}")
    print("Breakdown by star rating:")
    for stars in sorted(star_counts):
        label = "star" if stars == 1 else "stars"
        extras = []
        if stars == 4:
            sup = sum(1 for h in hotels if h["stars"] == 4 and h.get("superior"))
            if sup:
                extras.append(f"{sup} Superior")
        if stars == 5:
            lusso = sum(1 for h in hotels if h["stars"] == 5 and h.get("lusso"))
            if lusso:
                extras.append(f"{lusso} Lusso")
        extra_str = f"  ({', '.join(extras)})" if extras else ""
        print(f"  {stars} {label}: {star_counts[stars]}{extra_str}")


def main():
    hotels = fetch_and_parse()

    output = {
        "source": "Regione del Veneto - Direzione Turismo",
        "license": "CC BY 4.0",
        "url": (
            "https://dati.veneto.it/content/"
            "elenco_delle_strutture_ricettive_turistiche_della_regione_veneto_"
            "aggiornamento_quotidiano"
        ),
        "fetched": date.today().isoformat(),
        "hotels": hotels,
    }

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\nWrote {len(hotels)} hotels to {OUTPUT_FILE}")
    print_summary(hotels)


if __name__ == "__main__":
    main()

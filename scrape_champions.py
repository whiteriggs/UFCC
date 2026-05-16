"""
Scraper UFCC champions.

Fuente: Google My Map embebido en
https://www.stevesfootballstats.uk/unofficial_football_club_championship_ufcc.html
KML directo:
  https://www.google.com/maps/d/kml?mid=1_d88Yrh5pYFbeoOkpoOLvyxY4FFgyly2&forcekml=1

Cada <Placemark> = un club que ha ostentado el título UFCC alguna vez.
Salida: ufcc.db (SQLite) con tabla `champions`.
"""

from __future__ import annotations

import sqlite3
import sys
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

KML_URL = (
    "https://www.google.com/maps/d/kml"
    "?mid=1_d88Yrh5pYFbeoOkpoOLvyxY4FFgyly2&forcekml=1"
)
KML_PATH = Path("ufcc.kml")
DB_PATH = Path("ufcc.db")
KML_NS = {"kml": "http://www.opengis.net/kml/2.2"}


def download_kml(force: bool = False) -> bytes:
    if KML_PATH.exists() and not force:
        return KML_PATH.read_bytes()
    print(f"Downloading KML from {KML_URL} ...")
    req = urllib.request.Request(KML_URL, headers={"User-Agent": "ufcc-scraper/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = resp.read()
    KML_PATH.write_bytes(data)
    return data


def parse_placemarks(kml_bytes: bytes) -> list[dict]:
    root = ET.fromstring(kml_bytes)
    out: list[dict] = []
    for pm in root.findall(".//kml:Placemark", KML_NS):
        name_el = pm.find("kml:name", KML_NS)
        coords_el = pm.find(".//kml:Point/kml:coordinates", KML_NS)
        if name_el is None or coords_el is None:
            continue
        name = (name_el.text or "").strip()
        coords_text = (coords_el.text or "").strip()
        # KML: "lon,lat[,alt]"
        parts = coords_text.split(",")
        if len(parts) < 2:
            continue
        try:
            lon = float(parts[0])
            lat = float(parts[1])
        except ValueError:
            continue
        if not name:
            continue
        out.append({"name": name, "lat": lat, "lon": lon})
    return out


def write_sqlite(rows: list[dict]) -> None:
    if DB_PATH.exists():
        DB_PATH.unlink()
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute(
            """
            CREATE TABLE champions (
                id   INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                lat  REAL NOT NULL,
                lon  REAL NOT NULL
            )
            """
        )
        # Dedupe por nombre conservando primera aparición.
        seen: set[str] = set()
        deduped: list[dict] = []
        for r in rows:
            if r["name"] in seen:
                continue
            seen.add(r["name"])
            deduped.append(r)
        con.executemany(
            "INSERT INTO champions (name, lat, lon) VALUES (:name, :lat, :lon)",
            deduped,
        )
        con.commit()
        print(f"Inserted {len(deduped)} clubs ({len(rows) - len(deduped)} duplicates skipped).")
    finally:
        con.close()


def main() -> int:
    force = "--force" in sys.argv
    kml = download_kml(force=force)
    rows = parse_placemarks(kml)
    print(f"Parsed {len(rows)} placemarks from KML.")
    write_sqlite(rows)
    print(f"Database written to {DB_PATH.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""
Extrae escudos de clubes desde el HTML ya cacheado (cache_html/) y los descarga
a crests/. Crea tabla `clubs(name, crest_url, crest_path)` en ufcc.db.

Las páginas de Steve embeben el escudo del local y visitante como <img> dentro
de las dos celdas "vacías" (índices 2 y 6 de cada fila de 9 columnas) que el
scraper de linaje descartaba. Aquí se vuelven a parsear esos HTML locales.
"""

from __future__ import annotations

import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from html.parser import HTMLParser
from pathlib import Path

BASE = "https://www.stevesfootballstats.uk"
CACHE_DIR = Path("cache_html")
CRESTS_DIR = Path("crests")
DB_PATH = Path("ufcc.db")
USER_AGENT = "ufcc-scraper/1.0 (+personal project)"

WS_RX = re.compile(r"\s+")


class RowImgExtractor(HTMLParser):
    """Por cada <tr> devuelve (cells_text, cells_imgs) alineadas."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[tuple[list[str], list[list[str]]]] = []
        self._in_table = 0
        self._row_text: list[str] | None = None
        self._row_imgs: list[list[str]] | None = None
        self._cell_chunks: list[str] | None = None
        self._cell_imgs: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        t = tag.lower()
        if t == "table":
            self._in_table += 1
        elif t == "tr" and self._in_table:
            self._row_text = []
            self._row_imgs = []
        elif t in ("td", "th") and self._row_text is not None:
            self._cell_chunks = []
            self._cell_imgs = []
        elif t == "img" and self._cell_imgs is not None:
            for k, v in attrs:
                if k == "src" and v:
                    self._cell_imgs.append(v)
                    break
        elif t == "br" and self._cell_chunks is not None:
            self._cell_chunks.append(" ")

    def handle_endtag(self, tag):
        t = tag.lower()
        if t == "table" and self._in_table:
            self._in_table -= 1
        elif t == "tr" and self._row_text is not None:
            self.rows.append((self._row_text, self._row_imgs or []))
            self._row_text = None
            self._row_imgs = None
        elif t in ("td", "th") and self._cell_chunks is not None:
            text = WS_RX.sub(" ", "".join(self._cell_chunks)).strip()
            self._row_text.append(text)  # type: ignore[union-attr]
            self._row_imgs.append(self._cell_imgs or [])  # type: ignore[union-attr]
            self._cell_chunks = None
            self._cell_imgs = None

    def handle_data(self, data):
        if self._cell_chunks is not None:
            self._cell_chunks.append(data)


def collect_club_crests() -> dict[str, str]:
    """Recorre cada HTML cacheado y agrega votos (club -> URL). Devuelve la URL
    más común para cada club."""
    votes: dict[str, Counter[str]] = defaultdict(Counter)
    files = sorted(CACHE_DIR.glob("*ufcc_full_result*"))
    if not files:
        print("No cached HTML found. Run scrape_lineage.py first.", file=sys.stderr)
        sys.exit(1)
    for fpath in files:
        html = fpath.read_text(encoding="utf-8", errors="replace")
        parser = RowImgExtractor()
        parser.feed(html)
        for cells, imgs in parser.rows:
            if len(cells) < 9 or len(imgs) < 9:
                continue
            # Saltar cabeceras.
            no_digits = re.sub(r"\D", "", cells[0])
            if not no_digits:
                continue
            home, away = cells[3].strip(), cells[5].strip()
            home_imgs, away_imgs = imgs[2], imgs[6]
            if home and home_imgs:
                votes[home][home_imgs[0]] += 1
            if away and away_imgs:
                votes[away][away_imgs[0]] += 1
    return {club: c.most_common(1)[0][0] for club, c in votes.items() if c}


def normalize_url(src: str) -> str:
    if src.startswith("//"):
        return "https:" + src
    if src.startswith("/"):
        return BASE + src
    return src


def safe_filename(url: str) -> str:
    # Conserva el basename del path como nombre local.
    path = urllib.parse.urlparse(url).path
    name = path.rsplit("/", 1)[-1] or "crest"
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)


def download_all(crests: dict[str, str]) -> dict[str, str | None]:
    CRESTS_DIR.mkdir(exist_ok=True)
    results: dict[str, str | None] = {}
    total = len(crests)
    for i, (club, src) in enumerate(sorted(crests.items()), 1):
        url = normalize_url(src)
        fname = safe_filename(url)
        path = CRESTS_DIR / fname
        if path.exists() and path.stat().st_size > 0:
            results[club] = str(path)
            continue
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = resp.read()
            if not data:
                results[club] = None
            else:
                path.write_bytes(data)
                results[club] = str(path)
        except Exception as exc:
            print(f"[{i}/{total}] FAIL {club}: {url} -> {exc}", file=sys.stderr)
            results[club] = None
            continue
        if i % 25 == 0:
            print(f"  downloaded {i}/{total}")
        # Cortesía con el servidor.
        time.sleep(0.05)
    return results


DDL = """
DROP TABLE IF EXISTS clubs;
CREATE TABLE clubs (
    name        TEXT PRIMARY KEY,
    crest_url   TEXT,
    crest_path  TEXT
);
CREATE INDEX idx_clubs_name ON clubs(name);
"""


def write_clubs(crests: dict[str, str], paths: dict[str, str | None]) -> None:
    con = sqlite3.connect(DB_PATH)
    try:
        con.executescript(DDL)
        cur = con.cursor()
        # Recolectar TODOS los clubes que aparecen en matches (aunque sin escudo)
        # para que la tabla sea exhaustiva.
        rows_in_matches = cur.execute(
            "SELECT DISTINCT name FROM ("
            " SELECT home AS name FROM matches"
            " UNION SELECT away FROM matches"
            ")"
        ).fetchall()
        all_clubs = {r[0] for r in rows_in_matches} | set(crests.keys())
        for club in sorted(all_clubs):
            src = crests.get(club)
            url = normalize_url(src) if src else None
            cur.execute(
                "INSERT INTO clubs (name, crest_url, crest_path) VALUES (?, ?, ?)",
                (club, url, paths.get(club)),
            )
        con.commit()
    finally:
        con.close()


def main() -> int:
    crests = collect_club_crests()
    print(f"Distinct clubs with at least one crest URL: {len(crests)}")
    paths = download_all(crests)
    ok = sum(1 for v in paths.values() if v)
    print(f"Downloaded crests: {ok}/{len(paths)}")
    write_clubs(crests, paths)

    con = sqlite3.connect(DB_PATH)
    try:
        total = con.execute("SELECT COUNT(*) FROM clubs").fetchone()[0]
        with_crest = con.execute(
            "SELECT COUNT(*) FROM clubs WHERE crest_path IS NOT NULL"
        ).fetchone()[0]
        print(f"clubs table: {total} rows, {with_crest} with local crest "
              f"({with_crest / total * 100:.1f}% coverage)")
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

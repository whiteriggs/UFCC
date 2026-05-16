"""
Scraper UFCC: linaje completo de partidos.

Descubre todas las páginas de resultados por décadas desde la página principal
y las parsea. Calcula el campeón tras cada partido con la regla "boxing":
  - victoria local  -> nuevo campeón = local
  - victoria visit. -> nuevo campeón = visitante
  - empate          -> retiene el campeón previo
  - walkover (w/o)  -> gana el local por incomparecencia

Tablas creadas en ufcc.db:
  matches(
    id, match_no, date_raw, date_iso,
    home, away, score, home_goals, away_goals, result,
    competition, venue,
    champion_after, source_url
  )
  reigns(
    id, club, start_match_id, end_match_id, matches_held, started_on, ended_on
  )
"""

from __future__ import annotations

import re
import sqlite3
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from pathlib import Path

BASE = "https://www.stevesfootballstats.uk/"
ENTRY = BASE + "unofficial_football_club_championship_ufcc.html"
DB_PATH = Path("ufcc.db")
CACHE_DIR = Path("cache_html")
USER_AGENT = "ufcc-scraper/1.0 (+personal project)"

# Heurística para distinguir páginas de resultados globales UFCC
# (incluye `ufcc_full_results.html`, `ufcc_full_results_1900_-_1910.html`,
#  `ufcc_full_result_list_1890_-_1900.html`, `ufcc_full_result_2010_-_2020.html`).
# Anclado con `^` para EXCLUIR los títulos paralelos SUFCC (escocés),
# EUFCC y english_ufcc (inglés tier-1), que tienen sus propios linajes.
RESULT_URL_RX = re.compile(
    r"^ufcc_full_result(?:s|_list)?(?:_\d{4}_-_\d{4})?\.html$"
)

# Partidos que faltan en el HTML de la fuente (filas rotas o ausentes).
# Datos añadidos manualmente desde otras fuentes/aportes del usuario.
MANUAL_MATCHES: list[dict] = [
    {
        "match_no": 206,
        "date_raw": "28/11/1891",
        "home": "Preston North End",
        "away": "Bolton Wanderers",
        "score": "4-0",
        "competition": "Football League",
        "venue": "Deepdale, Preston",
        "source_url": "manual",
    },
    {
        "match_no": 557,
        "date_raw": "14/9/1901",
        "home": "Tottenham Hotspur",
        "away": "Queen's Park Rangers",
        "score": "2-0",
        "competition": "Southern League",
        "venue": "High Road Ground, London",
        "source_url": "manual",
    },
]


# ---------------------- HTTP + cache ----------------------

def fetch(url: str, force: bool = False) -> str:
    CACHE_DIR.mkdir(exist_ok=True)
    key = re.sub(r"[^A-Za-z0-9._-]+", "_", url)
    path = CACHE_DIR / key
    if path.exists() and not force:
        return path.read_text(encoding="utf-8", errors="replace")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
    text = raw.decode("utf-8", errors="replace")
    path.write_text(text, encoding="utf-8")
    return text


# ---------------------- HTML helpers ----------------------

WS_RX = re.compile(r"\s+")


class _RowExtractor(HTMLParser):
    """Extract todas las filas <tr> con sus celdas como texto plano.

    Maneja tablas anidadas correctamente (cada <tr> está ligado a su tabla
    más cercana). Devuelve filas agrupadas por la tabla de nivel superior
    a la que pertenecen para preservar el agrupamiento original del documento.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._table_stack: list[list[list[str]]] = []
        self._row: list[str] | None = None
        self._cell_chunks: list[str] | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        t = tag.lower()
        if t == "table":
            new_table: list[list[str]] = []
            self._table_stack.append(new_table)
            self.tables.append(new_table)
        elif t == "tr" and self._table_stack:
            self._row = []
        elif t in ("td", "th") and self._row is not None:
            self._cell_chunks = []
        elif t == "br" and self._cell_chunks is not None:
            self._cell_chunks.append(" ")

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t == "table" and self._table_stack:
            self._table_stack.pop()
        elif t == "tr" and self._row is not None:
            if self._table_stack:
                self._table_stack[-1].append(self._row)
            self._row = None
        elif t in ("td", "th") and self._cell_chunks is not None:
            text = WS_RX.sub(" ", "".join(self._cell_chunks)).strip()
            if self._row is not None:
                self._row.append(text)
            self._cell_chunks = None

    def handle_data(self, data: str) -> None:
        if self._cell_chunks is not None:
            self._cell_chunks.append(data)


def extract_all_rows(html: str) -> list[list[str]]:
    """Devuelve TODAS las filas (de cualquier tabla, anidada o no), en orden.

    El parsing por estructura es frágil para este sitio (usa tablas como
    layout), así que mezclamos todo y filtramos por forma de la fila
    (>=9 celdas + número de partido válido).
    """
    parser = _RowExtractor()
    parser.feed(html)
    rows: list[list[str]] = []
    for table in parser.tables:
        rows.extend(table)
    return rows


# ---------------------- URL discovery ----------------------

def discover_result_pages() -> list[str]:
    html = fetch(ENTRY)
    hrefs = re.findall(r'href="([^"]+)"', html, re.I)
    pages: list[str] = []
    seen: set[str] = set()
    for h in hrefs:
        # Normaliza a URL absoluta dentro del dominio.
        absolute = urllib.parse.urljoin(BASE, h)
        if not absolute.startswith(BASE):
            continue
        fname = absolute.split("/")[-1]
        if not RESULT_URL_RX.search(fname):
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        pages.append(absolute)
    # Ordenar para procesar cronológicamente: la página base (sin año) primero,
    # luego por la primera década que aparece en el nombre.
    def sort_key(u: str) -> tuple[int, int]:
        m = re.search(r"(\d{4})_-_(\d{4})", u)
        if not m:
            return (0, 0)
        return (1, int(m.group(1)))
    pages.sort(key=sort_key)
    return pages


# ---------------------- Match parsing ----------------------

SCORE_RX = re.compile(r"^\s*(\d+)\s*-\s*(\d+)")
# Acepta fechas simples "D/M/YYYY" y multidía como "9,10/4/1959",
# "6, 7/1/1962", "4-5/11/1972", "25-26/11/1972", "21, 22, 23/4/1962".
# Capturamos solo el primer día + mes + año.
DATE_RX = re.compile(r"^\s*(\d{1,2})(?:\s*[,\-]\s*\d{1,2})*\s*/\s*(\d{1,2})\s*/\s*(\d{4})\s*$")


@dataclass
class Match:
    match_no: int | None
    date_raw: str
    date_iso: str | None
    home: str
    away: str
    score: str
    home_goals: int | None
    away_goals: int | None
    result: str            # 'H', 'A', 'D', 'W' (walkover home), 'U' unknown
    competition: str
    venue: str
    source_url: str


def parse_match_no(raw: str) -> int | None:
    digits = re.sub(r"\D", "", raw)
    return int(digits) if digits else None


def parse_date(raw: str) -> str | None:
    m = DATE_RX.match(raw.strip())
    if not m:
        return None
    d, mo, y = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    try:
        return f"{y:04d}-{mo:02d}-{d:02d}"
    except ValueError:
        return None


def parse_score(raw: str) -> tuple[int | None, int | None, str]:
    s = raw.strip().lower()
    if "w/o" in s or s == "wo":
        return (None, None, "W")  # walkover, home advances
    m = SCORE_RX.match(raw)
    if not m:
        return (None, None, "U")
    h, a = int(m.group(1)), int(m.group(2))
    if h > a:
        return (h, a, "H")
    if a > h:
        return (h, a, "A")
    return (h, a, "D")


def parse_results_page(url: str) -> list[Match]:
    html = fetch(url)
    rows = extract_all_rows(html)
    matches: list[Match] = []
    for row in rows:
        if len(row) < 9:
            continue
        match_no_raw, date_raw, _gap1, home, score, away, _gap2, comp, venue = row[:9]
        # Filtrar cabeceras: la primera fila suele tener texto como
        # "... Result List 2020 - 2030 ... > Match no." en la primera celda,
        # lo que engaña a parse_match_no concatenando los años.
        if "match no" in match_no_raw.lower():
            continue
        if date_raw.lower().startswith("date of match"):
            continue
        if home.lower() in {"home", "home contenders"}:
            continue
        no = parse_match_no(match_no_raw)
        if no is None:
            continue
        # No descartamos la fila si la fecha no se puede parsear a ISO
        # (algunas son "Not played", "tbc", o multidía con formato raro);
        # date_raw queda como texto y date_iso será NULL.
        if not home or not away:
            continue
        hg, ag, result = parse_score(score)
        matches.append(
            Match(
                match_no=no,
                date_raw=date_raw.strip(),
                date_iso=parse_date(date_raw),
                home=home.strip(),
                away=away.strip(),
                score=score.strip(),
                home_goals=hg,
                away_goals=ag,
                result=result,
                competition=comp.strip(),
                venue=venue.strip(),
                source_url=url,
            )
        )
    return matches


# ---------------------- Champion lineage ----------------------

def assign_champions(matches: list[Match]) -> list[tuple[Match, str]]:
    """Devuelve [(match, champion_after)] aplicando regla boxing."""
    result: list[tuple[Match, str]] = []
    champion: str | None = None
    for m in matches:
        if champion is None:
            # Primer partido: el campeón inicial es el ganador.
            if m.result == "H" or m.result == "W":
                champion = m.home
            elif m.result == "A":
                champion = m.away
            else:  # empate en el primerísimo: convención → local.
                champion = m.home
        else:
            if m.result == "H" or m.result == "W":
                champion = m.home
            elif m.result == "A":
                champion = m.away
            # empate o desconocido → retiene
        result.append((m, champion))
    return result


# ---------------------- SQLite ----------------------

DDL = """
DROP TABLE IF EXISTS matches;
DROP TABLE IF EXISTS reigns;

CREATE TABLE matches (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    match_no        INTEGER,
    date_raw        TEXT,
    date_iso        TEXT,
    home            TEXT NOT NULL,
    away            TEXT NOT NULL,
    score           TEXT,
    home_goals      INTEGER,
    away_goals      INTEGER,
    result          TEXT,           -- H, A, D, W, U
    competition     TEXT,
    venue           TEXT,
    champion_after  TEXT NOT NULL,
    source_url      TEXT
);
CREATE INDEX idx_matches_date    ON matches(date_iso);
CREATE INDEX idx_matches_match_no ON matches(match_no);
CREATE INDEX idx_matches_champion ON matches(champion_after);

CREATE TABLE reigns (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    club            TEXT NOT NULL,
    start_match_id  INTEGER NOT NULL REFERENCES matches(id),
    end_match_id    INTEGER NOT NULL REFERENCES matches(id),
    matches_held    INTEGER NOT NULL,
    started_on      TEXT,
    ended_on        TEXT
);
CREATE INDEX idx_reigns_club ON reigns(club);
"""


def write_db(rows: list[tuple[Match, str]]) -> None:
    con = sqlite3.connect(DB_PATH)
    try:
        con.executescript(DDL)
        cur = con.cursor()
        match_ids: list[int] = []
        for m, champ in rows:
            cur.execute(
                """
                INSERT INTO matches (
                    match_no, date_raw, date_iso, home, away, score,
                    home_goals, away_goals, result, competition, venue,
                    champion_after, source_url
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    m.match_no, m.date_raw, m.date_iso, m.home, m.away, m.score,
                    m.home_goals, m.away_goals, m.result, m.competition, m.venue,
                    champ, m.source_url,
                ),
            )
            match_ids.append(cur.lastrowid)

        # Compactar reinados consecutivos.
        if rows:
            cur_champ = rows[0][1]
            cur_start = 0
            for i in range(1, len(rows) + 1):
                if i == len(rows) or rows[i][1] != cur_champ:
                    start = rows[cur_start][0]
                    end = rows[i - 1][0]
                    cur.execute(
                        """
                        INSERT INTO reigns (
                            club, start_match_id, end_match_id,
                            matches_held, started_on, ended_on
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            cur_champ,
                            match_ids[cur_start],
                            match_ids[i - 1],
                            i - cur_start,
                            start.date_iso,
                            end.date_iso,
                        ),
                    )
                    if i < len(rows):
                        cur_champ = rows[i][1]
                        cur_start = i
        con.commit()
    finally:
        con.close()


# ---------------------- Main ----------------------

def main() -> int:
    force = "--force" in sys.argv
    if force and CACHE_DIR.exists():
        for p in CACHE_DIR.iterdir():
            p.unlink()

    pages = discover_result_pages()
    print(f"Discovered {len(pages)} result pages:")
    for p in pages:
        print("  ", p)

    all_matches: list[Match] = []
    seen_match_no: set[int] = set()
    for url in pages:
        page_matches = parse_results_page(url)
        # Dedupe entre páginas (la página base se solapa con la primera década
        # en algunos años de transición). Conservamos primera aparición por match_no.
        added = 0
        for m in page_matches:
            if m.match_no is not None and m.match_no in seen_match_no:
                continue
            if m.match_no is not None:
                seen_match_no.add(m.match_no)
            all_matches.append(m)
            added += 1
        print(f"  {url.rsplit('/', 1)[-1]}: parsed {len(page_matches)}, kept {added}")

    # Inyectar partidos manuales que faltan en la fuente.
    for raw in MANUAL_MATCHES:
        if raw["match_no"] in seen_match_no:
            continue
        hg, ag, result = parse_score(raw["score"])
        all_matches.append(Match(
            match_no=raw["match_no"],
            date_raw=raw["date_raw"],
            date_iso=parse_date(raw["date_raw"]),
            home=raw["home"],
            away=raw["away"],
            score=raw["score"],
            home_goals=hg,
            away_goals=ag,
            result=result,
            competition=raw["competition"],
            venue=raw["venue"],
            source_url=raw["source_url"],
        ))
        seen_match_no.add(raw["match_no"])
        print(f"  [manual] inserted match #{raw['match_no']}: "
              f"{raw['home']} {raw['score']} {raw['away']}")

    # Ordenar por número de partido global (que es el orden cronológico oficial).
    all_matches.sort(key=lambda x: (x.match_no if x.match_no is not None else 1 << 30))

    with_champ = assign_champions(all_matches)
    write_db(with_champ)

    print()
    print(f"Total matches stored: {len(with_champ)}")
    if with_champ:
        first_m, _ = with_champ[0]
        last_m, last_champ = with_champ[-1]
        print(f"First match #{first_m.match_no} on {first_m.date_raw}: "
              f"{first_m.home} {first_m.score} {first_m.away}")
        print(f"Last  match #{last_m.match_no} on {last_m.date_raw}: "
              f"{last_m.home} {last_m.score} {last_m.away}")
        print(f"Current UFCC champion: {last_champ}")
    print(f"DB: {DB_PATH.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

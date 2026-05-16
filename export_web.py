"""
Exporta ufcc.db a JSON compacto para la web estática + copia los escudos.

Sale:
  docs/data/matches.json   -> array de partidos (ordenado descendente por match_no)
  docs/data/clubs.json     -> {nombre: crest_filename}
  docs/data/years.json     -> [{year, first_index, count}] para la timeline
  docs/crests/*            -> copia de crests/ (solo los referenciados)

Formato matches.json — cada item es un array compacto (menos overhead que objeto):
  [match_no, date_iso, home, score, away, result, competition, venue, champion]

result: 'H'/'A'/'D'/'W'/'U'
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

DB = Path("ufcc.db")
DOCS = Path("docs")
DATA = DOCS / "data"
CRESTS_SRC = Path("crests")
CRESTS_DST = DOCS / "crests"


def main() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    CRESTS_DST.mkdir(parents=True, exist_ok=True)

    con = sqlite3.connect(DB)
    try:
        cur = con.cursor()

        # 1) Clubs con escudo (mapa nombre -> filename relativo).
        clubs: dict[str, str] = {}
        used_files: set[str] = set()
        for name, crest_path in cur.execute(
            "SELECT name, crest_path FROM clubs WHERE crest_path IS NOT NULL"
        ):
            fname = Path(crest_path).name
            clubs[name] = fname
            used_files.add(fname)

        # 2) Matches ordenados DESC por match_no (recientes arriba).
        rows = cur.execute(
            """
            SELECT match_no, date_iso, home, score, away, result,
                   competition, venue, champion_after
            FROM matches
            ORDER BY match_no DESC
            """
        ).fetchall()
        matches = [list(r) for r in rows]

        # 3) Índice por año (para timeline). Asume orden DESC por fecha,
        #    coincide con DESC por match_no.
        years: list[dict] = []
        cur_year = None
        cur_start = 0
        cur_count = 0
        for i, m in enumerate(matches):
            date_iso = m[1]
            y = int(date_iso[:4]) if date_iso else None
            if y is None:
                continue
            if cur_year is None:
                cur_year, cur_start = y, i
            if y != cur_year:
                years.append({"year": cur_year, "first_index": cur_start, "count": cur_count})
                cur_year, cur_start, cur_count = y, i, 0
            cur_count += 1
        if cur_year is not None:
            years.append({"year": cur_year, "first_index": cur_start, "count": cur_count})
    finally:
        con.close()

    (DATA / "matches.json").write_text(
        json.dumps(matches, ensure_ascii=False, separators=(",", ":"))
    )
    (DATA / "clubs.json").write_text(
        json.dumps(clubs, ensure_ascii=False, separators=(",", ":"))
    )
    (DATA / "years.json").write_text(
        json.dumps(years, ensure_ascii=False, separators=(",", ":"))
    )

    # 4) Copiar solo los escudos referenciados.
    copied = 0
    for fname in used_files:
        src = CRESTS_SRC / fname
        if not src.exists():
            continue
        dst = CRESTS_DST / fname
        if not dst.exists() or dst.stat().st_size != src.stat().st_size:
            shutil.copy2(src, dst)
        copied += 1

    print(f"matches: {len(matches)}  clubs(with crest): {len(clubs)}  years: {len(years)}")
    print(f"crests copied to docs/crests: {copied}")
    print("sizes:")
    for f in sorted(DATA.glob("*.json")):
        print(f"  {f.name}: {f.stat().st_size/1024:.1f} KB")


if __name__ == "__main__":
    main()

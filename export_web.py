"""
Exporta ufcc.db a JSON compacto para la web estática + copia los escudos.

Sale:
  docs/data/matches.json          partidos DESC por match_no (compactos)
  docs/data/clubs.json            {nombre: crest_filename}
  docs/data/years.json            [{year, first_index, count}] timeline
  docs/data/rankings.json         ranking acumulado por club
  docs/data/longest_reigns.json   reinados individuales más largos
  docs/data/countries.json        agregado por país (vía bbox lat/lon)
  docs/data/champions_geo.json    geo para el mapa
  docs/data/stats.json            stats globales
  docs/crests/*                   copia de crests/ (solo los referenciados)
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import date
from pathlib import Path

DB = Path("ufcc.db")
DOCS = Path("docs")
DATA = DOCS / "data"
CRESTS_SRC = Path("crests")
CRESTS_DST = DOCS / "crests"


# ---------------------- Country mapping ----------------------

# Bounding boxes (lat_min, lat_max, lon_min, lon_max). Orden importa: el
# primero que casa gana. Heurística buena para los ~456 clubes UFCC.
COUNTRY_BBOXES: list[tuple[str, tuple[float, float, float, float]]] = [
    ("Ireland",        (51.4, 55.5, -10.6, -6.4)),
    ("United Kingdom", (49.5, 60.9, -8.7,  1.9)),
    ("Portugal",       (36.8, 42.2, -9.6, -6.2)),
    ("Spain",          (35.9, 43.9, -9.4,  3.4)),
    ("France",         (41.3, 51.1, -5.3,  9.6)),
    ("Belgium",        (49.5, 51.6,  2.5,  6.5)),
    ("Netherlands",    (50.7, 53.6,  3.2,  7.3)),
    ("Luxembourg",     (49.4, 50.2,  5.7,  6.6)),
    ("Switzerland",    (45.8, 47.9,  5.9, 10.6)),
    ("Italy",          (36.5, 47.1,  6.6, 18.6)),
    ("Austria",        (46.4, 49.1,  9.5, 17.2)),
    ("Germany",        (47.2, 55.1,  5.8, 15.1)),
    ("Czech Republic", (48.5, 51.1, 12.0, 18.9)),
    ("Slovakia",       (47.7, 49.7, 16.8, 22.6)),
    ("Poland",         (49.0, 54.9, 14.1, 24.2)),
    ("Hungary",        (45.7, 48.6, 16.1, 22.9)),
    ("Slovenia",       (45.4, 46.9, 13.4, 16.6)),
    ("Croatia",        (42.4, 46.6, 13.5, 19.5)),
    ("Bosnia and Herzegovina", (42.6, 45.3, 15.7, 19.6)),
    ("Serbia",         (42.2, 46.2, 18.8, 23.0)),
    ("Montenegro",     (41.8, 43.6, 18.4, 20.4)),
    ("North Macedonia",(40.8, 42.4, 20.4, 23.0)),
    ("Albania",        (39.6, 42.7, 19.2, 21.1)),
    ("Greece",         (34.8, 41.8, 19.3, 28.3)),
    ("Bulgaria",       (41.2, 44.2, 22.4, 28.6)),
    ("Romania",        (43.6, 48.3, 20.3, 29.7)),
    ("Denmark",        (54.5, 57.8,  8.1, 12.8)),
    ("Sweden",         (55.3, 69.1, 10.9, 24.2)),
    ("Norway",         (57.9, 71.2,  4.6, 31.1)),
    ("Finland",        (59.7, 70.1, 19.5, 31.6)),
    ("Ukraine",        (44.4, 52.4, 22.1, 40.2)),
    ("Russia",         (41.2, 81.9, 19.6, 180.0)),
    ("Turkey",         (35.8, 42.1, 25.6, 44.8)),
    ("Brazil",         (-33.8,  5.4, -73.9, -34.7)),
    ("Argentina",      (-55.1, -21.8, -73.5, -53.6)),
    ("Uruguay",        (-35.0, -30.1, -58.5, -53.0)),
    ("USA",            (24.4, 49.4, -125.0, -66.9)),
    ("Mexico",         (14.5, 32.7, -118.4, -86.7)),
]


def country_for(lat: float | None, lon: float | None) -> str:
    if lat is None or lon is None:
        return "Other"
    for name, (la, lb, na, nb) in COUNTRY_BBOXES:
        if la <= lat <= lb and na <= lon <= nb:
            return name
    return "Other"


# Pista por prefijo de competición (mucho más fiable que bbox para
# los clubes que no salen en el KML de coordenadas).
COMP_COUNTRY_HINTS = {
    "English": "United Kingdom",
    "Scottish": "United Kingdom",
    "Welsh": "United Kingdom",
    "Irish": "Ireland",
    "FA": "United Kingdom",
    "Football": "United Kingdom",
    "Premier": "United Kingdom",
    "Southern": "United Kingdom",
    "Western": "United Kingdom",
    "London": "United Kingdom",
    "Spanish": "Spain",
    "Copa": "Spain",
    "French": "France",
    "Coupe": "France",
    "Coupes": "France",
    "Ligues": "France",
    "German": "Germany",
    "Bundesliga": "Germany",
    "Regionalliga": "Germany",
    "Sudwestdeutscher": "Germany",
    "Oberliga": "Germany",
    "Qualifikationsrunde": "Germany",
    "DFB-Pokal": "Germany",
    "West": "Germany",
    "Italian": "Italy",
    "Serie": "Italy",
    "Coppa": "Italy",
    "Portuguese": "Portugal",
    "Primeira": "Portugal",
    "Taca": "Portugal",
    "Dutch": "Netherlands",
    "Eredivisie": "Netherlands",
    "KNVB": "Netherlands",
    "Belgian": "Belgium",
    "Swiss": "Switzerland",
    "Austrian": "Austria",
    "Hungarian": "Hungary",
    "Magyar": "Hungary",
    "Yugoslavian": "Yugoslavia",
    "Turkish": "Turkey",
    "Süper": "Turkey",
    "Brazilian": "Brazil",
    "Brasileiro": "Brazil",
    "Czech": "Czech Republic",
    "Czechoslovakian": "Czechoslovakia",
    "Slovakian": "Slovakia",
    "Polish": "Poland",
    "Romanian": "Romania",
    "Bulgarian": "Bulgaria",
    "Swedish": "Sweden",
    "Norwegian": "Norway",
    "Russian": "Russia",
    "Greek": "Greece",
    "Danish": "Denmark",
    "Argentinian": "Argentina",
    "Argentinean": "Argentina",
    "Uruguayan": "Uruguay",
    "Mexican": "Mexico",
    "American": "USA",
}


def country_from_competitions(comps: dict[str, int]) -> str | None:
    """Voto por país basado en las competiciones del club."""
    score: dict[str, int] = {}
    for comp, n in comps.items():
        if not comp:
            continue
        first = comp.split(" ", 1)[0]
        country = COMP_COUNTRY_HINTS.get(first)
        if country is None:
            continue
        score[country] = score.get(country, 0) + n
    if not score:
        return None
    return max(score.items(), key=lambda kv: kv[1])[0]


# ---------------------- Helpers ----------------------

def days_between(a: str, b: str) -> int:
    if not a or not b:
        return 1
    da = date.fromisoformat(a)
    db_ = date.fromisoformat(b)
    return max(1, (db_ - da).days)


# Periodos de suspensión de fútbol por las dos guerras mundiales.
# Las ligas europeas estuvieron paradas (o reemplazadas por torneos
# regionales no oficiales) entre estas fechas, así que los días no
# cuentan como "defensa" del título.
WARTIME_RANGES = [
    (date(1914, 8, 1), date(1919, 8, 31)),   # WWI
    (date(1939, 9, 1), date(1946, 8, 31)),   # WWII
]


def wartime_days(a: str, b: str) -> int:
    """Suma de días dentro de los periodos de guerra entre a y b."""
    if not a or not b:
        return 0
    da = date.fromisoformat(a)
    db_ = date.fromisoformat(b)
    if db_ <= da:
        return 0
    total = 0
    for ws, we in WARTIME_RANGES:
        lo = max(da, ws)
        hi = min(db_, we)
        if hi > lo:
            total += (hi - lo).days
    return total


def adjusted_days(a: str, b: str) -> int:
    """days_between menos overlap con periodos de guerra."""
    raw = days_between(a, b)
    return max(1, raw - wartime_days(a, b))


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

        # 2) Matches ordenados DESC por match_no.
        rows = cur.execute(
            """
            SELECT match_no, date_iso, home, score, away, result,
                   competition, venue, champion_after
            FROM matches
            ORDER BY match_no DESC
            """
        ).fetchall()
        matches = [list(r) for r in rows]

        # 3) Índice por año para timeline.
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

        # 4) Geo de campeones (lat/lon de champions).
        geo_raw = {
            name: (lat, lon)
            for name, lat, lon in cur.execute(
                "SELECT name, lat, lon FROM champions"
            )
        }

        # 4b) Competiciones por club (para deducir país sin coords).
        club_comps: dict[str, dict[str, int]] = {}
        for m in matches:
            home, away, comp = m[2], m[4], m[6]
            for who in (home, away):
                if not who:
                    continue
                bucket = club_comps.setdefault(who, {})
                if comp:
                    bucket[comp] = bucket.get(comp, 0) + 1

        def country_for_club(name: str) -> str:
            country = country_from_competitions(club_comps.get(name, {}))
            if country:
                return country
            lat, lon = geo_raw.get(name, (None, None))
            return country_for(lat, lon)

        # 5) Reigns + métricas por club.
        reigns_rows = cur.execute(
            """
            SELECT club, matches_held, started_on, ended_on
            FROM reigns
            ORDER BY started_on ASC
            """
        ).fetchall()

        # Día actual: para el campeón vigente, "días" se cuenta hasta hoy.
        # El último reign en reigns_rows tiene ended_on = fecha del último match.
        # El campeón actual recibe un bonus si su ultimo reign sigue abierto.
        # Pero como conceptualmente cada partido lo "renueva", basta con que el
        # reinado activo cuente desde started_on hasta hoy.
        today_iso = date.today().isoformat()
        last_reign_idx = len(reigns_rows) - 1

        # 5a) Acumulado por club (rankings):
        per_club_days: dict[str, int] = {}
        per_club_matches: dict[str, int] = {}
        per_club_reigns: dict[str, int] = {}
        for idx, (club, held, start, end) in enumerate(reigns_rows):
            ref_end = today_iso if idx == last_reign_idx else (end or start)
            d = adjusted_days(start, ref_end)
            per_club_days[club] = per_club_days.get(club, 0) + d
            per_club_matches[club] = per_club_matches.get(club, 0) + held
            per_club_reigns[club] = per_club_reigns.get(club, 0) + 1

        rankings = [
            {
                "name": club,
                "days": per_club_days[club],
                "matches": per_club_matches[club],
                "reigns": per_club_reigns[club],
                "crest": clubs.get(club),
            }
            for club in sorted(
                per_club_matches,
                key=lambda c: (-per_club_matches[c], -per_club_days[c]),
            )
        ]

        # 5b) Reinados individuales más largos.
        single_reigns = []
        for idx, (club, held, start, end) in enumerate(reigns_rows):
            ref_end = today_iso if idx == last_reign_idx else (end or start)
            wartime = wartime_days(start, ref_end)
            single_reigns.append({
                "club": club,
                "started_on": start,
                "ended_on": ref_end,
                "days": adjusted_days(start, ref_end),
                "days_raw": days_between(start, ref_end),
                "wartime_days": wartime,
                "matches": held,
                "is_current": idx == last_reign_idx,
                "crest": clubs.get(club),
            })
        single_reigns.sort(key=lambda r: (-r["matches"], -r["days"]))
        longest_reigns = single_reigns[:100]

        # 5c) Por país.
        country_days: dict[str, int] = {}
        country_matches: dict[str, int] = {}
        country_clubs: dict[str, dict[str, int]] = {}
        for club, days in per_club_days.items():
            country = country_for_club(club)
            country_days[country] = country_days.get(country, 0) + days
            country_matches[country] = country_matches.get(country, 0) + per_club_matches[club]
            country_clubs.setdefault(country, {})[club] = days
        countries = []
        for country in sorted(country_days, key=lambda c: -country_days[c]):
            top_clubs = sorted(
                country_clubs[country].items(),
                key=lambda kv: -kv[1],
            )[:10]
            countries.append({
                "country": country,
                "days": country_days[country],
                "matches": country_matches[country],
                "clubs_total": len(country_clubs[country]),
                "top_clubs": [
                    {"name": n, "days": d, "crest": clubs.get(n)} for n, d in top_clubs
                ],
            })

        # 5d) Geo (para el mapa): un punto por club con lat/lon y días acumulados.
        champions_geo = []
        for club, days in per_club_days.items():
            lat, lon = geo_raw.get(club, (None, None))
            if lat is None or lon is None:
                continue
            champions_geo.append({
                "name": club,
                "lat": lat,
                "lon": lon,
                "days": days,
                "matches": per_club_matches[club],
                "reigns": per_club_reigns[club],
                "crest": clubs.get(club),
            })
        champions_geo.sort(key=lambda x: -x["days"])

        # 5e) Stats globales.
        total_matches = len(matches)
        total_clubs_in_matches = len({m[2] for m in matches} | {m[4] for m in matches})
        total_champions = len(per_club_days)
        total_reigns = len(reigns_rows)
        first_match = matches[-1] if matches else None
        last_match = matches[0] if matches else None
        # Reinado más corto (por partidos defendidos, luego días)
        shortest = min(single_reigns, key=lambda r: (r["matches"], r["days"])) if single_reigns else None
        # Más cambios de campeón por año
        changes_per_year: dict[int, int] = {}
        for idx in range(1, len(reigns_rows)):
            start = reigns_rows[idx][2]
            if not start:
                continue
            y = int(start[:4])
            changes_per_year[y] = changes_per_year.get(y, 0) + 1
        most_changes_year = max(changes_per_year.items(), key=lambda kv: kv[1]) if changes_per_year else None

        stats = {
            "total_matches": total_matches,
            "total_clubs": total_clubs_in_matches,
            "total_champions": total_champions,
            "total_reigns": total_reigns,
            "first_date": first_match[1] if first_match else None,
            "last_date": last_match[1] if last_match else None,
            "longest_reign": {
                "club": longest_reigns[0]["club"],
                "days": longest_reigns[0]["days"],
                "matches": longest_reigns[0]["matches"],
                "started_on": longest_reigns[0]["started_on"],
                "ended_on": longest_reigns[0]["ended_on"],
            } if longest_reigns else None,
            "shortest_reign": {
                "club": shortest["club"],
                "days": shortest["days"],
                "matches": shortest["matches"],
                "started_on": shortest["started_on"],
                "ended_on": shortest["ended_on"],
            } if shortest else None,
            "most_changes_year": (
                {"year": most_changes_year[0], "changes": most_changes_year[1]}
                if most_changes_year else None
            ),
            "current_champion": matches[0][8] if matches else None,
        }
    finally:
        con.close()

    def _w(name: str, obj) -> None:
        (DATA / name).write_text(
            json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
        )

    _w("matches.json", matches)
    _w("clubs.json", clubs)
    _w("years.json", years)
    _w("rankings.json", rankings)
    _w("longest_reigns.json", longest_reigns)
    _w("countries.json", countries)
    _w("champions_geo.json", champions_geo)
    _w("stats.json", stats)

    # 6) Copiar solo los escudos referenciados.
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
    print(f"rankings: {len(rankings)}  countries: {len(countries)}  geo: {len(champions_geo)}")
    print(f"crests copied to docs/crests: {copied}")
    print("sizes:")
    for f in sorted(DATA.glob("*.json")):
        print(f"  {f.name}: {f.stat().st_size/1024:.1f} KB")


if __name__ == "__main__":
    main()


"""
Actualización incremental del linaje UFCC con la API de api-football.com.

Estrategia:
  1) Lee el campeón vigente de ufcc.db (matches.champion_after del último match).
  2) Para ese club, pide a la API los fixtures FINALIZADOS desde la fecha del
     último partido del campeón en BD + 1 día, hasta hoy.
  3) Por cada fixture nuevo en orden cronológico:
       - Inserta en `matches` con el siguiente match_no.
       - Aplica regla "boxing": ganador del partido (o empate → retiene)
         pasa a ser el nuevo `champion_after`.
       - Actualiza `reigns` (extiende el reinado vigente o cierra y abre uno).
  4) Si en algún momento el campeón cambia, vuelve a 1 con el nuevo club.
     Hasta MAX_CHAMPION_SWAPS por ejecución (corta runaway de requests).

Requisitos:
  - Variable de entorno API_FOOTBALL_KEY (header x-apisports-key).
  - data/api_team_map.json: cache nombre_club -> api_team_id (persistente).

Limitaciones honestas:
  - Penaltis: la API marca status=PEN y `teams.{home,away}.winner` decide.
    Lo usamos como ganador para H/A; `goals` refleja 90+120 min.
  - Friendlies vs oficiales: incluimos TODO partido senior del campeón
    (lo mismo que hace la fuente histórica de stevesfootballstats).
  - Nombre del club: si no hay mapping, se llama a /teams?search=<nombre>;
    si devuelve >1 resultado, se aborta y se loggea para mapeo manual.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

DB_PATH = Path("ufcc.db")
TEAM_MAP_PATH = Path("data/api_team_map.json")
API_BASE = "https://v3.football.api-sports.io"
API_KEY = os.environ.get("API_FOOTBALL_KEY", "").strip()
MAX_CHAMPION_SWAPS = 10
TIMEOUT = 30
FINISHED_STATUSES = {"FT", "AET", "PEN"}


# ---------------------- HTTP ----------------------

def api_get(path: str, params: dict[str, str | int]) -> dict:
    if not API_KEY:
        sys.exit("ERROR: API_FOOTBALL_KEY no está definida.")
    qs = urllib.parse.urlencode(params)
    url = f"{API_BASE}{path}?{qs}"
    req = urllib.request.Request(
        url,
        headers={
            "x-apisports-key": API_KEY,
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if data.get("errors"):
        # La API devuelve `errors` como dict u objeto vacío []
        errs = data["errors"]
        if isinstance(errs, dict) and errs:
            raise RuntimeError(f"API error en {path}: {errs}")
    return data


# ---------------------- Team mapping ----------------------

def load_team_map() -> dict[str, int]:
    if TEAM_MAP_PATH.exists():
        return json.loads(TEAM_MAP_PATH.read_text("utf-8"))
    return {}


def save_team_map(m: dict[str, int]) -> None:
    TEAM_MAP_PATH.parent.mkdir(parents=True, exist_ok=True)
    TEAM_MAP_PATH.write_text(
        json.dumps(m, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        "utf-8",
    )


def resolve_team_id(name: str, team_map: dict[str, int]) -> int | None:
    if name in team_map:
        return team_map[name]
    print(f"  · buscando team_id para '{name}'...")
    data = api_get("/teams", {"search": name})
    results = data.get("response", [])
    if not results:
        print(f"  · WARN: sin resultados para '{name}'")
        return None
    # Coincidencia exacta de nombre, si la hay.
    exact = [r for r in results if r["team"]["name"].lower() == name.lower()]
    chosen = exact[0] if exact else (results[0] if len(results) == 1 else None)
    if chosen is None:
        names = ", ".join(f"{r['team']['name']} ({r['team']['country']})" for r in results[:5])
        print(f"  · WARN: ambiguo para '{name}' → {names}")
        return None
    team_id = int(chosen["team"]["id"])
    team_map[name] = team_id
    save_team_map(team_map)
    print(f"  · cacheado '{name}' → {team_id} ({chosen['team']['name']})")
    return team_id


# ---------------------- DB helpers ----------------------

def current_champion_state(con: sqlite3.Connection) -> tuple[str, str, int]:
    """Devuelve (champion, last_date_iso, last_match_no)."""
    cur = con.cursor()
    cur.execute(
        "SELECT champion_after, date_iso, match_no FROM matches "
        "ORDER BY match_no DESC LIMIT 1"
    )
    row = cur.fetchone()
    if not row:
        sys.exit("ERROR: ufcc.db sin partidos.")
    return row[0], row[1], int(row[2])


def insert_match_and_update_reign(
    con: sqlite3.Connection,
    *,
    match_no: int,
    date_iso: str,
    date_raw: str,
    home: str,
    away: str,
    home_goals: int,
    away_goals: int,
    result: str,
    competition: str,
    venue: str,
    champion_before: str,
    champion_after: str,
    source_url: str,
) -> None:
    cur = con.cursor()
    score = f"{home_goals}-{away_goals}"
    cur.execute(
        """
        INSERT INTO matches (
            match_no, date_raw, date_iso, home, away, score,
            home_goals, away_goals, result, competition, venue,
            champion_after, source_url
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            match_no, date_raw, date_iso, home, away, score,
            home_goals, away_goals, result, competition, venue,
            champion_after, source_url,
        ),
    )
    new_match_id = cur.lastrowid

    if champion_after == champion_before:
        # Extiende el reinado actual (que pertenece a champion_before).
        cur.execute(
            "SELECT id, matches_held FROM reigns "
            "WHERE club = ? ORDER BY id DESC LIMIT 1",
            (champion_before,),
        )
        r = cur.fetchone()
        if r is None:
            # No debería pasar nunca, pero abrimos uno por defensa.
            cur.execute(
                """
                INSERT INTO reigns (
                    club, start_match_id, end_match_id, matches_held,
                    started_on, ended_on
                ) VALUES (?, ?, ?, 1, ?, ?)
                """,
                (champion_before, new_match_id, new_match_id, date_iso, date_iso),
            )
        else:
            reign_id, held = r
            cur.execute(
                "UPDATE reigns SET end_match_id = ?, matches_held = ?, "
                "ended_on = ? WHERE id = ?",
                (new_match_id, held + 1, date_iso, reign_id),
            )
    else:
        # Cambio de campeón: abre un reinado nuevo para champion_after de 1 partido
        # (este mismo partido cuenta para el nuevo campeón).
        cur.execute(
            """
            INSERT INTO reigns (
                club, start_match_id, end_match_id, matches_held,
                started_on, ended_on
            ) VALUES (?, ?, ?, 1, ?, ?)
            """,
            (champion_after, new_match_id, new_match_id, date_iso, date_iso),
        )


# ---------------------- Fixture processing ----------------------

def fetch_finished_fixtures(team_id: int, since_iso: str) -> list[dict]:
    """Pide fixtures FT/AET/PEN entre `since_iso` (exclusivo) y hoy.

    api-football exige `season` cuando filtras por `team`. Como una ventana
    de fechas puede cruzar temporadas (ej. mayo→agosto), pedimos cada año
    necesario y fusionamos.
    """
    today = date.today()
    since = date.fromisoformat(since_iso) + timedelta(days=1)
    if since > today:
        return []
    since_str = since.isoformat()
    today_str = today.isoformat()
    seasons = sorted({since.year, today.year})
    merged: dict[int, dict] = {}
    for season in seasons:
        data = api_get(
            "/fixtures",
            {
                "team": team_id,
                "season": season,
                "from": since_str,
                "to": today_str,
                "timezone": "UTC",
            },
        )
        for f in data.get("response", []):
            short = f["fixture"]["status"]["short"]
            if short in FINISHED_STATUSES:
                merged[int(f["fixture"]["id"])] = f
    out = list(merged.values())
    out.sort(key=lambda f: f["fixture"]["date"])
    return out


def fixture_to_row(f: dict, champion: str) -> dict:
    home = f["teams"]["home"]["name"]
    away = f["teams"]["away"]["name"]
    hg = f["goals"]["home"] or 0
    ag = f["goals"]["away"] or 0
    home_winner = f["teams"]["home"].get("winner")
    away_winner = f["teams"]["away"].get("winner")
    if home_winner is True:
        result = "H"
        winner = home
    elif away_winner is True:
        result = "A"
        winner = away
    else:
        result = "D"
        winner = champion  # empate → retiene
    league = f["league"]["name"]
    venue_name = (f["fixture"]["venue"] or {}).get("name") or ""
    venue_city = (f["fixture"]["venue"] or {}).get("city") or ""
    venue = " ".join(x for x in [venue_name, venue_city] if x).strip()
    iso_dt = f["fixture"]["date"]  # ISO 8601 con TZ
    d = datetime.fromisoformat(iso_dt.replace("Z", "+00:00")).date()
    date_iso = d.isoformat()
    date_raw = f"{d.day}/{d.month}/{d.year}"
    return {
        "home": home,
        "away": away,
        "home_goals": hg,
        "away_goals": ag,
        "result": result,
        "competition": league,
        "venue": venue,
        "date_iso": date_iso,
        "date_raw": date_raw,
        "winner": winner,
        "source_url": f"https://www.api-football.com/fixtures/{f['fixture']['id']}",
    }


# ---------------------- Main loop ----------------------

def run() -> int:
    team_map = load_team_map()
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA foreign_keys = ON")
    try:
        total_added = 0
        for swap in range(MAX_CHAMPION_SWAPS + 1):
            champion, last_date, last_no = current_champion_state(con)
            print(f"[{swap}] campeón vigente: {champion} (último partido {last_date}, #{last_no})")
            team_id = resolve_team_id(champion, team_map)
            if team_id is None:
                print("  · sin team_id, abortando esta iteración.")
                break
            fixtures = fetch_finished_fixtures(team_id, last_date)
            print(f"  · {len(fixtures)} fixture(s) nuevo(s) desde {last_date}")
            if not fixtures:
                break

            added_this_round = 0
            changed_champion = False
            for f in fixtures:
                row = fixture_to_row(f, champion)
                next_no = last_no + 1
                # ¿Saltamos si esta fecha+rivales ya está? (defensa contra duplicados)
                cur = con.cursor()
                cur.execute(
                    "SELECT 1 FROM matches WHERE date_iso = ? AND home = ? AND away = ?",
                    (row["date_iso"], row["home"], row["away"]),
                )
                if cur.fetchone():
                    print(f"  · duplicado, salto: {row['date_iso']} {row['home']}-{row['away']}")
                    last_no = next_no - 1  # no avanzamos numeración
                    continue

                insert_match_and_update_reign(
                    con,
                    match_no=next_no,
                    date_iso=row["date_iso"],
                    date_raw=row["date_raw"],
                    home=row["home"],
                    away=row["away"],
                    home_goals=row["home_goals"],
                    away_goals=row["away_goals"],
                    result=row["result"],
                    competition=row["competition"],
                    venue=row["venue"],
                    champion_before=champion,
                    champion_after=row["winner"],
                    source_url=row["source_url"],
                )
                con.commit()
                total_added += 1
                added_this_round += 1
                print(
                    f"  + #{next_no} {row['date_iso']} "
                    f"{row['home']} {row['home_goals']}-{row['away_goals']} {row['away']} "
                    f"[{row['result']}] → {row['winner']}"
                )
                last_no = next_no
                if row["winner"] != champion:
                    changed_champion = True
                    print(f"  ! cambio de campeón: {champion} → {row['winner']}")
                    break  # re-pedir fixtures del nuevo campeón

            if not changed_champion:
                # Procesados todos los fixtures del campeón actual sin cambios:
                # nada más que hacer hasta el próximo run.
                break

        print(f"Total partidos añadidos: {total_added}")
        return 0 if total_added >= 0 else 1
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(run())

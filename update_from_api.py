"""
Actualización incremental del linaje UFCC con football-data.org (API v4).

Lee el campeón actual de ufcc.db, pide sus partidos FINISHED desde la última
fecha en BD, los inserta aplicando la regla "boxing" y actualiza reigns.
Si el campeón cambia, vuelve a empezar con el nuevo (hasta MAX_CHAMPION_SWAPS).

Auth: header `X-Auth-Token` con $FOOTBALL_DATA_KEY.
Cobertura free: PL, ELC, BL1, SA, PD, FL1, PPL, DED, BSA, CL, EC, WC.
Si el campeón cae fuera de esas ligas, el script lo loggea y añades su id
manualmente a data/api_team_map.json.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

DB_PATH = Path("ufcc.db")
TEAM_MAP_PATH = Path("data/api_team_map.json")
CRESTS_DIR = Path("crests")
API_BASE = "https://api.football-data.org/v4"
API_KEY = os.environ.get("FOOTBALL_DATA_KEY", "").strip()
MAX_CHAMPION_SWAPS = 10
TIMEOUT = 30
USER_AGENT = "ufcc-updater/1.0 (+personal project)"

FREE_TIER_COMPETITIONS = [
    "PD",   # La Liga
    "PL",   # Premier League
    "ELC",  # Championship
    "BL1",  # Bundesliga
    "SA",   # Serie A
    "FL1",  # Ligue 1
    "PPL",  # Primeira Liga
    "DED",  # Eredivisie
    "BSA",  # Brasileirão
    "CL",   # Champions League
    "EC",   # Eurocopa
    "WC",   # Mundial
]


# ---------------------- HTTP ----------------------

def api_get(path: str, params: dict[str, str | int] | None = None) -> dict:
    if not API_KEY:
        sys.exit("ERROR: FOOTBALL_DATA_KEY no está definida.")
    url = f"{API_BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url,
        headers={
            "X-Auth-Token": API_KEY,
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:200]
        raise RuntimeError(f"HTTP {e.code} en {path}: {body}") from None


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


def _norm(s: str) -> str:
    return s.lower().replace(".", "").replace(",", "").strip()


def resolve_team_id(name: str, team_map: dict[str, int]) -> int | None:
    if name in team_map:
        return team_map[name]
    print(f"  · buscando team_id para '{name}'...")
    target = _norm(name)
    for code in FREE_TIER_COMPETITIONS:
        try:
            data = api_get(f"/competitions/{code}/teams")
        except RuntimeError as e:
            print(f"  · WARN: {code} → {e}")
            time.sleep(6)
            continue
        for t in data.get("teams", []):
            for c in (t.get("name"), t.get("shortName"), t.get("tla")):
                if c and _norm(c) == target:
                    team_id = int(t["id"])
                    team_map[name] = team_id
                    save_team_map(team_map)
                    print(f"  · cacheado '{name}' → {team_id} via {code}")
                    return team_id
        time.sleep(6)  # 10 req/min en free
    print(f"  · WARN: '{name}' no está en ninguna competición del free tier")
    return None


# ---------------------- Crest download ----------------------

def _safe_filename(url: str) -> str:
    path = urllib.parse.urlparse(url).path
    name = path.rsplit("/", 1)[-1] or "crest"
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)


def _download(url: str, dest: Path) -> bool:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = resp.read()
        if not data:
            return False
        dest.write_bytes(data)
        return True
    except Exception as exc:
        print(f"  · WARN crest download failed {url}: {exc}")
        return False


def upsert_club(con: sqlite3.Connection, name: str, crest_url: str | None) -> None:
    """Asegura que el club existe en `clubs` y descarga su crest si falta."""
    cur = con.cursor()
    cur.execute("SELECT crest_url, crest_path FROM clubs WHERE name = ?", (name,))
    row = cur.fetchone()
    crest_path: str | None = None
    if crest_url:
        CRESTS_DIR.mkdir(exist_ok=True)
        fname = _safe_filename(crest_url)
        dest = CRESTS_DIR / fname
        if dest.exists() and dest.stat().st_size > 0:
            crest_path = str(dest)
        elif _download(crest_url, dest):
            crest_path = str(dest)
            print(f"  · crest descargado: {name} → {fname}")
    if row is None:
        cur.execute(
            "INSERT INTO clubs (name, crest_url, crest_path) VALUES (?, ?, ?)",
            (name, crest_url, crest_path),
        )
        print(f"  · club nuevo en BD: {name}")
    else:
        existing_url, existing_path = row
        new_url = crest_url or existing_url
        new_path = crest_path or existing_path
        if new_url != existing_url or new_path != existing_path:
            cur.execute(
                "UPDATE clubs SET crest_url = ?, crest_path = ? WHERE name = ?",
                (new_url, new_path, name),
            )


# ---------------------- DB helpers ----------------------

def current_champion_state(con: sqlite3.Connection) -> tuple[str, str, int]:
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
        cur.execute(
            "SELECT id, matches_held FROM reigns "
            "WHERE club = ? ORDER BY id DESC LIMIT 1",
            (champion_before,),
        )
        r = cur.fetchone()
        if r is None:
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
        cur.execute(
            """
            INSERT INTO reigns (
                club, start_match_id, end_match_id, matches_held,
                started_on, ended_on
            ) VALUES (?, ?, ?, 1, ?, ?)
            """,
            (champion_after, new_match_id, new_match_id, date_iso, date_iso),
        )


# ---------------------- Match processing ----------------------

def fetch_finished_matches(team_id: int, since_iso: str) -> list[dict]:
    today = date.today()
    # OJO: arrancamos en el mismo día del último partido para no perdernos un
    # segundo encuentro del campeón en la misma jornada. El chequeo de
    # duplicados (date_iso + home + away) ya impide insertar dos veces.
    since = date.fromisoformat(since_iso)
    if since > today:
        return []
    data = api_get(
        f"/teams/{team_id}/matches",
        {
            "dateFrom": since.isoformat(),
            "dateTo": today.isoformat(),
            "status": "FINISHED",
        },
    )
    matches = data.get("matches", [])
    matches.sort(key=lambda m: m["utcDate"])
    return matches


def fetch_next_scheduled_match(team_id: int) -> dict | None:
    """Próximo partido SCHEDULED/TIMED del equipo, dentro de los siguientes 120 días."""
    today = date.today()
    horizon = today + timedelta(days=120)
    data = api_get(
        f"/teams/{team_id}/matches",
        {
            "dateFrom": today.isoformat(),
            "dateTo": horizon.isoformat(),
            "status": "SCHEDULED,TIMED",
        },
    )
    matches = data.get("matches", [])
    if not matches:
        return None
    matches.sort(key=lambda m: m["utcDate"])
    return matches[0]


def scheduled_to_payload(m: dict, champion: str) -> dict:
    home = m["homeTeam"]["name"]
    away = m["awayTeam"]["name"]
    iso_dt = m["utcDate"]
    competition = (m.get("competition") or {}).get("name", "")
    venue = m.get("venue") or ""
    return {
        "champion": champion,
        "kickoff_utc": iso_dt,
        "home": home,
        "away": away,
        "home_crest": m["homeTeam"].get("crest"),
        "away_crest": m["awayTeam"].get("crest"),
        "competition": competition,
        "venue": venue,
        "is_home": home == champion,
        "opponent": away if home == champion else home,
        "opponent_crest": m["awayTeam"].get("crest") if home == champion else m["homeTeam"].get("crest"),
    }


def match_to_row(m: dict, champion: str) -> dict:
    home = m["homeTeam"]["name"]
    away = m["awayTeam"]["name"]
    home_crest = m["homeTeam"].get("crest")
    away_crest = m["awayTeam"].get("crest")
    full = m["score"].get("fullTime") or {}
    hg = full.get("home") or 0
    ag = full.get("away") or 0
    winner_code = m["score"].get("winner")
    if winner_code == "HOME_TEAM":
        result, winner = "H", home
    elif winner_code == "AWAY_TEAM":
        result, winner = "A", away
    else:
        result, winner = "D", champion
    league = (m.get("competition") or {}).get("name", "")
    venue = m.get("venue") or ""
    iso_dt = m["utcDate"]
    d = datetime.fromisoformat(iso_dt.replace("Z", "+00:00")).date()
    return {
        "home": home,
        "away": away,
        "home_crest": home_crest,
        "away_crest": away_crest,
        "home_goals": hg,
        "away_goals": ag,
        "result": result,
        "competition": league,
        "venue": venue,
        "date_iso": d.isoformat(),
        "date_raw": f"{d.day}/{d.month}/{d.year}",
        "winner": winner,
        "source_url": f"https://www.football-data.org/match/{m['id']}",
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
            matches = fetch_finished_matches(team_id, last_date)
            print(f"  · {len(matches)} match(es) nuevo(s) desde {last_date}")
            if not matches:
                break

            changed_champion = False
            for m in matches:
                row = match_to_row(m, champion)
                next_no = last_no + 1
                cur = con.cursor()
                cur.execute(
                    "SELECT 1 FROM matches WHERE date_iso = ? AND home = ? AND away = ?",
                    (row["date_iso"], row["home"], row["away"]),
                )
                if cur.fetchone():
                    print(f"  · duplicado, salto: {row['date_iso']} {row['home']}-{row['away']}")
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
                upsert_club(con, row["home"], row["home_crest"])
                upsert_club(con, row["away"], row["away_crest"])
                con.commit()
                total_added += 1
                print(
                    f"  + #{next_no} {row['date_iso']} "
                    f"{row['home']} {row['home_goals']}-{row['away_goals']} {row['away']} "
                    f"[{row['result']}] → {row['winner']}"
                )
                last_no = next_no
                if row["winner"] != champion:
                    changed_champion = True
                    print(f"  ! cambio de campeón: {champion} → {row['winner']}")
                    break

            if not changed_champion:
                break

        print(f"Total partidos añadidos: {total_added}")

        # Próximo partido del campeón vigente.
        champion, _, _ = current_champion_state(con)
        team_id = resolve_team_id(champion, team_map)
        next_path = Path("docs/data/next_match.json")
        next_path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict | None = None
        if team_id is not None:
            try:
                m = fetch_next_scheduled_match(team_id)
                if m:
                    payload = scheduled_to_payload(m, champion)
                    print(
                        f"Próximo partido: {payload['kickoff_utc']} "
                        f"{payload['home']} vs {payload['away']} ({payload['competition']})"
                    )
                else:
                    print("Próximo partido: ninguno en los próximos 120 días.")
            except Exception as e:
                print(f"No se pudo obtener próximo partido: {e}")
        next_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) if payload else "null",
            encoding="utf-8",
        )

        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(run())

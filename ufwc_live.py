"""
Extensión "en vivo" del linaje UFWC (naciones) con football-data.org.

theufwc.com es la fuente principal: mantiene el linaje completo (amistosos,
penaltis, histórico) y `scrape_ufwc.py` reconstruye el dataset desde su API en
cada run. El problema es que esa web a veces se retrasa —típicamente durante un
torneo— y entonces nuestra web también se queda parada.

Este módulo sigue al campeón vigente (regla de boxeo: quien le gana le quita el
título) usando football-data.org, que SÍ cubre el Mundial, y añade al final del
dataset los partidos de título que theufwc.com todavía no ha publicado. Cuando
theufwc.com se pone al día, el rebuild completo vuelve a partir de su API y la
cola sintética se deduplica por (fecha, local, visitante): todo encaja sin
duplicar.

Sin FOOTBALL_DATA_KEY el módulo no hace nada y el dataset queda exactamente como
lo generó theufwc.com.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timezone

try:
    from update_from_api import (
        api_get,
        fetch_finished_matches,
        fetch_next_scheduled_match,
    )
except Exception:  # pragma: no cover - sin el updater de clubs no hay extensión
    api_get = None  # type: ignore[assignment]
    fetch_finished_matches = None  # type: ignore[assignment]
    fetch_next_scheduled_match = None  # type: ignore[assignment]

API_KEY = os.environ.get("FOOTBALL_DATA_KEY", "").strip()

# Competiciones donde football-data.org puede tener al campeón durante un torneo
# (free tier). El Mundial es la relevante ahora mismo.
COMPETITIONS = ["WC", "EC"]
MAX_SWAPS = 10

# Aliases football-data -> nombre canónico de theufwc, solo para los casos que
# NO resuelven por código FIFA (tla). Lo normal es que el tla del equipo en
# football-data coincida con el fifaCode de theufwc y no haga falta nada de esto.
NAME_ALIASES = {
    "turkiye": "Turkey",
    "korea republic": "South Korea",
    "korea dpr": "North Korea",
    "czechia": "Czech Republic",
    "cote d'ivoire": "Ivory Coast",
}


def _norm(s: str) -> str:
    return (s or "").lower().replace(".", "").replace(",", "").strip()


def _iso_from_ms(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date().isoformat()


def _iso_from_dt(iso_dt: str) -> str:
    return datetime.fromisoformat(iso_dt.replace("Z", "+00:00")).date().isoformat()


def _ms_at_date(iso_dt: str) -> int:
    d = datetime.fromisoformat(iso_dt.replace("Z", "+00:00")).date()
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000)


def _index(rows: list[dict]) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """name_by_code, code_by_name, confed_by_name a partir de las filas base."""
    name_by_code: dict[str, str] = {}
    code_by_name: dict[str, str] = {}
    confed_by_name: dict[str, str] = {}
    for m in rows:
        for side in ("home", "away"):
            t = m[side]
            n = t["name"]["en"]
            c = (t.get("fifaCode") or "").upper()
            cf = t.get("confederation") or ""
            if c:
                name_by_code[c] = n
                code_by_name[n] = c
            if cf:
                confed_by_name[n] = cf
    return name_by_code, code_by_name, confed_by_name


def _current_champion(rows: list[dict]) -> str | None:
    """Campeón vigente aplicando la regla de boxeo sobre las filas (ascendente)."""
    champ: str | None = None
    for m in rows:
        hn = m["home"]["name"]["en"]
        an = m["away"]["name"]["en"]
        hg = m["goals"]["home"]
        ag = m["goals"]["away"]
        pen = m.get("penalties")
        if hg > ag:
            champ = hn
        elif ag > hg:
            champ = an
        elif pen:
            champ = hn if pen["home"] > pen["away"] else an
        else:
            champ = champ if champ is not None else hn
    return champ


def _resolve_team_id(
    name: str,
    code: str,
    cache: dict[str, int],
    teams_cache: dict[str, list[dict]],
) -> int | None:
    """ID football-data del equipo, puenteando por tla == fifaCode."""
    if name in cache:
        return cache[name]
    for comp in COMPETITIONS:
        if comp not in teams_cache:
            try:
                data = api_get(f"/competitions/{comp}/teams")
            except Exception:
                teams_cache[comp] = []
                continue
            teams_cache[comp] = data.get("teams", [])
        teams = teams_cache[comp]
        if code:
            for t in teams:
                if (t.get("tla") or "").upper() == code:
                    cache[name] = int(t["id"])
                    return cache[name]
        target = _norm(name)
        for t in teams:
            for cand in (t.get("name"), t.get("shortName")):
                if cand and _norm(cand) == target:
                    cache[name] = int(t["id"])
                    return cache[name]
    return None


def _resolve_opponent(
    team: dict,
    name_by_code: dict[str, str],
    code_by_name: dict[str, str],
    confed_by_name: dict[str, str],
) -> tuple[str, str, str]:
    """(nombre canónico, fifaCode, confederación) del rival football-data."""
    tla = (team.get("tla") or "").upper()
    if tla and tla in name_by_code:
        name = name_by_code[tla]
        return name, tla, confed_by_name.get(name, "Other")

    raw = team.get("name") or ""
    norm = _norm(raw)
    for known in code_by_name:
        if _norm(known) == norm:
            return known, code_by_name.get(known, tla), confed_by_name.get(known, "Other")

    if norm in NAME_ALIASES:
        name = NAME_ALIASES[norm]
        return name, code_by_name.get(name, tla), confed_by_name.get(name, "Other")

    # Desconocido: usamos lo que da football-data. Si theufwc lo incorpora luego,
    # el rebuild lo corrige.
    return raw, tla, "Other"


def _build_row(
    *,
    match_no: int,
    iso_dt: str,
    home_ident: tuple[str, str, str],
    away_ident: tuple[str, str, str],
    home_goals: int,
    away_goals: int,
    penalties: dict | None,
) -> dict:
    def side(ident: tuple[str, str, str]) -> dict:
        name, code, confed = ident
        return {
            "name": {"en": name, "es": name},
            "fifaCode": code,
            "id": code,
            "confederation": confed,
        }

    row = {
        "matchNumber": match_no,
        "matchDate": _ms_at_date(iso_dt),
        "home": side(home_ident),
        "away": side(away_ident),
        "goals": {
            "home": home_goals,
            "away": away_goals,
            "details": {"home": [], "away": []},
        },
    }
    if penalties:
        row["penalties"] = penalties
    return row


def _winner_name(hn: str, an: str, hg: int, ag: int, penalties: dict | None, champ: str) -> str:
    if hg > ag:
        return hn
    if ag > hg:
        return an
    if penalties:
        return hn if penalties["home"] > penalties["away"] else an
    return champ


def _penalties_from_score(score: dict, winner_code: str | None, hg: int, ag: int) -> dict | None:
    if hg != ag or winner_code not in ("HOME_TEAM", "AWAY_TEAM"):
        return None
    p = score.get("penalties") or {}
    ph, pa = p.get("home"), p.get("away")
    if not isinstance(ph, int) or not isinstance(pa, int):
        ph, pa = (1, 0) if winner_code == "HOME_TEAM" else (0, 1)
    return {"home": ph, "away": pa}


def _compute_next(
    champion: str,
    code: str,
    cache: dict[str, int],
    teams_cache: dict[str, list[dict]],
    name_by_code: dict[str, str],
    code_by_name: dict[str, str],
    confed_by_name: dict[str, str],
) -> dict | None:
    team_id = _resolve_team_id(champion, code, cache, teams_cache)
    if team_id is None:
        return None
    try:
        m = fetch_next_scheduled_match(team_id)
    except Exception:
        return None
    if not m:
        return None
    home, away = m["homeTeam"], m["awayTeam"]
    if home.get("id") == team_id:
        opp = _resolve_opponent(away, name_by_code, code_by_name, confed_by_name)
        is_home = True
    elif away.get("id") == team_id:
        opp = _resolve_opponent(home, name_by_code, code_by_name, confed_by_name)
        is_home = False
    else:
        return None
    return {
        "champion": champion,
        "kickoff_utc": m["utcDate"],
        "opponent": opp[0],
        "opponent_code": opp[1],
        "is_home": is_home,
        "competition": (m.get("competition") or {}).get("name", ""),
        "venue": m.get("venue") or "",
    }


def extend(rows: list[dict]) -> tuple[list[dict], dict | None]:
    """
    Devuelve (rows_extendidas, live_next).

    - rows_extendidas: las filas de theufwc.com más, si procede, la cola de
      partidos de título recientes que aún no había publicado.
    - live_next: payload de next_match.json calculado desde football-data para el
      campeón vigente (o None si no se pudo / no hay clave).
    """
    if not API_KEY or api_get is None or not rows:
        return rows, None

    rows = sorted(rows, key=lambda m: m["matchNumber"])
    name_by_code, code_by_name, confed_by_name = _index(rows)
    cache: dict[str, int] = {}
    teams_cache: dict[str, list[dict]] = {}

    seen = {
        (_iso_from_ms(m["matchDate"]), m["home"]["name"]["en"], m["away"]["name"]["en"])
        for m in rows
    }
    last_no = rows[-1]["matchNumber"]
    last_date = _iso_from_ms(rows[-1]["matchDate"])
    champion = _current_champion(rows)
    appended = 0

    try:
        for _ in range(MAX_SWAPS + 1):
            if champion is None:
                break
            code = code_by_name.get(champion, "")
            team_id = _resolve_team_id(champion, code, cache, teams_cache)
            if team_id is None:
                break
            try:
                fd_matches = fetch_finished_matches(team_id, last_date)
            except Exception:
                break
            if not fd_matches:
                break

            changed = False
            for fm in fd_matches:
                home, away = fm.get("homeTeam") or {}, fm.get("awayTeam") or {}
                if home.get("id") == team_id:
                    champ_side = "home"
                elif away.get("id") == team_id:
                    champ_side = "away"
                else:
                    continue

                iso_dt = fm["utcDate"]
                d = _iso_from_dt(iso_dt)
                champ_ident = (champion, code, confed_by_name.get(champion, "Other"))
                opp_team = away if champ_side == "home" else home
                opp_ident = _resolve_opponent(
                    opp_team, name_by_code, code_by_name, confed_by_name
                )
                home_ident, away_ident = (
                    (champ_ident, opp_ident)
                    if champ_side == "home"
                    else (opp_ident, champ_ident)
                )
                hn, an = home_ident[0], away_ident[0]
                if (d, hn, an) in seen:
                    continue

                score = fm.get("score") or {}
                ft = score.get("fullTime") or {}
                hg = ft.get("home") or 0
                ag = ft.get("away") or 0
                winner_code = score.get("winner")
                penalties = _penalties_from_score(score, winner_code, hg, ag)

                last_no += 1
                rows.append(
                    _build_row(
                        match_no=last_no,
                        iso_dt=iso_dt,
                        home_ident=home_ident,
                        away_ident=away_ident,
                        home_goals=hg,
                        away_goals=ag,
                        penalties=penalties,
                    )
                )
                seen.add((d, hn, an))
                appended += 1
                last_date = d

                new_champ = _winner_name(hn, an, hg, ag, penalties, champion)
                if new_champ != champion:
                    champion = new_champ
                    changed = True
                    break

            if not changed:
                break
    except Exception as exc:  # nunca romper el scrape por la extensión
        print(f"WARN: extensión en vivo abortada: {exc}")

    if appended:
        print(f"Extensión en vivo: +{appended} partido(s), campeón ahora {champion}.")

    live_next = _compute_next(
        champion or "",
        code_by_name.get(champion or "", ""),
        cache,
        teams_cache,
        name_by_code,
        code_by_name,
        confed_by_name,
    )
    return rows, live_next

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
import re
import urllib.request
from datetime import date, datetime, timedelta, timezone

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


def _pair_key(a: str, b: str) -> frozenset:
    return frozenset({a, b})


def _is_dup(seen_pairs: set, d: str, a: str, b: str) -> bool:
    """True si ya existe ese enfrentamiento (mismo par) dentro de ±1 día.

    Fuentes distintas (theufwc/Wikipedia vs football-data) datan el mismo partido
    con un día de diferencia por el huso horario; el par + tolerancia evita
    duplicarlo.
    """
    pair = _pair_key(a, b)
    try:
        base = date.fromisoformat(d)
    except Exception:
        return (pair, d) in seen_pairs
    for delta in (-1, 0, 1):
        if (pair, (base + timedelta(days=delta)).isoformat()) in seen_pairs:
            return True
    return False


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


# ---------------------- Wikipedia (fallback sin auth) ----------------------
#
# theufwc.com cerró su API (401) y no hay registro posible. El infobox de la
# página UFWC de Wikipedia lo mantiene la comunidad al día, es gratis y sin auth,
# usa códigos FIFA ({{fb|TUR}}) y cubre amistosos / Nations League que el free
# tier de football-data no trae. Lo usamos para reconciliar el campeón vigente
# cuando football-data no capta un cambio de título.

WIKI_URL = (
    "https://en.wikipedia.org/w/index.php"
    "?title=Unofficial_Football_World_Championships&action=raw&section=0"
)
WIKI_UA = "ufcc-bot/1.0 (+https://github.com/whiteriggs/UFCC)"

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}


def _wiki_section(text: str, start: str, ends: list[str]) -> str:
    i = text.find(start)
    if i < 0:
        return ""
    i += len(start)
    j = len(text)
    for e in ends:
        k = text.find(e, i)
        if 0 <= k < j:
            j = k
    return text[i:j]


def _wiki_date(block: str) -> str | None:
    clean = re.sub(r"<[^>]+>", " ", block)
    m = re.search(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", clean)
    if not m:
        return None
    mon = _MONTHS.get(m.group(2).lower())
    if not mon:
        return None
    return f"{int(m.group(3)):04d}-{mon:02d}-{int(m.group(1)):02d}"


def _wiki_link(block: str) -> str:
    m = re.search(r"\[\[(?:[^\]|]*\|)?([^\]]+)\]\]", block)
    return m.group(1).strip() if m else ""


def _wiki_venue(block: str) -> str:
    tail = re.split(r"<br\s*/?>", block)[-1]
    tail = tail.split("<!--")[0]
    tail = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]+)\]\]", r"\1", tail)
    tail = re.sub(r"\{\{nowrap\|", "", tail)
    tail = re.sub(r"<[^>]+>", "", tail)
    return tail.replace("}}", "").strip(" |\n")


def _wiki_event(block: str, with_score: bool) -> dict | None:
    if not block:
        return None
    mo = re.search(r"\{\{fb\|([A-Za-z]{3})\}\}", block)
    ev: dict = {
        "date": _wiki_date(block),
        "opponent_code": mo.group(1).upper() if mo else None,
        "competition": _wiki_link(block),
        "venue": _wiki_venue(block),
    }
    if with_score:
        ms = re.search(r"(\d+)\s*[–-]\s*(\d+)\s*(?:\([^)]*\)\s*)?vs", block)
        if ms:
            ev["champ_goals"] = int(ms.group(1))
            ev["opp_goals"] = int(ms.group(2))
        mp = re.search(r"\(\s*(\d+)\s*[–-]\s*(\d+)\s*pen", block, re.I)
        if mp:
            ev["penalties"] = {"home": int(mp.group(1)), "away": int(mp.group(2))}
    return ev


def _wiki_infobox() -> dict | None:
    """Lee y parsea el infobox: campeón actual, último cambio y próxima defensa."""
    try:
        req = urllib.request.Request(WIKI_URL, headers={"User-Agent": WIKI_UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            text = r.read().decode("utf-8", errors="replace")
    except Exception:
        return None
    champ_block = _wiki_section(text, "Current Champions", ["Title gained"])
    gained_block = _wiki_section(text, "Title gained", ["Title defences", "Next defence"])
    next_block = _wiki_section(text, "Next defence", ["|}"])
    mc = re.search(r"\{\{fb\|([A-Za-z]{3})\}\}", champ_block)
    champion_code = mc.group(1).upper() if mc else None
    if not champion_code:
        return None
    return {
        "champion_code": champion_code,
        "gained": _wiki_event(gained_block, with_score=True),
        "next": _wiki_event(next_block, with_score=False),
    }


def _wiki_reconcile(
    wiki: dict,
    rows: list[dict],
    seen_pairs: set,
    champion: str | None,
    last_no: int,
    last_date: str | None,
    name_by_code: dict[str, str],
    code_by_name: dict[str, str],
    confed_by_name: dict[str, str],
) -> tuple[str | None, int, str | None, int]:
    """Si Wikipedia muestra un campeón más nuevo que el nuestro, añade ese partido."""
    code = wiki.get("champion_code")
    if not code:
        return champion, last_no, last_date, 0
    wiki_champ = name_by_code.get(code, code)
    if wiki_champ == champion:
        return champion, last_no, last_date, 0
    g = wiki.get("gained")
    if not g or not g.get("date"):
        return champion, last_no, last_date, 0
    d = g["date"]
    if last_date and d <= last_date:
        # No es más reciente que lo que ya tenemos: no tocamos el linaje.
        return champion, last_no, last_date, 0
    opp_code = g.get("opponent_code") or ""
    opp_name = name_by_code.get(opp_code, opp_code or "Unknown")
    if _is_dup(seen_pairs, d, wiki_champ, opp_name):
        return champion, last_no, last_date, 0
    cg = g.get("champ_goals", 1)
    og = g.get("opp_goals", 0)
    penalties = g.get("penalties")
    if cg == og and not penalties:
        penalties = {"home": 1, "away": 0}
    last_no += 1
    rows.append(
        _build_row(
            match_no=last_no,
            iso_dt=d + "T00:00:00Z",
            home_ident=(wiki_champ, code, confed_by_name.get(wiki_champ, "Other")),
            away_ident=(opp_name, opp_code, confed_by_name.get(opp_name, "Other")),
            home_goals=cg,
            away_goals=og,
            penalties=penalties,
        )
    )
    seen_pairs.add((_pair_key(wiki_champ, opp_name), d))
    print(f"Reconciliación Wikipedia: +1 partido, campeón ahora {wiki_champ} (era {champion}).")
    return wiki_champ, last_no, d, 1


def _wiki_next(wiki: dict, champion: str | None, name_by_code: dict[str, str]) -> dict | None:
    n = wiki.get("next")
    if not n or not n.get("date") or not n.get("opponent_code"):
        return None
    opp_name = name_by_code.get(n["opponent_code"], n["opponent_code"])
    venue = n.get("venue", "")
    is_home = bool(champion and venue and venue.strip().endswith(champion))
    return {
        "champion": champion,
        "kickoff_utc": n["date"] + "T00:00:00Z",
        "opponent": opp_name,
        "opponent_code": n["opponent_code"],
        "is_home": is_home,
        "competition": n.get("competition", ""),
        "venue": venue,
    }


def extend(rows: list[dict]) -> tuple[list[dict], dict | None]:
    """
    Devuelve (rows_extendidas, live_next).

    - rows_extendidas: las filas de theufwc.com más, si procede, la cola de
      partidos de título recientes que aún no había publicado (vía football-data
      durante torneos, y vía Wikipedia para amistosos / Nations League).
    - live_next: payload de next_match.json (football-data si lo tiene; si no, la
      próxima defensa que anuncia el infobox de Wikipedia).

    Wikipedia funciona aunque no haya FOOTBALL_DATA_KEY: es el respaldo cuando
    theufwc.com no está disponible.
    """
    if not rows:
        return rows, None

    rows = sorted(rows, key=lambda m: m["matchNumber"])
    name_by_code, code_by_name, confed_by_name = _index(rows)
    cache: dict[str, int] = {}
    teams_cache: dict[str, list[dict]] = {}

    seen_pairs = {
        (_pair_key(m["home"]["name"]["en"], m["away"]["name"]["en"]), _iso_from_ms(m["matchDate"]))
        for m in rows
    }
    last_no = rows[-1]["matchNumber"]
    last_date = _iso_from_ms(rows[-1]["matchDate"])
    champion = _current_champion(rows)
    appended = 0
    fd_available = bool(API_KEY and api_get is not None)

    if fd_available:
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
                if _is_dup(seen_pairs, d, hn, an):
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
                seen_pairs.add((_pair_key(hn, an), d))
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
        print(f"WARN: extensión football-data abortada: {exc}")

    if appended:
        print(f"Extensión en vivo: +{appended} partido(s), campeón ahora {champion}.")

    # Respaldo Wikipedia (sin auth): reconcilia el campeón si football-data no
    # captó el cambio (amistosos / Nations League) y aporta la próxima defensa.
    wiki_next = None
    wiki = _wiki_infobox()
    if wiki:
        champion, last_no, last_date, w_added = _wiki_reconcile(
            wiki, rows, seen_pairs, champion, last_no, last_date,
            name_by_code, code_by_name, confed_by_name,
        )
        appended += w_added
        wiki_next = _wiki_next(wiki, champion, name_by_code)

    fd_next = None
    if fd_available:
        fd_next = _compute_next(
            champion or "",
            code_by_name.get(champion or "", ""),
            cache,
            teams_cache,
            name_by_code,
            code_by_name,
            confed_by_name,
        )
    live_next = fd_next or wiki_next
    return rows, live_next

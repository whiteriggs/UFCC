"""
UFWC — Unofficial Football World Championships (national teams).

Pulls the full official lineage from theufwc.com's JSON API and writes a dataset
with the SAME shape as the clubs one, so the static site can consume it in
"nations mode":

  docs/data-ufwc/matches.json          partidos DESC por nº (compactos)
  docs/data-ufwc/next_match.json       próxima defensa del título (si la hay)
  docs/data-ufwc/clubs.json            {seleccion: emoji_bandera}  (hace de "escudo")
  docs/data-ufwc/years.json            índice por año (timeline)
  docs/data-ufwc/rankings.json         ranking acumulado por selección
  docs/data-ufwc/longest_reigns.json   reinados individuales más largos
  docs/data-ufwc/countries.json        agregado por confederación
  docs/data-ufwc/champions_geo.json    geo (centroides) para el mapa
  docs/data-ufwc/stats.json            stats globales

Endpoints (API Next.js de theufwc.com, mantenida al día por la propia web):
  /api/matches?order=asc&pageSize=9999&pageNumber=1   todos los partidos título
  /api/matches/next                                   próximo partido título

La web decide qué partido defiende el título, así que no calculamos fixtures:
leemos sus datos y reconstruimos el dataset. Reglas de linaje (estilo boxeo):
quien gana al campeón le quita el título; empate -> retiene; tanda de penaltis
(goles empatados + objeto `penalties`) -> se lo lleva quien gana la tanda. El
primer partido siembra el campeón con el local si fue empate. Pensado para
ejecutarse en una GitHub Action periódica que commitea si hay cambios.
"""

from __future__ import annotations

import json
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

API_MATCHES = "https://www.theufwc.com/api/matches?order=asc&pageSize=9999&pageNumber=1"
API_NEXT = "https://www.theufwc.com/api/matches/next"
CACHE = Path("cache_html") / "theufwc_matches.json"
# Snapshot base COMMITEADO (no en .gitignore). Es la red de seguridad cuando la
# API de theufwc.com no está disponible (p.ej. devolvió 401 al cerrar su API):
# el linaje histórico no cambia, así que basta con esta base + la extensión en
# vivo (ufwc_live) para seguir actualizando el campeón vía football-data.org.
BASE = Path("data") / "ufwc_base.json"
DATA = Path("docs") / "data-ufwc"

# Periodos de suspensión por las guerras mundiales (igual que el dataset clubs):
# los días dentro de estos rangos no cuentan como "defensa" del título.
WARTIME_RANGES = [
    (date(1914, 8, 1), date(1919, 8, 31)),
    (date(1939, 9, 1), date(1946, 8, 31)),
]

# FIFA code -> ISO-3166 alpha-2 (o proxy para selecciones históricas), para
# generar el emoji de bandera que hace de "escudo".
_ISO = {
    "HUN": "HU", "AUT": "AT", "BEL": "BE", "LUX": "LU", "FRA": "FR", "NOR": "NO",
    "GER": "DE", "NED": "NL", "SUI": "CH", "ITA": "IT", "TCH": "CZ", "SWE": "SE",
    "YUG": "RS", "ROU": "RO", "FIN": "FI", "CRO": "HR", "BUL": "BG", "DEN": "DK",
    "POL": "PL", "POR": "PT", "CHI": "CL", "USA": "US", "PAN": "PA", "MEX": "MX",
    "PER": "PE", "URU": "UY", "BRA": "BR", "BOL": "BO", "ECU": "EC", "PAR": "PY",
    "ARG": "AR", "CRC": "CR", "COL": "CO", "FRG": "DE", "ESP": "ES", "MAR": "MA",
    "ANT": "NL", "SLV": "SV", "HON": "HN", "URS": "RU", "PRK": "KP", "GRE": "GR",
    "CYP": "CY", "DDR": "DE", "ISR": "IL", "MLT": "MT", "ISL": "IS", "IRL": "IE",
    "TUR": "TR", "IRN": "IR", "TUN": "TN", "ALG": "DZ", "CMR": "CM", "ALB": "AL",
    "EGY": "EG", "AUS": "AU", "CIV": "CI", "KSA": "SA", "VEN": "VE", "KOR": "KR",
    "NGA": "NG", "SVN": "SI", "RUS": "RU", "SMR": "SM", "FRO": "FO", "QAT": "QA",
    "UAE": "AE", "CZE": "CZ", "ARM": "AM", "UKR": "UA", "RSA": "ZA", "OMA": "OM",
    "BIH": "BA", "JPN": "JP", "JAM": "JM", "AND": "AD", "BLR": "BY", "GEO": "GE",
    "LIE": "LI", "MDA": "MD", "CAN": "CA", "RWA": "RW", "ANG": "AO", "GAB": "GA",
    "BOT": "BW", "MOZ": "MZ", "ZIM": "ZW", "ZAM": "ZM", "COD": "CD", "SCG": "RS",
    "LBY": "LY", "LTU": "LT", "MNE": "ME", "MKD": "MK", "GHA": "GH", "SVK": "SK",
    "JOR": "JO", "SYR": "SY", "UZB": "UZ", "VIE": "VN", "TJK": "TJ", "KUW": "KW",
    "PHI": "PH", "IND": "IN", "PLE": "PS", "TKM": "TM", "IDN": "ID", "TPE": "TW",
    "GUM": "GU", "HKG": "HK", "GUA": "GT", "TRI": "TT", "NCA": "NI", "NZL": "NZ",
    "EST": "EE", "KAZ": "KZ", "CUW": "CW", "KEN": "KE", "CHA": "TD", "SLE": "SL",
    "LBR": "LR", "TOG": "TG", "GAM": "GM", "EIR": "IE", "NIR": "GB", "KVX": "XK",
}
# Naciones del Reino Unido: el indicador regional no las representa; usan la
# secuencia de etiquetas de subdivisión (bandera negra + tag).
_SUBDIV = {"ENG": "gbeng", "SCO": "gbsct", "WAL": "gbwls"}


def _flag(code: str) -> str:
    if code in _SUBDIV:
        tag = _SUBDIV[code]
        return "\U0001F3F4" + "".join(chr(0xE0000 + ord(c)) for c in tag) + "\U000E007F"
    iso = _ISO.get(code)
    if not iso:
        return ""
    return "".join(chr(0x1F1E6 + ord(c) - 65) for c in iso)


FLAG = {code: _flag(code) for code in list(_ISO) + list(_SUBDIV)}

# Centroides aproximados (lat, lon) por código FIFA de las selecciones campeonas.
CENTROID = {
    "SCO": (56.49, -4.20), "ENG": (52.36, -1.17), "EIR": (53.20, -7.50),
    "WAL": (52.13, -3.78), "NIR": (54.79, -6.49), "AUT": (47.52, 14.55),
    "YUG": (44.0, 20.5), "ITA": (41.87, 12.57), "SUI": (46.82, 8.23),
    "HUN": (47.16, 19.50), "GER": (51.17, 10.45), "SWE": (60.13, 18.64),
    "USA": (39.0, -98.0), "CHI": (-35.68, -71.54), "BRA": (-14.24, -51.93),
    "PER": (-9.19, -75.02), "URU": (-32.52, -55.77), "PAR": (-23.44, -58.44),
    "ARG": (-38.42, -63.62), "BOL": (-16.29, -63.59), "FRG": (50.5, 9.5),
    "ESP": (40.46, -3.75), "TCH": (49.8, 16.5), "MEX": (23.63, -102.55),
    "ANT": (12.20, -69.0), "CRC": (9.75, -83.75), "COL": (4.57, -74.30),
    "ECU": (-1.83, -78.18), "URS": (56.0, 50.0), "FRA": (46.23, 2.21),
    "BUL": (42.73, 25.49), "NED": (52.13, 5.29), "IRL": (53.41, -8.24),
    "POL": (51.92, 19.15), "POR": (39.40, -8.22), "BEL": (50.50, 4.47),
    "DEN": (56.26, 9.50), "ROU": (45.94, 24.97), "GRE": (39.07, 21.82),
    "AUS": (-25.27, 133.78), "KOR": (36.5, 127.85), "RUS": (55.75, 37.62),
    "CZE": (49.82, 15.47), "ISR": (31.05, 34.85), "NGA": (9.08, 8.68),
    "ANG": (-11.20, 17.87), "ZIM": (-19.02, 29.15), "VEN": (6.42, -66.59),
    "GEO": (42.32, 43.36), "TUR": (38.96, 35.24), "JPN": (36.20, 138.25),
    "PRK": (40.34, 127.51), "CRO": (45.10, 15.20), "CIV": (7.54, -5.55),
    "SLE": (8.46, -11.78), "LBR": (6.43, -9.43), "ALG": (28.03, 1.66),
    "KVX": (42.60, 20.90),
}


def fetch_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def load_matches() -> list[dict]:
    """Todos los partidos título (asc). API en vivo con fallback a base/caché."""
    try:
        data = fetch_json(API_MATCHES)
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(data), encoding="utf-8")
        # theufwc.com está vivo: refrescamos el snapshot base committeado para
        # que la red de seguridad se mantenga al día (el workflow lo commitea).
        try:
            BASE.parent.mkdir(parents=True, exist_ok=True)
            BASE.write_text(
                json.dumps({"matches": data["matches"]}, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
        except Exception:
            pass
    except Exception as exc:  # API caída/401 -> base committeado o última caché
        if BASE.exists():
            data = json.loads(BASE.read_text(encoding="utf-8"))
        elif CACHE.exists():
            data = json.loads(CACHE.read_text(encoding="utf-8"))
        else:
            raise SystemExit(f"No se puede acceder a la API y no hay base/caché: {exc}")
    return sorted(data["matches"], key=lambda m: m["matchNumber"])


def load_next() -> dict | None:
    try:
        return fetch_json(API_NEXT)
    except Exception:
        return None


def iso_date(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date().isoformat()


def days_between(a: str, b: str) -> int:
    if not a or not b:
        return 1
    return max(1, (date.fromisoformat(b) - date.fromisoformat(a)).days)


def wartime_days(a: str, b: str) -> int:
    if not a or not b:
        return 0
    da, db_ = date.fromisoformat(a), date.fromisoformat(b)
    if db_ <= da:
        return 0
    total = 0
    for ws, we in WARTIME_RANGES:
        lo, hi = max(da, ws), min(db_, we)
        if hi > lo:
            total += (hi - lo).days
    return total


def adjusted_days(a: str, b: str) -> int:
    return max(1, days_between(a, b) - wartime_days(a, b))


def main() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    rows = load_matches()  # ascendente por nº de partido

    # Extensión "en vivo": si theufwc.com va por detrás (p.ej. durante un
    # torneo), seguimos al campeón vigente con football-data.org y añadimos los
    # partidos de título que falten. Sin FOOTBALL_DATA_KEY no hace nada.
    live_next: dict | None = None
    try:
        from ufwc_live import extend as _live_extend
        rows, live_next = _live_extend(rows)
    except Exception as exc:
        print(f"WARN: extensión en vivo desactivada: {exc}")

    flags: dict[str, str] = {}      # nombre selección -> emoji bandera ("escudo")
    confed: dict[str, str] = {}     # nombre selección -> confederación
    code_of: dict[str, str] = {}    # nombre selección -> código FIFA (centroides)
    matches_asc: list[list] = []
    champ: str | None = None

    for m in rows:
        no = m["matchNumber"]
        date_iso = iso_date(m["matchDate"])
        home, away = m["home"], m["away"]
        hn, an = home["name"]["en"], away["name"]["en"]
        hc, ac = home["fifaCode"], away["fifaCode"]

        for name, fc, cf in ((hn, hc, home["confederation"]), (an, ac, away["confederation"])):
            flags.setdefault(name, FLAG.get(fc, ""))
            confed[name] = cf
            code_of[name] = fc

        hg, ag = m["goals"]["home"], m["goals"]["away"]
        pen = m.get("penalties")
        if hg > ag:
            winner = hn
        elif ag > hg:
            winner = an
        elif pen:
            winner = hn if pen["home"] > pen["away"] else an
        else:
            winner = None

        champ = winner or (champ if champ is not None else hn)

        result = "H" if winner == hn else ("A" if winner == an else "D")
        score = f"{hg}-{ag} ({pen['home']}-{pen['away']}p)" if pen else f"{hg}-{ag}"

        matches_asc.append([no, date_iso, hn, score, an, result, "", "", champ])

    matches = matches_asc[::-1]  # DESC para el feed
    write = lambda name, obj: (DATA / name).write_text(
        json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )

    # next_match.json: próxima defensa del título (si hay partido programado).
    # Prioridad a la extensión en vivo (football-data); si no la hay, theufwc.com.
    current = matches[0][8] if matches else None
    nxt_path = DATA / "next_match.json"
    wrote_next = False
    if live_next and current and live_next.get("opponent"):
        opp = live_next["opponent"]
        flags.setdefault(opp, FLAG.get(live_next.get("opponent_code", ""), ""))
        write("next_match.json", {
            "champion": live_next.get("champion", current),
            "kickoff_utc": live_next["kickoff_utc"],
            "opponent": opp,
            "is_home": live_next.get("is_home", False),
            "competition": live_next.get("competition", ""),
            "venue": live_next.get("venue", ""),
        })
        wrote_next = True
    nxt = None if wrote_next else load_next()
    if isinstance(nxt, dict) and nxt.get("home") and current:
        nh, na = nxt["home"], nxt["away"]
        nhn, nan = nh["name"]["en"], na["name"]["en"]
        flags.setdefault(nhn, FLAG.get(nh["fifaCode"], ""))
        flags.setdefault(nan, FLAG.get(na["fifaCode"], ""))
        opponent = nan if nhn == current else (nhn if nan == current else None)
        ms_k = nxt.get("dateTime") or nxt.get("matchDate")
        if opponent and ms_k:
            kickoff = datetime.fromtimestamp(ms_k / 1000, tz=timezone.utc)
            write("next_match.json", {
                "champion": current,
                "kickoff_utc": kickoff.isoformat().replace("+00:00", "Z"),
                "opponent": opponent,
                "is_home": nhn == current,
                "competition": "",
                "venue": "",
            })
            wrote_next = True
    if not wrote_next and nxt_path.exists():
        nxt_path.unlink()

    # clubs.json: selección -> emoji bandera (hace de "escudo").
    write("clubs.json", flags)

    # matches.json
    write("matches.json", matches)

    # years.json (timeline) sobre el orden DESC.
    years: list[dict] = []
    cy = cs = cc = None
    for i, m in enumerate(matches):
        y = int(m[1][:4])
        if cy is None:
            cy, cs, cc = y, i, 0
        if y != cy:
            years.append({"year": cy, "first_index": cs, "count": cc})
            cy, cs, cc = y, i, 0
        cc += 1
    if cy is not None:
        years.append({"year": cy, "first_index": cs, "count": cc})
    write("years.json", years)

    # Reinados (runs consecutivos del mismo campeón) sobre orden ascendente.
    reigns: list[dict] = []
    today = date.today().isoformat()
    for m in matches_asc:
        champ_after = m[8]
        date_iso = m[1]
        if reigns and reigns[-1]["club"] == champ_after:
            reigns[-1]["matches_held"] += 1
            reigns[-1]["ended_on"] = date_iso
        else:
            reigns.append({
                "club": champ_after, "matches_held": 1,
                "started_on": date_iso, "ended_on": date_iso,
            })
    last_idx = len(reigns) - 1

    per_days: dict[str, int] = {}
    per_matches: dict[str, int] = {}
    per_reigns: dict[str, int] = {}
    single_reigns: list[dict] = []
    for idx, rg in enumerate(reigns):
        club, held, start = rg["club"], rg["matches_held"], rg["started_on"]
        ref_end = today if idx == last_idx else rg["ended_on"]
        d = adjusted_days(start, ref_end)
        per_days[club] = per_days.get(club, 0) + d
        per_matches[club] = per_matches.get(club, 0) + held
        per_reigns[club] = per_reigns.get(club, 0) + 1
        single_reigns.append({
            "club": club, "started_on": start, "ended_on": ref_end,
            "days": d, "days_raw": days_between(start, ref_end),
            "wartime_days": wartime_days(start, ref_end),
            "matches": held, "is_current": idx == last_idx,
            "crest": flags.get(club),
        })

    rankings = [
        {"name": c, "days": per_days[c], "matches": per_matches[c],
         "reigns": per_reigns[c], "crest": flags.get(c)}
        for c in sorted(per_matches, key=lambda c: (-per_matches[c], -per_days[c]))
    ]
    write("rankings.json", rankings)

    single_reigns.sort(key=lambda r: (-r["matches"], -r["days"]))
    write("longest_reigns.json", single_reigns[:100])

    # countries.json -> agregado por confederación.
    cf_days: dict[str, int] = {}
    cf_matches: dict[str, int] = {}
    cf_clubs: dict[str, dict[str, int]] = {}
    for club, days in per_days.items():
        cf = confed.get(club, "Other")
        cf_days[cf] = cf_days.get(cf, 0) + days
        cf_matches[cf] = cf_matches.get(cf, 0) + per_matches[club]
        cf_clubs.setdefault(cf, {})[club] = days
    countries = []
    for cf in sorted(cf_days, key=lambda c: -cf_days[c]):
        top = sorted(cf_clubs[cf].items(), key=lambda kv: -kv[1])[:10]
        countries.append({
            "country": cf, "days": cf_days[cf], "matches": cf_matches[cf],
            "clubs_total": len(cf_clubs[cf]),
            "top_clubs": [{"name": n, "days": d, "crest": flags.get(n)} for n, d in top],
        })
    write("countries.json", countries)

    # champions_geo.json -> centroides.
    geo = []
    for club, days in per_days.items():
        c = CENTROID.get(code_of.get(club, ""))
        if not c:
            continue
        geo.append({
            "name": club, "lat": c[0], "lon": c[1], "days": days,
            "matches": per_matches[club], "reigns": per_reigns[club],
            "crest": flags.get(club),
        })
    geo.sort(key=lambda x: -x["days"])
    write("champions_geo.json", geo)

    # stats.json
    longest = single_reigns[0] if single_reigns else None
    shortest = min(single_reigns, key=lambda r: (r["matches"], r["days"])) if single_reigns else None
    changes_per_year: dict[int, int] = {}
    for i in range(1, len(reigns)):
        y = int(reigns[i]["started_on"][:4])
        changes_per_year[y] = changes_per_year.get(y, 0) + 1
    most_year = max(changes_per_year.items(), key=lambda kv: kv[1]) if changes_per_year else None
    stats = {
        "total_matches": len(matches),
        "total_clubs": len({m[2] for m in matches} | {m[4] for m in matches}),
        "total_champions": len(per_days),
        "total_reigns": len(reigns),
        "first_date": matches[-1][1] if matches else None,
        "last_date": matches[0][1] if matches else None,
        "longest_reign": {
            "club": longest["club"], "days": longest["days"], "matches": longest["matches"],
            "started_on": longest["started_on"], "ended_on": longest["ended_on"],
        } if longest else None,
        "shortest_reign": {
            "club": shortest["club"], "days": shortest["days"], "matches": shortest["matches"],
            "started_on": shortest["started_on"], "ended_on": shortest["ended_on"],
        } if shortest else None,
        "most_changes_year": {"year": most_year[0], "changes": most_year[1]} if most_year else None,
        "current_champion": matches[0][8] if matches else None,
    }
    write("stats.json", stats)

    print(f"OK — {len(matches)} matches, {len(per_days)} champions, "
          f"current: {stats['current_champion']}")


if __name__ == "__main__":
    main()

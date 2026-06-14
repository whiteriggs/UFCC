# UFCC — Unofficial Football Club Championship

A single imaginary title that passes to whichever club beats the current
holder, every single match, since **11 November 1871** (Upton Park 0–3
Clapham Rovers — Clapham Rovers became the first champion).

Live: <https://ufcc-stats.github.io/UFCC/>

## What you can do on the site

- **Feed** — every title match in reverse chronological order, with a draggable
  year timeline. The hero on top shows the current champion, how long they've
  held it, and their next scheduled match.
- **Rankings** — clubs sorted by matches defended.
- **Longest reigns** — top 100 individual reigns.
- **Countries** — clubs grouped by country.
- **Map** — every champion on a world map, sized by total days held.
- **Stats** — totals, current champion, longest/shortest reign, busiest year.
- **Search** — across every match in history.

## Where the data comes from

The historical lineage was scraped once from
[stevesfootballstats.uk](https://www.stevesfootballstats.uk/unofficial_football_club_championship_ufcc.html).
Daily updates use the [football-data.org](https://www.football-data.org/)
free API to detect new matches of the current champion, insert them, and
swap the title if the holder loses.

A GitHub Actions workflow keeps everything in sync automatically. On match
days the new result appears on the site within minutes of the final whistle;
the rest of the time the API stays untouched.

## A couple of design choices

- **Matches, not days, is the primary ranking metric.** Days on the calendar
  are misleading — a club crowned in May and beaten in the first August
  matchday looks dominant but barely defended the title. Days are still shown
  as secondary info.
- **World War years are excluded from day counts** (1914–1919 and 1939–1946),
  when European league football was suspended.

## Two modes: clubs (UFCC) and nations (UFWC)

The top bar has a **Clubs / Nations** toggle. "Nations" switches the whole
site to the **UFWC — Unofficial Football World Championships**: the same
boxing-style title, but contested by national teams since the first ever
international (Scotland 0–0 England, 1872). It uses a distinct emerald theme,
flag emoji instead of crests, and groups the "Countries" view by confederation.

The nations dataset is rebuilt from [theufwc.com](https://www.theufwc.com)'s API
on every run of `scrape_ufwc.py`. When that site lags behind (typically during a
tournament), `ufwc_live.py` follows the current champion via
[football-data.org](https://www.football-data.org/) and appends the missing
title matches, applying the same boxing-style rule; once theufwc.com catches up,
the full rebuild absorbs them with no duplicates. A GitHub Action keeps it in
sync automatically.

## Repo

```
docs/                 GitHub Pages root (static site)
docs/data/            clubs (UFCC) JSON
docs/data-ufwc/       nations (UFWC) JSON
ufcc.db               SQLite database (clubs)
update_from_api.py    incremental updater (football-data.org)
export_web.py         SQLite → JSON exporter (clubs)
scrape_ufwc.py        UFWC nations scraper (theufwc.com) → docs/data-ufwc/
ufwc_live.py          live tail: follows the champion via football-data.org
scrape_*.py           one-shot historical scrapers (clubs)
.github/workflows/    automation
```

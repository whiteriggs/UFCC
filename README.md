# UFCC — Unofficial Football Club Championship

Scraper + static web for the lineage of the *Unofficial Football Club
Championship*, with data sourced from
[stevesfootballstats.uk](https://www.stevesfootballstats.uk/unofficial_football_club_championship_ufcc.html).

## Data pipeline

```sh
python3 scrape_champions.py    # 456 historical champions from KML (name, lat, lon)
python3 scrape_lineage.py      # 5437 matches from 1871 to today, with derived champion-after each match
python3 fetch_crests.py        # 1012 club crests scraped from the source HTML
python3 export_web.py          # dump everything to docs/data/*.json + docs/crests/
```

Output lives in `ufcc.db` (SQLite) and `docs/` (static site).

## Web

Static, vanilla HTML/CSS/JS. Reverse-chronological infinite-scroll feed of every
title match with a draggable year timeline on the right. Deployed via GitHub
Pages from `/docs`.

Live: <https://jcaboroca.github.io/UFCC/>

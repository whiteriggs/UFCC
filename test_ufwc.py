import json
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

import scrape_ufwc
import ufwc_live


def row(number, day, home, away, goals):
    names = {"ITA": "Italy", "FRA": "France", "TUR": "Turkey", "BEL": "Belgium"}
    return {
        "matchNumber": number,
        "matchDate": int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp() * 1000),
        "home": {"name": {"en": names[home]}, "fifaCode": home, "confederation": "UEFA"},
        "away": {"name": {"en": names[away]}, "fifaCode": away, "confederation": "UEFA"},
        "goals": {"home": goals[0], "away": goals[1]},
    }


def event(day, opponent, goals, venue):
    return {
        "date": day, "opponent_code": opponent,
        "champ_goals": goals[0], "opp_goals": goals[1],
        "venue": venue, "competition": "UEFA Nations League",
    }


FRANCE = {
    "champion_code": "FRA",
    "gained": event("2026-09-25", "TUR", (1, 0), "Turkey"),
    "defences": [
        event("2026-09-28", "BEL", (1, 0), "Belgium"),
        event("2026-10-02", "ITA", (1, 1), "France"),
        event("2026-10-05", "BEL", (4, 1), "France"),
    ],
    "next": None,
}
ITALY = {
    "champion_code": "ITA",
    "gained": event("2026-11-12", "FRA", (3, 1), "Italy"),
    "defences": [],
    "next": None,
}


class NationsHistoryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.base = root / "base.json"
        self.data = root / "output"
        self.initial = [
            row(1, "2026-06-01", "ITA", "FRA", (0, 1)),
            row(2, "2026-06-25", "TUR", "FRA", (2, 1)),
        ]
        self.saved = self.initial + [
            row(3, "2026-09-25", "TUR", "FRA", (0, 1)),
            row(4, "2026-09-28", "BEL", "FRA", (0, 1)),
            row(5, "2026-10-02", "FRA", "ITA", (1, 1)),
            row(6, "2026-10-05", "FRA", "BEL", (4, 1)),
        ]
        self.save(self.initial)
        for target, value in [
            ("scrape_ufwc.BASE", self.base),
            ("scrape_ufwc.CACHE", root / "cache.json"),
            ("scrape_ufwc.DATA", self.data),
            ("ufwc_live.API_KEY", ""),
        ]:
            mock = patch(target, value)
            mock.start()
            self.addCleanup(mock.stop)

    def save(self, rows):
        self.base.write_text(json.dumps({"matches": rows}), encoding="utf-8")

    def test_champion_change_keeps_previous_reign_across_rebuilds(self):
        with patch("scrape_ufwc.fetch_json", side_effect=URLError("offline")):
            with patch("ufwc_live._wiki_infobox", return_value=FRANCE):
                scrape_ufwc.main()
            with patch("ufwc_live._wiki_infobox", return_value=ITALY):
                scrape_ufwc.main()
                scrape_ufwc.main()
        matches = json.loads((self.data / "matches.json").read_text())
        self.assertEqual([m[1] for m in matches], [
            "2026-11-12", "2026-10-05", "2026-10-02", "2026-09-28",
            "2026-09-25", "2026-06-25", "2026-06-01",
        ])
        self.assertEqual(matches[0][2:6], ["Italy", "3-1", "France", "H"])
        self.assertEqual(matches[0][8], "Italy")
        self.assertEqual(len(json.loads(self.base.read_text())["matches"]), 7)

    def test_stale_api_keeps_saved_tail_and_accepts_historical_corrections(self):
        self.save(self.saved)
        corrected = deepcopy(self.initial)
        corrected[0]["goals"]["away"] = 2
        with patch("scrape_ufwc.fetch_json", return_value={"matches": corrected}):
            result = scrape_ufwc.load_matches()
        self.assertEqual(len(result), 6)
        self.assertEqual(result[0]["goals"]["away"], 2)
        self.assertEqual(result[-1]["matchNumber"], 6)

    def test_catching_up_api_deduplicates_timezone_shift_and_home_order(self):
        self.save(self.saved)
        official = self.initial + [row(3, "2026-09-24", "FRA", "TUR", (1, 0))]
        with patch("scrape_ufwc.fetch_json", return_value={"matches": official}):
            result = scrape_ufwc.load_matches()
        self.assertEqual(len(result), 6)
        self.assertEqual(result[2], official[2])
        self.assertEqual([m["matchNumber"] for m in result], [1, 2, 3, 4, 5, 6])

    def test_failed_extension_does_not_publish_or_overwrite_snapshot(self):
        before = self.base.read_bytes()
        self.data.mkdir()
        published = self.data / "matches.json"
        published.write_text("previous published dataset")
        with patch("scrape_ufwc.fetch_json", side_effect=URLError("offline")):
            with patch("ufwc_live._wiki_infobox", return_value=ITALY):
                with self.assertRaisesRegex(ValueError, "linaje"):
                    scrape_ufwc.main()
        self.assertEqual(self.base.read_bytes(), before)
        self.assertEqual(published.read_text(), "previous published dataset")

    def test_api_correction_cannot_attach_an_unrelated_saved_tail(self):
        self.save(self.saved)
        corrected = deepcopy(self.initial)
        corrected[-1]["goals"] = {"home": 1, "away": 2}
        with patch("scrape_ufwc.fetch_json", return_value={"matches": corrected}):
            # France now holds the title, so the first saved match is still valid.
            self.assertEqual(len(scrape_ufwc.load_matches()), 6)
        corrected[-1] = row(2, "2026-06-25", "ITA", "FRA", (2, 1))
        with patch("scrape_ufwc.fetch_json", return_value={"matches": corrected}):
            with self.assertRaisesRegex(ValueError, "linaje"):
                scrape_ufwc.load_matches()

    def test_empty_api_response_cannot_erase_history(self):
        before = self.base.read_bytes()
        with patch("scrape_ufwc.fetch_json", return_value={"matches": []}):
            with self.assertRaises(ValueError):
                scrape_ufwc.load_matches()
        self.assertEqual(self.base.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()

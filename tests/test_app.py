import csv
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from gaslab.app import Lab, csv_export, weighted_median


class PublicDemoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lab = Lab()

    def test_example_matches_raw_price_differences_and_export_dates(self):
        lab = self.lab
        result = lab.query("2025-12-31", (), sequence=["Down", "+8"])
        all_dates = []
        rows = lab.rows
        limit = lab.index["2025-12-31"]
        for i in range(2, limit - 10):
            if (rows[i-1]["price"] < rows[i-2]["price"]
                and round(100 * (rows[i]["price"] - rows[i-1]["price"])) == 8):
                all_dates.append(rows[i]["date"])
        self.assertEqual([m["date"] for m in result["exact"]["occurrences"]], all_dates)
        self.assertEqual(len(all_dates), 31)
        text = csv_export(result, "exact").decode()
        exported = list(csv.DictReader(io.StringIO(text)))
        self.assertEqual(len(exported), 31)
        self.assertEqual(sum(r["distinct_episode"] == "True" for r in exported), 27)
        for r in exported:
            i = lab.index[r["date"]]
            self.assertEqual(r["end_5_date"], rows[i+5]["date"])
            self.assertEqual(r["sequence"], "Down,+8")
        self.assertEqual(result["exact_benchmark"]["season"], "Matched season mix")
        self.assertEqual(result["query"]["path"], [100])

    def test_physical_rule_compares_only_dates_with_input_coverage(self):
        fields = ["storage", "weather"]
        result = self.lab.query("2025-12-31", fields, {"storage": "Deficit", "weather": "HDD Up / CDD Flat"})
        sample = result["exact"]["matches"]
        self.assertEqual(len(sample), 7)
        valid_seasons = {m["features"]["season"] for m in sample}
        known = [r for r in self.lab.rows[:-11] if all(r["features"][k] for k in fields)
                 and r["features"]["season"] in valid_seasons]
        self.assertEqual(result["exact_benchmark"]["n"], len(known))
        self.assertLess(len(known), 103)
        self.assertEqual(sum(m["outcomes"]["5"] > 0 for m in sample), 4)

    def test_early_dates_and_invalid_filters(self):
        first = self.lab.rows[0]["date"]
        result = self.lab.query(first, ["move"])
        self.assertFalse(result["exact"]["matches"])
        self.assertIsNone(result["benchmark"]["median_5"])
        with self.assertRaises(ValueError):
            self.lab.query(first, ["weather"], {"weather": "unknown"})
        with self.assertRaises(ValueError):
            self.lab.query(first, [])

    def test_report_identity_for_nondefault_config(self):
        with TemporaryDirectory() as folder:
            location = Path(folder)
            (location / "research.json").write_text(json.dumps({"data_id": "wrong"}))
            custom = Lab(spot=self.lab.paths[0], reports=location, analogue_weather=True)
            self.assertIsNone(custom.research()["evaluation"])

    def test_integer_weighted_median_ties(self):
        self.assertEqual(weighted_median([(1, 3), (4, 2), (5, 1)]), 2.5)
        self.assertEqual(weighted_median([(1, 4), (5, 2)]), 1)
        self.assertIsNone(weighted_median([]))

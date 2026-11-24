import csv
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import date, timedelta
from pathlib import Path

from gaslab import weekend


def event(friday, settle, opening, monday, **edits):
    day = date.fromisoformat(friday)
    contract = "NG-2025-07"
    values = {
        "friday_date": friday, "schedule": "regular", "friday_contract": contract,
        "friday_settle": settle, "reopen_date": (day + timedelta(days=2)).isoformat(),
        "reopen_contract": contract, "reopen_price": opening,
        "monday_date": (day + timedelta(days=3)).isoformat(), "monday_contract": contract,
        "monday_settle": monday,
    }
    values.update(edits)
    return values


class WeekendStudyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "weekends.csv"

    def write(self, rows):
        with self.path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=weekend.INPUT_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)

    def test_roll_cannot_be_a_gap(self):
        self.write([event("2024-01-05", 2.6, 3.0, 3.2, reopen_contract="NG-2025-08")])
        result = weekend.study(self.path)
        self.assertEqual(result["gap_up"]["n"], 0)
        self.assertIsNone(result["gap_up"]["rate_up"])
        self.assertEqual(result["exclusion_counts"], {"contract_changed": 1})

    def test_flat_outcome_is_a_counterexample_and_gap_threshold_is_inclusive(self):
        self.write([event("2024-01-05", "2.999", "3.000", "3.000"),
                    event("2024-01-12", "3.000", "3.000", "3.100")])
        result = weekend.study(self.path, season="winter", min_gap="0.001")
        self.assertEqual(result["gap_up"]["n"], 1)
        self.assertEqual(result["gap_up"]["n_flat"], 1)
        self.assertEqual(result["counterexample"]["friday_date"], "2024-01-05")
        self.assertEqual(result["benchmark"]["rate_up"], 0.5)
        self.assertEqual(result["gap_up"]["rate_up"], 0.0)

    def test_duplicates_missing_holidays_unknowns_and_invalid_dates(self):
        duplicated = event("2024-01-05", 2, 3, 4)
        self.write([duplicated, duplicated, event("2024-01-12", 2, 3, 4, schedule="holiday"),
                    event("2024-01-19", 2, "", 4), event("2024-02-02", 2, 3, 4, schedule="unknown"),
                    event("2024-02-09", 2, 3, 4, reopen_date="2024-02-12"),
                    event("2024-02-16", 2, 3, 4, friday_contract="NG1!")])
        result = weekend.study(self.path)
        self.assertEqual(result["coverage"]["eligible"], 0)
        self.assertEqual(set(result["exclusion_counts"]), {
            "duplicate_weekend", "holiday_or_modified", "missing_reopen_price", "missing_weekend_row",
            "unverified_schedule", "missing_or_nonstandard_observation_date", "missing_or_non_delivery_contract",
        })
        self.assertEqual(len(result["exclusions"]), 7)

    def test_all_season_baseline_weights_use_gap_season_counts(self):
        self.write([event("2024-02-09", 2, 3, 2), event("2024-02-16", 2, 3, 2),
                    event("2024-03-08", 2, 3, 4), event("2024-03-15", 3, 2, 3),
                    event("2024-03-22", 3, 2, 3)])
        result = weekend.study(self.path)
        self.assertEqual(result["gap_up"]["n"], 3)
        self.assertAlmostEqual(result["benchmark"]["rate_up"], 1 / 3)
        self.assertEqual(result["benchmark"]["median_move"], -1)
        self.assertEqual(result["by_season"]["spring"]["all"]["n"], 3)
        self.assertEqual(result["benchmark"]["n"], 5)
        self.assertEqual(weekend.study(self.path, season="spring")["gap_up"]["n"], 1)

    def test_lower_bounds_and_invalid_prices(self):
        self.write([event("2024-01-05", -0.010, -0.009, 0),
                    event("2024-01-12", "nan", 0, 0),
                    event("2024-01-19", 1, 2.0005, 3)])
        result = weekend.study(self.path)
        self.assertEqual(result["gap_up"]["n"], 1)
        self.assertEqual(result["gap_up"]["median_move"], 0.009)
        self.assertEqual(result["exclusion_counts"], {"invalid_friday_settle": 1, "invalid_reopen_price": 1})

    def test_user_files_cli_returns_machine_readable_exports(self):
        self.write([event("2024-01-05", 2, 3, 2), event("2024-01-12", 2, 3, "")])
        out = self.path.parent / "out"
        weekend.main([str(self.path), "--json", str(out / "r.json"), "--csv", str(out / "a.csv")])
        result = json.loads((out / "r.json").read_text(encoding="utf-8"))
        self.assertEqual(result["counterexample"]["friday_date"], "2024-01-05")
        with (out / "a.csv").open(newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual([r["status"] for r in rows], ["eligible", "excluded"])
        self.assertEqual(rows[1]["reason"], "missing_monday_settle")

    def test_bad_input_dates_headers_threshold_and_duplicate_output_paths(self):
        with self.assertRaisesRegex(ValueError, "min_gap"):
            weekend.study(self.path, min_gap="NaN")
        self.write([event("2024-01-06", 2, 3, 4)])
        with self.assertRaisesRegex(ValueError, "Friday"):
            weekend.study(self.path)
        self.path.write_text("a,b\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Missing required columns"):
            weekend.study(self.path)
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as stopped:
            weekend.main([str(self.path), "--json", str(self.path)])
        self.assertEqual(stopped.exception.code, 2)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "a,b\n")


if __name__ == "__main__":
    unittest.main()

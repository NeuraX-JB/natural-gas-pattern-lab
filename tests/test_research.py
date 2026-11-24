from datetime import date, timedelta
import unittest
from unittest.mock import patch

from gaslab.research import _paired_interval, discover, evaluate


def make_rows(n):
    start = date(2001, 1, 1)
    return [dict(date=(start + timedelta(days=i)).isoformat(),
                 price=4 + i / 100 + ((i % 12) - 6) ** 2 / 50,
                 features=dict(season=("winter", "summer")[(i // 30) % 2],
                               prior_move="up" if i % 3 else "flat",
                               move="down" if i % 4 == 0 else "up",
                               trend_5="up" if i % 12 < 6 else "down",
                               volatility="normal", storage=None, weather=None))
            for i in range(n)]


class DiscoveryTests(unittest.TestCase):
    def test_rank_fixed_before_holdout_and_windows_do_not_overlap(self):
        rows = make_rows(360)
        split = rows[225]["date"]
        config = dict(split_date=split, top=20, min_train=4)
        result = discover(rows, **config)
        changed = [dict(row, price=-row["price"] if row["date"] >= split else row["price"])
                   for row in rows]
        after = discover(changed, **config)
        self.assertTrue(result["patterns"])
        self.assertEqual([(r["conditions"], r["training_score"]) for r in result["patterns"]],
                         [(r["conditions"], r["training_score"]) for r in after["patterns"]])
        self.assertTrue(any(a["holdout"]["positive_rate"] != b["holdout"]["positive_rate"]
                            for a, b in zip(result["patterns"], after["patterns"])))
        for rule in result["patterns"]:
            for sample in ("train", "holdout"):
                events = rule[sample]["events"]
                self.assertEqual(rule[sample]["n"], len(events))
                for a, b in zip(events, events[1:]):
                    self.assertLess(a["end_date"], b["date"])
            self.assertTrue(all(e["end_date"] < split for e in rule["train"]["events"]))
            self.assertTrue(all(e["date"] >= split for e in rule["holdout"]["events"]))

    def test_cutoff_keeps_even_holdout_outcomes_in_past(self):
        rows = make_rows(350)
        kwargs = dict(cutoff=rows[249]["date"], min_train=3, min_test=1000)
        a = discover(rows, **kwargs)
        self.assertEqual(a, discover(rows[:250], **kwargs))
        self.assertTrue(a["patterns"])
        self.assertTrue(all(r["status"] == "thin holdout" for r in a["patterns"]))
        self.assertTrue(all("storage" not in r["conditions"] for r in a["patterns"]))

    def test_no_history_returns_an_empty_report(self):
        result = discover([], min_train=2)
        self.assertFalse(result["patterns"])
        self.assertEqual(result["tested_rules"], 0)


class EvaluationTests(unittest.TestCase):
    def test_matcher_sees_only_query_prefix_and_metrics_are_paired(self):
        rows = make_rows(180)
        seen = []

        def stub(prefix, as_of, limit, include_paths, include_weather):
            self.assertEqual(as_of, prefix[-1]["date"])
            seen.append(as_of)
            return {"matches": [{"outcomes": {"5": 0.2}}] * 3}

        with patch("gaslab.engine.find_analogues", side_effect=stub):
            report = evaluate(rows, end_date=rows[160]["date"], warmup=30, stride=1,
                              max_queries=70, bootstrap_reps=120)
        records = report["records"]
        self.assertEqual(seen[-1], rows[155]["date"])
        self.assertEqual(records[-1]["end_date"], rows[160]["date"])
        self.assertEqual(report["summary"]["n"], 70)
        self.assertEqual(len(report["summary"]["ci95"]), 2)
        self.assertAlmostEqual(report["summary"]["mae_difference"],
                               report["summary"]["analogue_mae"] - report["summary"]["seasonal_mae"])
        for r in records:
            i = next(i for i, x in enumerate(rows) if x["date"] == r["date"])
            past = [rows[j + 5]["price"] - rows[j]["price"] for j in range(i - 10)
                    if rows[j]["features"]["season"] == rows[i]["features"]["season"]]
            self.assertEqual(r["n_seasonal"], len(past))

    def test_constant_paired_interval_and_short_series(self):
        self.assertEqual(_paired_interval([2.0] * 25, 5, 100, 0), [2.0, 2.0])
        self.assertIsNone(_paired_interval([2.0] * 3, 5, 100, 0))
        self.assertEqual(_paired_interval(list(range(30)), 6, 100, 17),
                         _paired_interval(list(range(30)), 6, 100, 17))


if __name__ == "__main__":
    unittest.main()

import copy
import unittest
from datetime import date, timedelta
from decimal import Decimal

from gaslab.vintage import derive, evaluate, first_fridays, load_events, parse_archive, replay


class VintageTests(unittest.TestCase):
    def test_archive_requires_vintage_identity(self):
        with self.assertRaisesRegex(ValueError, "header"):
            parse_archive(b"observation_date,DHHNGSP\n2018-01-01,2.4\n", date(2018, 1, 5))
        with self.assertRaisesRegex(ValueError, "future observation"):
            parse_archive(b"observation_date,DHHNGSP_20180105\n2018-01-06,2.4\n", date(2018, 1, 5))

    def test_post_decision_target_ignores_later_release_of_elapsed_dates(self):
        origin, check = date(2018, 1, 5), date(2018, 2, 2)
        observed = [(origin - timedelta(days=d), Decimal("2")) for d in range(8, 2, -1)]
        later = observed + [(origin - timedelta(days=1), Decimal("100")), (origin, Decimal("100"))]
        later += [(origin + timedelta(days=d), Decimal(d)) for d in range(1,7)]
        records = derive([origin, check], {origin: (observed,None), check: (later,None)})
        self.assertEqual(records[0]["outcome_5"], 3.0)
        self.assertEqual(records[0]["outcome_date"], "2018-01-10")
        self.assertEqual(records[0]["outcome_known_by"], str(check))
        self.assertEqual(records[1]["outcome_5"], "")

    def test_archived_zero_has_a_dated_exclusion(self):
        events = load_events()
        jan = next(e for e in events if e["decision_date"] == "2021-01-01")
        self.assertEqual(jan["input_status"], "nonpositive_mark_in_feature_window")
        self.assertIsNone(jan["volatility_5_pct"])
        row = replay("2021-01-01", events)
        self.assertIsNone(row["analogue_forecast"])
        future = replay("2022-01-07", events)
        eligible = future["matches"] + future["exact_matches"]
        self.assertNotIn(jan["decision_date"], [x["decision_date"] for x in eligible])

    def test_future_data_does_not_change_prior_forecast(self):
        events = load_events()
        as_of = events[-8]["decision_date"]
        before = replay(as_of, events)
        corrupted = copy.deepcopy(events)
        for e in corrupted:
            if e["decision_date"] >= as_of:
                e["outcome_5"] = 999999.0
            if e["decision_date"] > as_of:
                e["change_5_pct"] = 999999.0
        self.assertEqual(replay(as_of, corrupted), before)
        for match in before["matches"]:
            self.assertLess(match["decision_date"], as_of)
            self.assertLessEqual(match["outcome_known_by"], as_of)
        self.assertNotIn("outcome_5", before["query"])

    def test_bundled_sampling_and_common_evaluation(self):
        rows = load_events()
        self.assertEqual([e["decision_date"] for e in rows], list(map(str,first_fridays())))
        for event in rows[:-1]:
            self.assertLessEqual(event["latest_observation"], event["decision_date"])
            self.assertLess(event["decision_date"], event["outcome_date"])
            self.assertLessEqual(event["outcome_date"], event["outcome_known_by"])
        summary, predictions = evaluate(rows)
        self.assertEqual(summary["n"], len(predictions))
        self.assertGreaterEqual(summary["n"], 80)
        self.assertAlmostEqual(summary["mae_difference"], summary["analogue_mae"] - summary["seasonal_mae"], places=5)

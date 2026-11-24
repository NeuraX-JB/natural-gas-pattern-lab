"""Walk-forward analogue evaluation and chronological discovery of exact rules."""

import argparse
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from datetime import date
import json
from math import ceil
from pathlib import Path
import random
from statistics import mean, median


HORIZON = 5
MATCH_MATURITY = 10
RULE_TEMPLATES = (
    ("prior_move", "move"),
    ("move", "trend_5"),
    ("move", "volatility"),
    ("trend_5", "volatility"),
    ("season", "prior_move", "move"),
    ("season", "trend_5", "volatility"),
    ("move", "storage"),
    ("trend_5", "storage"),
    ("trend_5", "weather"),
    ("storage", "weather"),
)
CAUTION = (
    "Exploratory screen across multiple rules. The training rank sets the displayed rules. "
    "Holdout rates are descriptive and can reflect chance, shared episodes, or market changes."
)


def _dates(rows):
    dates = [r["date"] for r in rows]
    if any(a >= b for a, b in zip(dates, dates[1:])):
        raise ValueError("rows must contain one observation per date in increasing order")
    return dates


def _iso(value):
    if value is None:
        return None
    return date.fromisoformat(value).isoformat()


def _quantile(ordered, fraction):
    position = (len(ordered) - 1) * fraction
    left = int(position)
    right = min(left + 1, len(ordered) - 1)
    return ordered[left] + (ordered[right] - ordered[left]) * (position - left)


def _paired_interval(deltas, block, reps, seed):
    """Percentile interval on the paired MAE difference, sampled in circular blocks."""
    if reps == 0 or len(deltas) < max(20, 2 * block):
        return None
    rng = random.Random(seed)
    n = len(deltas)
    full_blocks, tail = divmod(n, block)
    doubled = list(deltas) * 2
    prefix = [0.0]
    for delta in doubled:
        prefix.append(prefix[-1] + delta)
    totals = [prefix[i + block] - prefix[i] for i in range(n)]
    tails = [prefix[i + tail] - prefix[i] for i in range(n)]
    means = []
    for _ in range(reps):
        total = sum(totals[rng.randrange(n)] for _ in range(full_blocks))
        if tail:
            total += tails[rng.randrange(n)]
        means.append(total / n)
    means.sort()
    return [_quantile(means, 0.025), _quantile(means, 0.975)]


def evaluate(rows, *, start_date=None, end_date=None, stride=5, warmup=252,
             max_queries=0, bootstrap_reps=1000, block_size=10, seed=2718, include_weather=True):
    """Evaluate five-observation forecasts using history available at each origin."""
    from .engine import find_analogues

    if min(stride, warmup, block_size) < 1 or min(max_queries, bootstrap_reps) < 0:
        raise ValueError("stride, warmup and block size must be positive; caps must be nonnegative")
    first, last = _iso(start_date), _iso(end_date)
    if first and last and first > last:
        raise ValueError("start_date must precede or equal end_date")
    dates = _dates(rows)
    stop = bisect_right(dates, last) if last else len(rows)
    start = max(warmup, bisect_left(dates, first) if first else 0)
    origins = range(start, stop - HORIZON, stride)
    if max_queries:
        origins = origins[-max_queries:]
    settings = dict(horizon=HORIZON, stride=stride, warmup=warmup, max_queries=max_queries,
                    bootstrap_reps=bootstrap_reps, seed=seed, include_weather=include_weather,
                    block_size=max(block_size, ceil(HORIZON / stride)),
                    start_date=first, end_date=last, min_analogues=3,
                    benchmark_maturity=MATCH_MATURITY)
    records = []
    excluded = Counter()
    for i in origins:
        # Only this prefix is visible to the matcher. Read the query outcome afterwards.
        match = find_analogues(rows[:i + 1], dates[i], limit=10, include_paths=False, include_weather=include_weather)
        estimates = [m["outcomes"]["5"] for m in match["matches"]]
        if len(estimates) < settings["min_analogues"]:
            excluded["fewer_than_three_analogues"] += 1
            continue
        season = rows[i]["features"].get("season")
        seasonal = [rows[j + HORIZON]["price"] - rows[j]["price"]
                    for j in range(i - MATCH_MATURITY)
                    if season is not None and rows[j]["features"].get("season") == season]
        if not seasonal:
            excluded["no_mature_same_season_outcomes"] += 1
            continue
        analogue, baseline = median(estimates), median(seasonal)
        actual = rows[i + HORIZON]["price"] - rows[i]["price"]
        records.append(dict(date=dates[i], end_date=dates[i + HORIZON], actual=actual,
                            analogue=analogue, seasonal=baseline, n_analogues=len(estimates),
                            n_seasonal=len(seasonal), analogue_error=abs(analogue - actual),
                            seasonal_error=abs(baseline - actual)))
    n = len(records)
    analogue_mae = mean(r["analogue_error"] for r in records) if n else None
    seasonal_mae = mean(r["seasonal_error"] for r in records) if n else None
    deltas = [r["analogue_error"] - r["seasonal_error"] for r in records]
    summary = dict(n=n, analogue_mae=analogue_mae, seasonal_mae=seasonal_mae,
                   mae_difference=mean(deltas) if n else None,
                   ci95=_paired_interval(deltas, settings["block_size"], bootstrap_reps, seed),
                   unit="$/MMBtu", first_date=records[0]["date"] if n else None,
                   last_date=records[-1]["date"] if n else None)
    return dict(status="evaluated" if n else "insufficient history", settings=settings,
                summary=summary, records=records, excluded=dict(excluded), scheduled=len(origins))


def _groups(rows, anchors):
    groups = defaultdict(list)
    for i in anchors:
        features = rows[i]["features"]
        for template in RULE_TEMPLATES:
            values = tuple(features.get(field) for field in template)
            if None not in values:
                groups[template, values].append(i)
    return groups


def _distinct(anchors):
    """Retain the earliest occurrence when 5-session outcome windows share an endpoint."""
    selected = []
    for i in anchors:
        if not selected or i > selected[-1] + HORIZON:
            selected.append(i)
    return selected


def _season_rates(rows, anchors):
    counts = defaultdict(lambda: [0, 0])
    for i in anchors:
        season = rows[i]["features"]["season"]
        counts[season][0] += rows[i + HORIZON]["price"] > rows[i]["price"]
        counts[season][1] += 1
    return {season: up / n for season, (up, n) in counts.items()}


def _stats(rows, anchors, rates, include_events=False, above=True):
    events = [dict(date=rows[i]["date"], end_date=rows[i + HORIZON]["date"],
                   season=rows[i]["features"]["season"],
                   change=round(rows[i + HORIZON]["price"] - rows[i]["price"], 10))
              for i in anchors]
    n = len(events)
    positive = sum(e["change"] > 0 for e in events) / n if n else None
    seasonal = mean(rates[e["season"]] for e in events) if n else None
    result = dict(n=n, positive_rate=positive, seasonal_rate=seasonal,
                  excess_rate=positive - seasonal if n else None,
                  median_change=median(e["change"] for e in events) if n else None)
    if include_events:
        counter = next((e for e in events if (e["change"] > 0) != above), None)
        result.update(dates=[e["date"] for e in events], events=events, counterexample=counter)
    return result


def discover(rows, *, cutoff=None, split_date=None, min_train=12, min_test=5, top=8):
    """Freeze rules on training outcomes; then measure those rules on later observations."""
    if min(min_train, min_test, top) < 1:
        raise ValueError("min_train, min_test and top must be positive")
    dates = _dates(rows)
    cutoff, split_date = _iso(cutoff), _iso(split_date)
    n = bisect_right(dates, cutoff) if cutoff else len(rows)
    split = bisect_left(dates, split_date, 0, n) if split_date else int(n * 0.7)
    settings = dict(cutoff=dates[n - 1] if n else None,
                    split_date=dates[split] if split < n else split_date, horizon=HORIZON,
                    min_train=min_train, min_test=min_test, top=top,
                    templates=[list(fields) for fields in RULE_TEMPLATES])
    train = range(max(0, split - HORIZON))
    holdout = range(split, max(split, n - HORIZON))
    train_rates = _season_rates(rows, train)
    train_groups = _groups(rows, train)
    eligible = []
    for key, all_occurrences in train_groups.items():
        indices = _distinct(all_occurrences)
        if len(indices) < min_train:
            continue
        stats = _stats(rows, indices, train_rates)
        # Shrink small training effects before ranking. No holdout labels used here.
        score = abs(stats["excess_rate"]) * len(indices) / (len(indices) + 30)
        eligible.append((score, key, indices, stats["excess_rate"] >= 0))
    eligible.sort(key=lambda entry: (-entry[0], -len(entry[2]), entry[1]))
    selected = eligible[:top]
    holdout_rates = _season_rates(rows, holdout)
    holdout_groups = _groups(rows, holdout)
    patterns = []
    for score, key, train_indices, above in selected:
        template, values = key
        test_indices = _distinct(holdout_groups.get(key, []))
        train_stats = _stats(rows, train_indices, train_rates, True, above)
        test_stats = _stats(rows, test_indices, holdout_rates, True, above)
        patterns.append(dict(conditions=dict(zip(template, values)), training_score=score,
                             direction="more often up" if above else "less often up",
                             train=train_stats, holdout=test_stats,
                             status="tested" if len(test_indices) >= min_test else "thin holdout"))
    return dict(status="screened" if patterns else "insufficient training occurrences",
                settings=settings, caution=CAUTION, patterns=patterns,
                tested_rules=len(train_groups), eligible_rules=len(eligible),
                train_anchors=len(train), holdout_anchors=len(holdout))


def main(argv=None):
    data_dir = Path(__file__).resolve().parents[1] / "data"
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="task", required=True)
    walk = sub.add_parser("evaluate", help="Compare as-of analogue and seasonal forecasts")
    walk.add_argument("--start-date")
    walk.add_argument("--end-date")
    walk.add_argument("--stride", type=int, default=5)
    walk.add_argument("--warmup", type=int, default=252)
    walk.add_argument("--max-queries", type=int, default=0, help="0 selects all scheduled origins")
    walk.add_argument("--bootstrap-reps", type=int, default=1000)
    rules = sub.add_parser("discover", help="Select exact rules on training dates and score later dates")
    rules.add_argument("--cutoff")
    rules.add_argument("--split-date")
    rules.add_argument("--min-train", type=int, default=12)
    rules.add_argument("--min-test", type=int, default=5)
    rules.add_argument("--top", type=int, default=8)
    for command in (walk, rules):
        command.add_argument("--spot", type=Path, default=data_dir / "spot.csv")
        command.add_argument("--storage", type=Path)
        command.add_argument("--weather", type=Path)
        command.add_argument("--out", type=Path, help="Write full JSON report (default stdout)")
    args = vars(parser.parse_args(argv))
    from .engine import load_rows
    sources = [args.pop(key) for key in ("spot", "storage", "weather")]
    target = args.pop("out")
    fn = evaluate if args.pop("task") == "evaluate" else discover
    try:
        if target:
            for src in filter(None, sources):
                same_link = target.exists() and src.exists() and target.samefile(src)
                if target.resolve() == src.resolve() or same_link:
                    raise ValueError("the output path must differ from every input CSV")
        rows = load_rows(*sources)
        payload = json.dumps(fn(rows, **args), indent=2, allow_nan=False) + "\n"
        if target is None:
            print(payload, end="")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(payload, encoding="utf-8")
    except (OSError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()

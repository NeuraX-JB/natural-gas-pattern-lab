"""Replay monthly Henry Hub decisions from date-stamped ALFRED publications."""
from __future__ import annotations

import argparse
import concurrent.futures
import csv
import hashlib
import io
import json
import random
import statistics
import time
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "vintages"
EVENTS = DATA / "monthly.csv"
INPUTS = ("change_1_pct", "change_5_pct", "volatility_5_pct")
EXACT = ("prior_move", "move", "trend_5")
TRAIN_YEARS = 4
MATCH_COUNT = 10
SEEDS = 7351
SEASONS = ("Winter", "Spring", "Summer", "Autumn")


def first_fridays():
    for year in range(2015, 2026):
        for month in range(1, 13):
            first = date(year, month, 1)
            yield first + timedelta(days=(4 - first.weekday()) % 7)


def source_url(day):
    return ("https://alfred.stlouisfed.org/graph/alfredgraph.csv?id=DHHNGSP"
            f"&vintage_date={day}&cosd={day - timedelta(days=80)}&coed={day}")


def parse_archive(raw, day):
    rows = csv.reader(io.StringIO(raw.decode("utf-8-sig")))
    if next(rows, None) != ["observation_date", f"DHHNGSP_{day:%Y%m%d}"]:
        raise ValueError(f"Unexpected archive or vintage header for {day}")
    values = []
    for observation, price in rows:
        stamp = date.fromisoformat(observation)
        if stamp > day:
            raise ValueError(f"Archive {day} contains a future observation")
        if price in ("", "."):
            continue
        value = Decimal(price)
        if not value.is_finite() or (values and stamp <= values[-1][0]):
            raise ValueError(f"Invalid dated price in archive {day}")
        values.append((stamp, value))
    if len(values) < 6:
        raise ValueError(f"Too few observations in archive {day}")
    return values


def fetch_archive(day, cache=None):
    path = cache / f"{day}.csv" if cache else None
    if path and path.exists():
        raw = path.read_bytes()
        values = parse_archive(raw, day)
    else:
        request = Request(source_url(day), headers={"User-Agent": "NaturalGasPatternLab/1.0 (public research)"})
        for attempt in range(4):
            try:
                with urlopen(request, timeout=26) as response:
                    raw = response.read()
                values = parse_archive(raw, day)
                break
            except (ValueError, OSError):
                if attempt == 3:
                    raise
                time.sleep(1.5 ** attempt)
        if path:
            path.write_bytes(raw)
    return day, values, hashlib.sha256(raw).hexdigest()


def direction(change):
    return "Up" if change > 0 else "Down" if change < 0 else "Flat"


def derive(days, archives):
    result = []
    for i, day in enumerate(days):
        values = archives[day][0]
        marks = [p for _, p in values[-6:]]
        usable = all(p > 0 for p in marks)
        daily = [100 * (float(b / a) - 1) for a, b in zip(marks, marks[1:])] if usable else []
        latest_date, latest_price = values[-1]
        event = {
            "decision_date": str(day), "latest_observation": str(latest_date),
            "season": SEASONS[(day.month % 12) // 3],
            "input_status": "ok" if usable else "nonpositive_mark_in_feature_window",
            "prior_move": direction(marks[-2] - marks[-3]),
            "move": direction(marks[-1] - marks[-2]), "trend_5": direction(marks[-1] - marks[0]),
            "change_1_pct": round(daily[-1], 8) if usable else "",
            "change_5_pct": round(100 * (float(marks[-1] / marks[0]) - 1), 8) if usable else "",
            "volatility_5_pct": round(statistics.pstdev(daily), 8) if usable else "",
            "outcome_date": "", "outcome_known_by": "", "outcome_5": "",
        }
        if i + 1 < len(days):
            next_day = days[i + 1]
            future = [(d, p) for d, p in archives[next_day][0] if d > day]
            if len(future) >= 5 and future[4][1] > 0 and latest_price > 0:
                outcome_date, outcome_price = future[4]
                event.update(outcome_date=str(outcome_date), outcome_known_by=str(next_day),
                             outcome_5=float(outcome_price - latest_price))
        result.append(event)
    return result


def write_csv(path, records):
    temporary = None
    try:
        with NamedTemporaryFile("w", dir=path.parent, encoding="utf-8", newline="", delete=False) as stream:
            temporary = Path(stream.name)
            writer = csv.DictWriter(stream, fieldnames=records[0], lineterminator="\n")
            writer.writeheader()
            writer.writerows(records)
        temporary.chmod(0o644)
        temporary.replace(path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def refresh(cache=None):
    if cache:
        cache.mkdir(exist_ok=True, parents=True)
    days = list(first_fridays())
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        archives = {day: (prices, digest) for day, prices, digest in
                    pool.map(lambda d: fetch_archive(d, cache), days)}
    events = derive(days, archives)
    manifest = [{"decision_date": str(day), "source_url": source_url(day),
                 "raw_sha256": archives[day][1], "reported_prices": len(archives[day][0])}
                for day in days]
    DATA.mkdir(parents=True, exist_ok=True)
    write_csv(EVENTS, events)
    write_csv(DATA / "manifest.csv", manifest)


def load_events(path=EVENTS):
    with Path(path).open(encoding="utf-8", newline="") as stream:
        events = list(csv.DictReader(stream))
    for event in events:
        for field in INPUTS:
            event[field] = float(event[field]) if event[field] != "" else None
        event["outcome_5"] = float(event["outcome_5"]) if event["outcome_5"] else None
    return events


def median(items, key):
    return statistics.median(row[key] for row in items)


def replay(as_of, events=None):
    """Forecast from a supported sampled decision and only earlier settled decisions."""
    if events is None:
        events = load_events()
    query = next((e for e in events if e["decision_date"] == as_of), None)
    if query is None:
        raise ValueError("Choose a decision date from data/vintages/monthly.csv")
    prior = [e for e in events if e["decision_date"] < as_of and e["input_status"] == "ok" and e["outcome_known_by"]
             and e["outcome_known_by"] <= as_of and e["outcome_5"] is not None]
    seasonal = [e for e in prior if e["season"] == query["season"]]
    exact = [e for e in prior if all(e[k] == query[k] for k in EXACT)]
    public = {key: query[key] for key in ("decision_date", "latest_observation", "input_status", "season", *EXACT, *INPUTS)}
    info = {"query": public, "eligible": len(prior), "season_count": len(seasonal),
            "exact_fields": list(EXACT), "exact_count": len(exact),
            "exact_up_count": sum(e["outcome_5"] > 0 for e in exact),
            "exact_matches": [_public_match(e) for e in exact],
            "analogue_forecast": None, "season_forecast": None, "matches": []}
    if query["input_status"] != "ok":
        info.update(exact_count=0, exact_up_count=0, exact_matches=[], status=query["input_status"])
        return info
    if len(prior) < 12 * TRAIN_YEARS or len(seasonal) < 3:
        return info
    scales = {}
    for key in INPUTS:
        center = median(prior, key)
        mad = statistics.median(abs(e[key] - center) for e in prior)
        scales[key] = mad or statistics.pstdev(e[key] for e in prior) or 1.0

    def distance(event):
        components = {k: abs(event[k] - query[k]) / scales[k] for k in INPUTS}
        components["season"] = float(event["season"] != query["season"])
        return sum(components.values()) / len(components), components

    ranked = sorted(((distance(event), event) for event in prior),
                    key=lambda pair: (pair[0][0], pair[1]["decision_date"]))
    nearest = ranked[:MATCH_COUNT]
    info.update(scales=scales,
                analogue_forecast=median([e for _, e in nearest], "outcome_5"),
                season_forecast=median(seasonal, "outcome_5"),
                matches=[dict(_public_match(e), distance=round(d, 5),
                              components={k: round(v, 5) for k, v in c.items()})
                         for (d, c), e in nearest])
    return info


def _public_match(event):
    return {k: event[k] for k in ("decision_date", "latest_observation", "outcome_date",
                                  "outcome_known_by", "outcome_5", "season", *EXACT)}


def block_ci(values, size=3, repetitions=2000):
    """Percentile interval for a mean, circular moving blocks, fixed public seed."""
    if len(values) < 2 * size:
        return None
    rng = random.Random(SEEDS)
    estimates = []
    for _ in range(repetitions):
        sample = []
        while len(sample) < len(values):
            start = rng.randrange(len(values))
            sample.extend(values[(start + j) % len(values)] for j in range(size))
        estimates.append(statistics.mean(sample[:len(values)]))
    estimates.sort()
    return [round(estimates[int(p * (repetitions - 1))], 6) for p in (0.025, 0.975)]


def evaluate(events=None):
    if events is None:
        events = load_events()
    scored = []
    for event in events:
        if event["outcome_5"] is None:
            continue
        evidence = replay(event["decision_date"], events)
        if evidence["analogue_forecast"] is None:
            continue
        analogue_error = abs(evidence["analogue_forecast"] - event["outcome_5"])
        season_error = abs(evidence["season_forecast"] - event["outcome_5"])
        scored.append({"decision_date": event["decision_date"], "latest_observation": event["latest_observation"],
                       "outcome_date": event["outcome_date"], "outcome_known_by": event["outcome_known_by"],
                       "actual": event["outcome_5"], "analogue": evidence["analogue_forecast"],
                       "seasonal": evidence["season_forecast"], "analogue_error": round(analogue_error, 6),
                       "seasonal_error": round(season_error, 6)})
    diff = [s["analogue_error"] - s["seasonal_error"] for s in scored]
    summary = {
        "status": "scored" if scored else "insufficient_history",
        "n": len(scored), "archive_count": len(events), "series": "DHHNGSP",
        "excluded_price_windows": [e["decision_date"] for e in events if e["input_status"] != "ok"],
        "archive_start": events[0]["decision_date"] if events else None,
        "archive_end": events[-1]["decision_date"] if events else None,
        "first_decision": scored[0]["decision_date"] if scored else None,
        "last_decision": scored[-1]["decision_date"] if scored else None,
        "analogue_mae": round(statistics.mean(s["analogue_error"] for s in scored), 6) if scored else None,
        "seasonal_mae": round(statistics.mean(s["seasonal_error"] for s in scored), 6) if scored else None,
        "mae_difference": round(statistics.mean(diff), 6) if diff else None,
        "ci95": block_ci(diff),
        "target": "Fifth quoted spot observation dated after the decision minus the latest spot mark in its ALFRED vintage, $/MMBtu.",
        "decision_rule": "End of first Friday of each month. At least 48 earlier labeled decisions and three from the same season.",
        "match_rule": "Ten closest earlier decisions by training-scaled 1- and 5-observation percentage change, 5-return volatility and season.",
        "interval_method": "Paired circular moving-block bootstrap, 3 monthly decisions per block, 2000 draws, seed 7351.",
    }
    return summary, scored


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", help="Replay an archived first-Friday decision (YYYY-MM-DD).")
    parser.add_argument("--refresh", action="store_true", help="Regenerate derived data from official archived CSVs (network).")
    parser.add_argument("--cache", type=Path, help="Optional external raw CSV cache for --refresh.")
    parser.add_argument("--write-reports", action="store_true", help="Regenerate reports/vintage_summary.json and vintage_scores.csv.")
    args = parser.parse_args()
    if args.refresh:
        refresh(args.cache)
    if args.date:
        try:
            print(json.dumps(replay(args.date), indent=2))
        except ValueError as exc:
            parser.error(str(exc))
    else:
        summary, scored = evaluate()
        if args.write_reports:
            reports = ROOT / "reports"
            reports.mkdir(exist_ok=True)
            (reports / "vintage_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
            if scored:
                write_csv(reports / "vintage_scores.csv", scored)
        print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()

"""Same-contract Friday settlement → weekend reopening → Monday settlement study."""
import argparse
import csv
import json
import math
import re
from collections import Counter, defaultdict
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from pathlib import Path
from statistics import median

SEASONS = ("winter", "spring", "summer", "autumn")
INPUT_COLUMNS = (
    "friday_date", "schedule", "friday_contract", "friday_settle", "reopen_date",
    "reopen_contract", "reopen_price", "monday_date", "monday_contract", "monday_settle",
)
EVENT_COLUMNS = (
    "friday_date", "reopen_date", "monday_date", "season", "contract", "friday_settle",
    "reopen_price", "monday_settle", "gap", "gap_up", "move", "continued_higher",
)
CONTRACT = re.compile(r"NG-(\d{4})-(0[1-9]|1[0-2])\Z")


def _season(day):
    return SEASONS[(day.month % 12) // 3]


def _load(path):
    grouped = defaultdict(list)
    with Path(path).open(encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        header = reader.fieldnames or []
        missing = set(INPUT_COLUMNS).difference(header)
        if missing:
            raise ValueError(f"Missing required columns: {', '.join(sorted(missing))}")
        if len(header) != len(set(header)):
            raise ValueError("Duplicate column name in input.")
        for line, row in enumerate(reader, 2):
            if None in row:
                raise ValueError(f"Line {line} has more fields than the header.")
            row = {key: (value or "").strip() for key, value in row.items()}
            try:
                friday = date.fromisoformat(row["friday_date"])
            except ValueError as exc:
                raise ValueError(f"Line {line} needs a valid friday_date (YYYY-MM-DD).") from exc
            if friday.weekday() != 4:
                raise ValueError(f"Line {line}: friday_date must be a Friday, including on holidays.")
            grouped[friday].append(row)
    return grouped


def _event(friday, rows, threshold):
    if not rows:
        return None, "missing_weekend_row"
    if len(rows) > 1:
        return None, "duplicate_weekend"
    row = rows[0]
    calendar = row["schedule"].lower()
    if calendar != "regular":
        return None, "holiday_or_modified" if calendar == "holiday" else "unverified_schedule"
    sunday = friday + timedelta(days=2)
    monday = friday + timedelta(days=3)
    if row["reopen_date"] != sunday.isoformat() or row["monday_date"] != monday.isoformat():
        return None, "missing_or_nonstandard_observation_date"
    contracts = [row[f"{leg}_contract"] for leg in ("friday", "reopen", "monday")]
    if any(not CONTRACT.fullmatch(code) for code in contracts):
        return None, "missing_or_non_delivery_contract"
    if len(set(contracts)) != 1:
        return None, "contract_changed"
    if contracts[0][3:] <= monday.isoformat()[:7]:
        return None, "delivery_month_already_started"
    prices = []
    for field in ("friday_settle", "reopen_price", "monday_settle"):
        if not row[field]:
            return None, f"missing_{field}"
        try:
            value = Decimal(row[field])
            if not value.is_finite() or abs(value) >= 1_000_000 or value != value.quantize(Decimal("0.001")):
                return None, f"invalid_{field}"
        except InvalidOperation:
            return None, f"invalid_{field}"
        prices.append(value)
    friday_px, open_px, monday_px = prices
    gap = open_px - friday_px
    move = monday_px - open_px
    return {
        "friday_date": friday.isoformat(), "reopen_date": sunday.isoformat(),
        "monday_date": monday.isoformat(), "season": _season(monday), "contract": contracts[0],
        "friday_settle": float(friday_px), "reopen_price": float(open_px),
        "monday_settle": float(monday_px), "gap": float(gap), "gap_up": gap > 0 and gap >= threshold,
        "move": float(move), "continued_higher": move > 0,
    }, None


def _summary(events):
    total = len(events)
    up = sum(row["continued_higher"] for row in events)
    result = {"n": total, "n_up": up, "n_flat": sum(row["move"] == 0 for row in events),
              "rate_up": None, "median_move": None, "wilson_95": None}
    if not total:
        return result
    rate = up / total
    z = 1.95996398454
    denom = 1 + z * z / total
    center = (rate + z * z / (2 * total)) / denom
    half = z * math.sqrt((rate * (1 - rate) + z * z / (4 * total)) / total) / denom
    result.update(rate_up=rate, median_move=median(row["move"] for row in events),
                  wilson_95=[max(0.0, center - half), min(1.0, center + half)])
    return result


def _weighted_baseline(strata, filtered):
    """Standardize the whole-weekend distribution to the exposed season mix."""
    gap_counts = {name: sum(row["gap_up"] for row in rows) for name, rows in strata.items()}
    total_gaps = sum(gap_counts.values())
    result = {"n": 0, "rate_up": None, "median_move": None, "season_weights": {}}
    if not total_gaps:
        if filtered is not None:
            result.update(_summary(strata[filtered]))
        return result
    weighted = []
    for name, num in gap_counts.items():
        if num == 0:
            continue
        rows = strata[name]
        share = Fraction(num, total_gaps)
        result["season_weights"][name] = float(share)
        weighted.extend((row["move"], share / len(rows)) for row in rows)
    weighted.sort()
    result["n"] = len(weighted)
    result["rate_up"] = float(sum(weight for move, weight in weighted if move > 0))
    cumulative = Fraction()
    for idx, (move, weight) in enumerate(weighted):
        cumulative += weight
        if cumulative >= Fraction(1, 2):
            result["median_move"] = (move + weighted[idx + 1][0]) / 2 if cumulative == Fraction(1, 2) else move
            break
    return result


def study(csv_path, season=None, min_gap=0.0):
    """Run a descriptive, calendar-audited study over one input row per Friday."""
    if season is not None and season not in SEASONS:
        raise ValueError(f"season must be one of {', '.join(SEASONS)}")
    try:
        threshold = Decimal(str(min_gap))
    except InvalidOperation as exc:
        raise ValueError("min_gap must be a finite nonnegative dollar amount") from exc
    if not threshold.is_finite() or threshold < 0 or threshold >= 1_000_000:
        raise ValueError("min_gap must be a finite nonnegative dollar amount below 1000000")
    grouped = _load(csv_path)
    events, excluded = [], []
    if grouped:
        first, last = min(grouped), max(grouped)
        for day_offset in range(0, (last - first).days + 1, 7):
            friday = first + timedelta(days=day_offset)
            if season and _season(friday + timedelta(days=3)) != season:
                continue
            event, problem = _event(friday, grouped.get(friday), threshold)
            if problem:
                excluded.append({"friday_date": friday.isoformat(), "reason": problem})
            else:
                events.append(event)
    strata = {s: [e for e in events if e["season"] == s] for s in SEASONS if not season or s == season}
    by_season = {s: {"gap_up": _summary([e for e in rows if e["gap_up"]]), "all": _summary(rows)}
                 for s, rows in strata.items()}
    gaps = [e for e in events if e["gap_up"]]
    return {
        "definition": "Gap = Sunday reopen minus Friday settlement. Move = Monday settlement minus reopen. Prices in USD/MMBtu.",
        "parameters": {"season": season, "min_gap": float(threshold), "instrument": "NYMEX Henry Hub NG futures"},
        "coverage": {"first_input_friday": min(grouped).isoformat() if grouped else None,
                     "last_input_friday": max(grouped).isoformat() if grouped else None,
                     "eligible": len(events), "excluded": len(excluded)},
        "gap_up": _summary(gaps), "benchmark": _weighted_baseline(strata, season),
        "by_season": by_season,
        "counterexample": next((e for e in gaps if not e["continued_higher"]), None),
        "exclusion_counts": dict(Counter(e["reason"] for e in excluded)),
        "exclusions": excluded, "events": events,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="One row per Friday. See docs/FUTURES.md.")
    parser.add_argument("--season", choices=SEASONS)
    parser.add_argument("--min-gap", default="0", help="Minimum positive gap in USD/MMBtu (inclusive).")
    parser.add_argument("--json", type=Path, help="Write summaries, eligible occurrences and exclusions as JSON.")
    parser.add_argument("--csv", type=Path, help="Write eligible occurrences and excluded weekends as CSV.")
    args = parser.parse_args(argv)
    try:
        paths = [p.resolve() for p in (args.input, args.json, args.csv) if p is not None]
        if len(set(paths)) != len(paths):
            raise ValueError("Use a different path for each input and output.")
        report = study(args.input, args.season, args.min_gap)
        rendered = json.dumps(report, indent=2, allow_nan=False)
        if args.json:
            args.json.parent.mkdir(parents=True, exist_ok=True)
            args.json.write_text(rendered + "\n", encoding="utf-8")
        if args.csv:
            args.csv.parent.mkdir(parents=True, exist_ok=True)
            with args.csv.open("w", encoding="utf-8", newline="") as dest:
                writer = csv.DictWriter(dest, fieldnames=("status", "reason", *EVENT_COLUMNS))
                writer.writeheader()
                audit = [dict(status="eligible", **row) for row in report["events"]]
                audit.extend(dict(status="excluded", **row) for row in report["exclusions"])
                writer.writerows(sorted(audit, key=lambda row: row["friday_date"]))
    except (OSError, ValueError, csv.Error) as exc:
        parser.exit(2, f"weekend study: {exc}\n")
    if not args.json and not args.csv:
        print(rendered)


if __name__ == "__main__":
    main()

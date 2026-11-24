"""Rebuild the distributed snapshot from pinned prices and dated EIA issues."""
import argparse
import csv
import hashlib
import html
import io
import json
import re
import urllib.request
from datetime import date, datetime, time
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gaslab.timezones import NEW_YORK

HERE = Path(__file__).resolve().parent
COMMIT = "fe9c40ee28516489b055736045746dd7faf00898"
SPOT = f"https://raw.githubusercontent.com/datasets/natural-gas/{COMMIT}/data/daily.csv"
ARCHIVE = "https://www.eia.gov/naturalgas/weekly/archivenew_ngwu"
HISTORY = re.compile(
    r"""name:\s*['"]working gas in storage['"]\s*,[^{}]*?data:\s*\[(.*?)\]\s*[,\n\r ]*\}""",
    re.IGNORECASE | re.DOTALL,
)
POINT = re.compile(r"\[\s*Date.UTC\(\s*(\d+),\s*(\d+),\s*(\d+)\s*\)\s*,\s*(\d+)\s*\]")
SPOT_FIELDS = ["date", "price"]
STORAGE_FIELDS = ["period_end", "published_at", "inventory_bcf", "verified", "five_year_avg_bcf"]


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def csv_bytes(header, rows):
    out = io.StringIO(newline="")
    w = csv.writer(out, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    return out.getvalue().encode("utf-8")


def archive_url(issue):
    return f"{ARCHIVE}/{issue[:4]}/{issue[5:7]}_{issue[8:]}/"


def normalized(markup):
    return " ".join(html.unescape(re.sub(r"<[^>]*>", " ", markup)).split())


def five_year_average(page, current):
    for table in re.findall(r"<table\b.*?</table>", page, re.S | re.I):
        clean = normalized(table).lower()
        if "historical comparisons" not in clean or "5-year average" not in clean:
            continue
        for row in re.findall(r"<tr\b.*?</tr>", table, re.S | re.I):
            cells = [normalized(cell) for cell in re.findall(r"<t[hd]\b[^>]*>(.*?)</t[hd]>", row, re.S | re.I)]
            if len(cells) == 5 and cells[0].lower() == "total":
                try:
                    average = int(cells[3].replace(",", ""))
                    pct = float(cells[4].replace("%", ""))
                except ValueError:
                    continue
                if average > 0 and abs(100 * (current / average - 1) - pct) <= 0.15:
                    return average
    return None


def parse_issue(raw, expected_date):
    page = raw.decode("utf-8", errors="replace")
    page = re.sub(r"<!--.*?-->", "", page, flags=re.S)
    release = re.search(r"Release date:\s*</strong>(.*?)\|", page, re.S | re.I)
    if not release:
        raise ValueError("missing printed release date")
    datestamp = normalized(release[1]).replace(".", "").replace(",", "").replace("Sept ", "Sep ")
    try:
        release_day = datetime.strptime(datestamp, "%B %d %Y").date()
    except ValueError:
        release_day = datetime.strptime(datestamp, "%b %d %Y").date()
    if release_day.isoformat() != expected_date:
        raise ValueError("printed date differs from archive index")
    matches = HISTORY.findall(page)
    if len(matches) != 1:
        raise ValueError("ambiguous or missing working-gas series")
    periods = {}
    for y, m, d, bcf in POINT.findall(matches[0]):
        day = date(int(y), int(m) + 1, int(d))
        volume = int(bcf)
        if day.weekday() != 4 or day >= release_day or not 0 < volume < 6000:
            raise ValueError("invalid inventory point")
        if day in periods and periods[day] != volume:
            raise ValueError("conflicting inventory within issue")
        periods[day] = volume
    if not periods or (release_day - max(periods)).days > 14:
        raise ValueError("missing or stale latest inventory")
    average = five_year_average(page, periods[max(periods)])
    return periods, average


def issue_digest(periods, average):
    last_period = max(periods)
    return digest(csv_bytes(["period", "inventory", "official_five_year_mean"],
                            ((d, v, average if d == last_period else None)
                             for d, v in sorted(periods.items()))))


def price_snapshot(raw):
    rows = []
    for row in csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))):
        day = row["Date"]
        if "2008-01-01" <= day <= "2025-12-31" and row["Price"].strip():
            if rows and day <= rows[-1][0]:
                raise ValueError("price observations are duplicated or unsorted")
            float(row["Price"])
            rows.append((day, row["Price"]))
    return csv_bytes(SPOT_FIELDS, rows)


def storage_snapshot(issues):
    seen = {}
    rows = []
    for stamp, periods, average in issues:
        dt = datetime.combine(date.fromisoformat(stamp), time(23, 59), NEW_YORK)
        latest = max(periods)
        for day, level in sorted(periods.items()):
            # New or revised stocks and newly printed comparisons can change a replay.
            official = average if day == latest else None
            earlier = seen.get(day)
            changed = earlier is None or earlier[0] != level
            add_comparison = official is not None and earlier != (level, official)
            if changed or add_comparison:
                rows.append((day, dt.isoformat(), level, "true", official))
                seen[day] = level, official
    return csv_bytes(STORAGE_FIELDS, rows)


def read_remote(url):
    request = urllib.request.Request(url, headers={"User-Agent": "natural-gas-pattern-lab/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-only", action="store_true", help="Check the five bundled CSVs offline.")
    parser.add_argument("--output", type=Path,
                        help="Destination for the regenerated CSVs.")
    parser.add_argument("--archive-cache", type=Path, help="Directory holding YYYY-MM-DD.html files.")
    args = parser.parse_args()
    manifest = json.loads((HERE / "manifest.json").read_text())
    if args.verify_only:
        for filename in ("spot.csv", "storage.csv", "storage_issues.csv", "weather.csv", "weather_sources.csv"):
            if digest((HERE / filename).read_bytes()) != manifest[filename]["sha256"]:
                raise ValueError(f"Digest mismatch for {filename}")
        print("Verified all five bundled CSV files against the frozen manifest.")
        return
    if args.output is None:
        parser.error("supply --output or --verify-only")
    spot = price_snapshot(read_remote(SPOT))
    with (HERE / "storage_issues.csv").open() as f:
        sources = list(csv.DictReader(f))
    issues = []
    for source in sources:
        day = source["issue_date"]
        cache = args.archive_cache / (day + ".html") if args.archive_cache else None
        raw = cache.read_bytes() if cache and cache.exists() else read_remote(archive_url(day))
        periods, avg = parse_issue(raw, day)
        if issue_digest(periods, avg) != source["series_sha256"]:
            raise ValueError(f"Archived source changed on {day}; review before updating.")
        if cache and not cache.exists():
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(raw)
        issues.append((day, periods, avg))
    storage = storage_snapshot(issues)
    for filename, blob in [("spot.csv", spot), ("storage.csv", storage)]:
        if digest(blob) != manifest[filename]["sha256"]:
            raise ValueError(f"{filename} differs from the frozen edition")
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "spot.csv").write_bytes(spot)
    (args.output / "storage.csv").write_bytes(storage)
    print(f"Verified and rebuilt {len(issues)} storage editions and the pinned spot archive.")


if __name__ == "__main__":
    main()

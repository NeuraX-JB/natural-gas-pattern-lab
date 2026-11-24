"""Reproduce the frozen first-Wednesday forecast pairs from NOAA CPC's dated files."""
import argparse
import csv
import hashlib
import io
import json
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = "https://ftp.cpc.ncep.noaa.gov/htdocs/degree_days/weighted/daily_forecasts_7day"
FIELDS = ["previous_run_at", "current_run_at", "available_at", "valid_date", "hdd_previous",
          "hdd_current", "cdd_previous", "cdd_current", "verified"]


def source_url(day, kind):
    return f"{BASE}/{day:%Y/%m/%d}/Population.{kind}.txt"


def read_source(day, kind, cache):
    url = source_url(day, kind)
    prefix = cache / f"{day}-{kind}" if cache else None
    if prefix and prefix.with_suffix(".txt").exists() and prefix.with_suffix(".time").exists():
        data = prefix.with_suffix(".txt").read_bytes()
        modified = prefix.with_suffix(".time").read_text()
    else:
        req = urllib.request.Request(url, headers={"User-Agent": "natural-gas-pattern-lab/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read()
            modified = resp.headers["Last-Modified"]
        if not modified:
            raise ValueError("archive timestamp unavailable")
        if prefix:
            cache.mkdir(parents=True, exist_ok=True)
            prefix.with_suffix(".txt").write_bytes(data)
            prefix.with_suffix(".time").write_text(modified)
    available = parsedate_to_datetime(modified).astimezone(timezone.utc)
    text = data.decode().splitlines()
    m = re.search(r"Issued (\d{4})UTC (\d{8})", text[0])
    if not m or m[2] != day.strftime("%Y%m%d") or "NDFD" not in text[0] or kind not in text[0]:
        raise ValueError("issue heading differs from expected forecast")
    run = datetime.strptime(m[2] + m[1], "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
    if ("Weights: Population" not in text or available < run or available.date() != day):
        raise ValueError("source weight or same-day archived file timestamp unavailable")
    header = next(line for line in text if line.startswith("Region|")).split("|")[1:-1]
    values = next(line for line in text if line.startswith("CONUS|")).split("|")[1:-1]
    if len(header) != 7 or len(values) != 7:
        raise ValueError("incomplete CONUS forecast")
    forecast = {datetime.strptime(k, "%Y%m%d").date(): int(v) for k, v in zip(header, values)}
    if min(forecast.values()) < 0 or set(forecast) != {day + timedelta(days=i) for i in range(7)}:
        raise ValueError("invalid forecast interval")
    return run, available, forecast, hashlib.sha256(data).hexdigest()


def build_pair(day, cache):
    previous = day - timedelta(days=1)
    files = {(d, k): read_source(d, k, cache) for d in (previous, day) for k in ("Heating", "Cooling")}
    earlier, later = files[previous, "Heating"][0], files[day, "Heating"][0]
    if (earlier != files[previous, "Cooling"][0] or later != files[day, "Cooling"][0]):
        raise ValueError("forecast initialization mismatch")
    available = max(x[1] for x in files.values())
    common = set.intersection(*(set(x[2]) for x in files.values()))
    if len(common) != 6:
        raise ValueError("expected six common valid dates")
    rows = [(earlier.isoformat(), later.isoformat(), available.isoformat(), valid.isoformat(),
             files[previous, "Heating"][2][valid], files[day, "Heating"][2][valid],
             files[previous, "Cooling"][2][valid], files[day, "Cooling"][2][valid], "true")
            for valid in sorted(common)]
    evidence = [(d, k, rec[1].isoformat(), rec[3]) for (d, k), rec in files.items()]
    return rows, evidence


def as_csv(fields, rows):
    out = io.StringIO(newline="")
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(fields)
    writer.writerows(rows)
    return out.getvalue().encode()


def sampled_days():
    for y in range(2016, 2026):
        for m in range(1, 13):
            first = date(y, m, 1)
            yield first + timedelta(days=(2 - first.weekday()) % 7)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path)
    args = parser.parse_args()
    manifest = json.loads((HERE / "manifest.json").read_text())
    with (HERE / "weather_sources.csv").open() as f:
        expected = list(csv.DictReader(f))
    days = sorted({date.fromisoformat(r["run_date"]) for r in expected} & set(sampled_days()))
    rows = []
    evidence = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for records, sources in pool.map(lambda d: build_pair(d, args.cache), days):
            rows.extend(records)
            evidence.extend(sources)
    evidence_fields = ["run_date", "metric", "last_modified_utc", "source_sha256"]
    blobs = {"weather.csv": as_csv(FIELDS, rows),
             "weather_sources.csv": as_csv(evidence_fields, evidence)}
    for name, blob in blobs.items():
        if hashlib.sha256(blob).hexdigest() != manifest[name]["sha256"]:
            raise ValueError(f"{name} differs from the frozen edition")
    args.output.mkdir(parents=True, exist_ok=True)
    for name, blob in blobs.items():
        (args.output / name).write_bytes(blob)
    print(f"Verified {len(days)} complete forecast pairs.")


if __name__ == "__main__":
    main()

"""Application data, offline HTTP endpoints and CSV exports."""

import csv
import hashlib
import io
import json
import mimetypes
from collections import Counter
from math import lcm
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .engine import DEFAULT_FIELDS, EXACT_FIELDS, find_analogues, load_rows, match_exact

ROOT = Path(__file__).resolve().parent.parent


def input_fingerprint(paths):
    digest = hashlib.sha256()
    for path in paths:
        if path is None:
            digest.update(b"absent;")
        else:
            value = Path(path).read_bytes()
            digest.update(len(value).to_bytes(8, "big"))
            digest.update(value)
    return digest.hexdigest()


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def weighted_median(pairs):
    if not pairs:
        return None
    pairs = sorted(pairs)
    total = sum(weight for _, weight in pairs)
    accumulated = 0
    for i, (value, weight) in enumerate(pairs):
        accumulated += weight
        if 2 * accumulated == total:
            return (value + pairs[i+1][0]) / 2
        if 2 * accumulated > total:
            return value


class Lab:
    def __init__(self, spot=None, storage=None, weather=None, reports=None, analogue_weather=False):
        bundled = spot is None
        spot = Path(spot) if spot else ROOT / "data/spot.csv"
        if bundled and storage is None and (ROOT / "data/storage.csv").is_file():
            storage = ROOT / "data/storage.csv"
        if bundled and weather is None and (ROOT / "data/weather.csv").is_file():
            weather = ROOT / "data/weather.csv"
        self.analogue_weather = analogue_weather
        self.paths = (spot, storage, weather)
        self.rows = load_rows(*self.paths)
        if not self.rows:
            raise ValueError("The input has no usable spot-price observations.")
        self.index = {row["date"]: i for i, row in enumerate(self.rows)}
        self.reports = Path(reports) if reports else ROOT / "reports"
        self.bundled = bundled
        self.source = ("EIA Henry Hub daily cash spot observations" if bundled
                       else "User-supplied spot observations")
        self.fingerprint = input_fingerprint(self.paths) + ("-with-weather" if analogue_weather else "-core")

    @lru_cache(maxsize=1)
    def meta(self):
        values = {key: sorted({row["features"][key] for row in self.rows
                               if row["features"].get(key) is not None})
                  for key in EXACT_FIELDS}
        if "move_cents" in values:
            values["move_cents"].sort(key=int)
        return {"dates": list(self.index), "default_date": self.rows[-1]["date"],
                "fields": EXACT_FIELDS, "values": values, "default_fields": list(DEFAULT_FIELDS),
                "source": self.source, "units": "$/MMBtu",
                "availability_note": "Daily spot history is a frozen observation-date snapshot. Original quote publication times and past revisions are unverified. Results are retrospective.",
                "data_id": self.fingerprint}

    def benchmark(self, date, seasons, required_fields=(), sequence_length=0):
        """Each eligible anchor gets its season's target share divided by its stratum size."""
        if isinstance(seasons, str):
            seasons = {seasons: 1}
        q = self.index[date]
        indices = [i for i in range(sequence_length, max(0, q - 10))
                   if self.rows[i]["features"].get("season") in seasons
                   and all(self.rows[i]["features"].get(k) is not None for k in required_fields)]
        counts = Counter(self.rows[i]["features"]["season"] for i in indices)
        label = next(iter(seasons)) if len(seasons) == 1 else "Matched season mix"
        if not indices:
            return {"season": label, "n": 0, "path": [], "median_5": None, "up_rate": None}
        # Integer weights preserve exact halfway ties in the weighted median.
        unit = lcm(*counts.values())
        weights = {k: unit // n * seasons[k] for k, n in counts.items()}
        observations = [(i, weights[self.rows[i]["features"]["season"]]) for i in indices]
        changes = [(round(self.rows[i+5]["price"] - self.rows[i]["price"], 8), w)
                   for i, w in observations]
        positive_weight = sum(w for delta, w in changes if delta > 0)
        path = []
        for h in range(11):
            prices = [(100 * self.rows[i+h]["price"] / self.rows[i]["price"], w)
                      for i, w in observations if self.rows[i]["price"] != 0]
            path.append(weighted_median(prices))
        return {"season": label, "season_counts": dict(seasons), "n": len(changes), "path": path,
                "median_5": weighted_median(changes),
                "up_rate": positive_weight / sum(w for _, w in changes)}

    def pre_path(self, index):
        base = self.rows[index]["price"]
        return [round(100 * self.rows[j]["price"] / base, 5) if j >= 0 and base else None
                for j in range(index-5, index)]

    def query(self, date, fields, conditions=None, sequence=None):
        if date not in self.index:
            raise ValueError("Select an observed date from the dataset.")
        if (not fields and not sequence) or set(fields) - EXACT_FIELDS.keys():
            raise ValueError("Choose a move sequence or at least one valid field.")
        overrides = conditions or {}
        if set(overrides) - set(fields):
            raise ValueError("Every overridden condition must be selected.")
        for key, value in overrides.items():
            if value not in self.meta()["values"][key]:
                raise ValueError(f"Unknown value for {key}: {value}")
        exact = match_exact(self.rows, date, fields=fields, conditions=overrides, sequence=sequence or None)
        analogue = find_analogues(self.rows, date, include_weather=self.analogue_weather)
        q = self.index[date]
        anchor = self.rows[q]
        for m in (*analogue["matches"], *exact["matches"]):
            m["path_pre"] = self.pre_path(self.index[m["date"]])
        season = anchor["features"]["season"]
        benchmark = self.benchmark(date, season)
        rule_seasons = Counter(m["features"]["season"] for m in exact["matches"])
        if not rule_seasons:
            rule_seasons = {exact["conditions"].get("season") or season: 1}
        return {"exact": exact, "analogue": analogue, "benchmark": benchmark,
                "exact_benchmark": self.benchmark(date, rule_seasons, fields, len(exact["sequence"])),
                "query": {"date": date, "price": anchor["price"], "features": anchor["features"],
                          "path_pre": self.pre_path(q), "path": [100]}}

    def research(self):
        path = self.reports / "research.json"
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("data_id") == self.fingerprint:
                vintage = self.reports / "vintage_summary.json"
                if self.bundled and vintage.is_file():
                    data["vintage"] = json.loads(vintage.read_text(encoding="utf-8"))
                return data
        return {"status": "Run the research command on these inputs to generate matched results.",
                "evaluation": None, "discovery": None}

    def run_research(self, **config):
        from .research import discover, evaluate
        return {"data_id": self.fingerprint, "evaluation": evaluate(self.rows, include_weather=self.analogue_weather, **config),
                "discovery": discover(self.rows), "status": "complete"}


def csv_export(result, mode):
    if mode not in {"exact", "analogue"}:
        raise ValueError("Export mode must be exact or analogue.")
    group = result[mode]
    matches = group["occurrences"] if mode == "exact" else group["matches"]
    distinct = {m["date"] for m in group["matches"]}
    text = io.StringIO(newline="")
    columns = ["query_date", "mode", "conditions_json", "sequence", "date", "price", "distinct_episode",
               "delta_1_usd_mmbtu", "delta_5_usd_mmbtu", "delta_10_usd_mmbtu",
               "end_1_date", "end_5_date", "end_10_date", "distance", *EXACT_FIELDS]
    writer = csv.writer(text)
    writer.writerow(columns)
    for m in matches:
        writer.writerow([result["query"]["date"], mode, json.dumps(group.get("conditions", {}), sort_keys=True),
                         ",".join(group.get("sequence", [])), m["date"], m["price"], m["date"] in distinct,
                         *(m["outcomes"].get(str(i)) for i in (1, 5, 10)),
                         *(m["outcome_dates"][str(i)] for i in (1, 5, 10)), m.get("distance"),
                         *(m["features"].get(f) for f in EXACT_FIELDS)])
    return text.getvalue().encode("utf-8")


def make_handler(lab):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            url = urlparse(self.path)
            try:
                if url.path == "/api/meta":
                    return self.reply_json(lab.meta())
                if url.path == "/api/research":
                    return self.reply_json(lab.research())
                if url.path in {"/api/query", "/api/export"}:
                    p = parse_qs(url.query, keep_blank_values=True)
                    fields = tuple(dict.fromkeys(s for s in p.get("fields", [",".join(DEFAULT_FIELDS)])[0].split(",") if s))
                    date = p.get("date", [lab.rows[-1]["date"]])[0]
                    conditions = {k[2:]: v[0] for k, v in p.items() if k.startswith("c_")}
                    sequence = [s.strip() for s in p.get("sequence", [""])[0].split(",") if s.strip()]
                    result = lab.query(date, fields, conditions, sequence)
                    if url.path == "/api/query":
                        return self.reply_json(result)
                    mode = p.get("mode", ["exact"])[0]
                    payload = csv_export(result, mode)
                    return self.reply(200, "text/csv; charset=utf-8", payload,
                                      f'attachment; filename="henry-hub-{date}-{mode}.csv"')
                pages = {"/": "index.html", "/index.html": "index.html",
                         "/app.js": "app.js", "/style.css": "style.css"}
                if url.path not in pages:
                    return self.reply_json({"error": "Page not found"}, 404)
                f = ROOT / "gaslab/web" / pages[url.path]
                return self.reply(200, mimetypes.guess_type(f.name)[0] or "text/plain", f.read_bytes())
            except (ValueError, KeyError) as e:
                return self.reply_json({"error": str(e)}, 400)
            except (OSError, ArithmeticError) as e:
                return self.reply_json({"error": str(e)}, 500)

        def reply_json(self, obj, code=200):
            self.reply(code, "application/json; charset=utf-8", json.dumps(obj, allow_nan=False,
                       separators=(",", ":")).encode("utf-8"))

        def reply(self, code, mime, payload, disposition=None):
            self.send_response(code)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            if disposition:
                self.send_header("Content-Disposition", disposition)
            self.end_headers()
            try:
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                pass

    return Handler


def server_for(lab, port):
    return ThreadingHTTPServer(("127.0.0.1", port), make_handler(lab))

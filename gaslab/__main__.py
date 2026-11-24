"""Command line entry point for the local research lab."""

import argparse
import json
from pathlib import Path
import sys
import webbrowser

from .app import Lab, csv_export, save_json, server_for
from .engine import DEFAULT_FIELDS


def add_inputs(parser):
    parser.add_argument("--spot", type=Path, help="CSV with date,price (default: bundled history)")
    parser.add_argument("--storage", type=Path, help="Optional verified storage-vintage CSV")
    parser.add_argument("--weather", type=Path, help="Optional aligned forecast-run CSV")
    parser.add_argument("--analogue-weather", action="store_true",
                        help="Include verified weather revisions in analogue distances when available")
    parser.add_argument("--reports", type=Path, help="Directory for this dataset's research.json")


def run(argv=None):
    parser = argparse.ArgumentParser(prog="python -m gaslab", description=__doc__)
    commands = parser.add_subparsers(dest="command")
    demo = commands.add_parser("demo", help="Open the offline local browser app")
    demo.add_argument("--port", type=int, default=8765)
    demo.add_argument("--no-browser", action="store_true")
    query = commands.add_parser("query", help="Find and export a historical pattern")
    query.add_argument("--date", help="Observation date, default most recent")
    query.add_argument("--sequence", help="1–10 comma-separated moves, oldest first (Down,+8)")
    query.add_argument("--fields", default=None,
                       help="Comma-separated exact fields (see README)")
    query.add_argument("--where", action="append", default=[], metavar="FIELD=VALUE",
                       help="Override a selected field; repeat to build an AND rule")
    query.add_argument("--mode", choices=("exact", "analogue"), default="exact")
    query.add_argument("--csv", type=Path, help="Export all individual occurrence dates")
    research = commands.add_parser("research", help="Rebuild the discovery and walk-forward report")
    research.add_argument("--stride", type=int, default=5)
    research.add_argument("--warmup", type=int, default=252)
    research.add_argument("--max-queries", type=int, default=0)
    for command in (demo, query, research):
        add_inputs(command)
    if argv is None:
        argv = sys.argv[1:]
    args = parser.parse_args(argv or ["demo"])
    try:
        lab = Lab(args.spot, args.storage, args.weather, args.reports, args.analogue_weather)
        if args.command == "demo":
            with server_for(lab, args.port) as server:
                url = f"http://127.0.0.1:{server.server_port}"
                print(f"Natural Gas Pattern Lab  |  {url}\nPress Ctrl+C to stop.", flush=True)
                if not args.no_browser:
                    webbrowser.open(url)
                try:
                    server.serve_forever()
                except KeyboardInterrupt:
                    pass
        elif args.command == "query":
            sequence = [s.strip() for s in (args.sequence or "").split(",") if s.strip()]
            fields = (tuple(key.strip() for key in args.fields.split(",") if key.strip())
                      if args.fields is not None else (() if sequence else DEFAULT_FIELDS))
            values = {}
            for condition in args.where:
                field, sep, value = condition.partition("=")
                if not sep or field in values:
                    raise ValueError("Use one --where FIELD=VALUE per distinct selected field.")
                values[field] = value
            result = lab.query(args.date or lab.rows[-1]["date"], fields, values, sequence)
            group = result[args.mode]
            if args.csv:
                if args.csv.suffix.lower() != ".csv" or args.csv.resolve() in {
                    Path(x).resolve() for x in lab.paths if x is not None
                }:
                    raise ValueError("Choose an output CSV separate from the input files.")
                args.csv.write_bytes(csv_export(result, args.mode))
            report = {"as_of": result["query"]["date"], "conditions": group.get("conditions"),
                      "sequence": group.get("sequence"), "status": group["status"], "all_occurrences": len(group.get("occurrences", group["matches"])),
                      "non_overlapping": len(group["matches"]), "summary": group["summary"],
                      "selected_dates": [m["date"] for m in group["matches"]],
                      "export": str(args.csv) if args.csv else None}
            print(json.dumps(report, indent=2, allow_nan=False))
        elif args.command == "research":
            result = lab.run_research(stride=args.stride, warmup=args.warmup, max_queries=args.max_queries)
            target = lab.reports / "research.json"
            if target.resolve() in {Path(x).resolve() for x in lab.paths if x is not None}:
                raise ValueError("Choose a report directory separate from your input path.")
            save_json(target, result)
            print(json.dumps(result["evaluation"]["summary"], indent=2))
            print(f"Saved {target}")
    except (OSError, ValueError) as err:
        parser.exit(2, f"gaslab: {err}\n")


if __name__ == "__main__":
    run()

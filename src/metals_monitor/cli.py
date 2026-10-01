"""Command-line entry points.

    python -m metals_monitor all --demo          # whole pipeline on synthetic data
    python -m metals_monitor pull                # one step (uses .env / EE_PROJECT)
    metals-monitor features --start 2020-01-01   # same, via the installed console script

The numbered scripts in pipeline/ call the same step functions.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from types import SimpleNamespace

import pandas as pd

from . import config as C
from . import steps

STEPS = {
    "check-sites": steps.step_check_sites,
    "pull": steps.step_pull,
    "features": steps.step_features,
    "fundamentals": steps.step_fundamentals,
    "figures": steps.step_figures,
    "report": steps.step_report,
}
DEFAULT_SEQUENCE = ["pull", "features", "fundamentals", "figures", "report"]


def _default_end():
    return pd.Timestamp.today().normalize().replace(day=1).strftime("%Y-%m-%d")


def common_parser(description=None):
    ap = argparse.ArgumentParser(description=description,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--demo", action="store_true",
                    help="synthetic data in ./demo_out (no Earth Engine needed)")
    ap.add_argument("--project", default=os.environ.get("EE_PROJECT"),
                    help="Google Cloud project registered for Earth Engine (or EE_PROJECT in .env)")
    ap.add_argument("--config", default=None, help="sites YAML (default config/sites.yaml)")
    ap.add_argument("--start", default=os.environ.get("MM_START", C.DEFAULT_START))
    ap.add_argument("--end", default=os.environ.get("MM_END") or _default_end(),
                    help="exclusive month start (default: first day of the current month)")
    ap.add_argument("--sites", help="comma-separated site ids")
    ap.add_argument("--commodities", help="comma-separated commodities")
    ap.add_argument("--no-climate", action="store_true")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--chunk-months", type=int, default=12)
    ap.add_argument("--refresh", action="store_true", help="ignore the cache and re-pull everything")
    ap.add_argument("--no-prices", action="store_true")
    ap.add_argument("--prices-csv", default=os.environ.get("MM_PRICES_CSV"))
    ap.add_argument("--production-csv", default=os.environ.get("MM_PRODUCTION_CSV"))
    ap.add_argument("--anomaly-months", type=int, default=6)
    ap.add_argument("-v", "--verbose", action="store_true")
    return ap


def build_context(args):
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    for noisy in ("matplotlib", "fontTools", "PIL", "googleapiclient", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    sites, regions, tickers = C.load_config(args.config)
    sites, regions = C.select(sites, regions,
                              args.sites.split(",") if args.sites else None,
                              args.commodities.split(",") if args.commodities else None,
                              include_climate=not args.no_climate)
    paths = C.Paths(C.repo_root(), demo=args.demo).ensure()
    return SimpleNamespace(args=args, demo=args.demo, paths=paths, sites=sites, regions=regions,
                           tickers=tickers, start=args.start, end=args.end)


def run_step(name, argv=None, description=None):
    """Used by pipeline/0N_*.py."""
    args = common_parser(description).parse_args(argv)
    ctx = build_context(args)
    STEPS[name](ctx)
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    choices = list(STEPS) + ["all", "list"]
    if not argv or argv[0] not in choices:
        print(f"usage: metals-monitor {{{','.join(choices)}}} [options]   (-h after a command for options)")
        return 2
    cmd, rest = argv[0], argv[1:]
    args = common_parser(__doc__).parse_args(rest)
    ctx = build_context(args)
    if cmd == "list":
        for k, v in ctx.sites.items():
            print(f"{k:20s} {','.join(v['commodities']):24s} {v['role']:16s} "
                  f"({v['lat']:.3f}, {v['lon']:.3f}) r={v['buffer_km']}km  [{','.join(v['indicators'])}]")
        for k, v in ctx.regions.items():
            print(f"{k:20s} {','.join(v['commodities']):24s} region/{v['risk_side']:9s} bbox={v['bbox']}")
        return 0
    for name in (DEFAULT_SEQUENCE if cmd == "all" else [cmd]):
        logging.getLogger("metals_monitor").info("== %s ==", name)
        STEPS[name](ctx)
    return 0


if __name__ == "__main__":
    sys.exit(main())

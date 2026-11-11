"""Command line.

    python -m agileflex backtest [--no-forecast]   # replay 12 months of real prices -> outputs/
    python -m agileflex plan [--force]              # tonight's plan from tomorrow's prices -> plans/
    python -m agileflex fetch [--refresh]           # (re)download a year of prices + carbon -> data/
"""
from __future__ import annotations

import argparse

from .common import load_config


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="agileflex", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=None, help="path to config.yaml (default: repo root)")
    sub = parser.add_subparsers(dest="command", required=True)
    bt = sub.add_parser("backtest", aliases=["run"], help="back-test all strategies over the cached year")
    bt.add_argument("--no-forecast", action="store_true", help="skip the price-forecast study")
    pl = sub.add_parser("plan", help="build tonight's charging plan from tomorrow's Agile prices")
    pl.add_argument("--force", action="store_true", help="rebuild even if today's plan exists")
    fe = sub.add_parser("fetch", help="download a year of Agile prices and carbon intensity into data/")
    fe.add_argument("--refresh", action="store_true", help="re-download even if cached")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    if args.command in ("backtest", "run"):
        from .report import backtest
        backtest(cfg, forecast=not args.no_forecast)
    elif args.command == "plan":
        from .planner import make_plan
        make_plan(cfg, force=args.force)
    else:
        from .fetch import cache_year
        cache_year(cfg, refresh=args.refresh)


if __name__ == "__main__":
    main()

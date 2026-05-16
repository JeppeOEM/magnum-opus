"""CLI entry point for running backtests from the terminal.

Usage:
    python -m bot_service.backtest.cli \\
        --strategy OFIBot \\
        --symbol BTCUSDT \\
        --tf 1s \\
        --exchange bybit \\
        --start 2026-01-01 \\
        --end 2026-02-01 \\
        [--capital 10000] \\
        [--sample-every 60] \\
        [--strategies-dir strategies/active]

Results are printed as JSON to stdout. Exits 0 on success, 1 on error.
Fills are written to order_events (backtest=true) in QuestDB.
strategy_snapshots and backtest_runs are NOT written by the CLI — use the
REST API (POST /backtest/run) for full persistence.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from pathlib import Path


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="python -m bot_service.backtest.cli",
        description="Run a backtest on a strategy file.",
    )
    p.add_argument("--strategy", required=True, help="Strategy class name (file stem)")
    p.add_argument("--symbol", required=True, help="Trading symbol, e.g. BTCUSDT")
    p.add_argument("--tf", default="1s", help="Timeframe (1s, 1m, 15m, ...)")
    p.add_argument("--exchange", default="bybit", help="Exchange name")
    p.add_argument("--start", required=True, help="Start date ISO format, e.g. 2026-01-01")
    p.add_argument("--end", required=True, help="End date ISO format, e.g. 2026-02-01")
    p.add_argument("--capital", type=float, default=10_000.0, help="Initial capital USD")
    p.add_argument("--sample-every", type=int, default=60,
                   help="Equity curve sample interval (bars)")
    p.add_argument("--strategies-dir", default="strategies/active",
                   help="Directory containing strategy .py files")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    questdb_http_addr = os.environ.get("QUESTDB_HTTP_ADDR", "http://localhost:9000")
    questdb_ilp_addr = os.environ.get("QUESTDB_ILP_ADDR", "localhost:9009")

    strategies_dir = Path(args.strategies_dir)
    path = strategies_dir / f"{args.strategy}.py"
    if not path.exists():
        print(f"ERROR: strategy file not found: {path}", file=sys.stderr)
        return 1

    from bot_service.backtest.runner import run_backtest

    try:
        result = run_backtest(
            path=path,
            symbol=args.symbol,
            tf=args.tf,
            exchange=args.exchange,
            start_date=args.start,
            end_date=args.end,
            initial_capital=args.capital,
            questdb_http_addr=questdb_http_addr,
            questdb_ilp_addr=questdb_ilp_addr,
            sample_every=args.sample_every,
        )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    # Exclude equity_curve from CLI output (can be large); show summary only
    d = dataclasses.asdict(result)
    d["equity_curve_points"] = len(d.pop("equity_curve", []))
    print(json.dumps(d, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

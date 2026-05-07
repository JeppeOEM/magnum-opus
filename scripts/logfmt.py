#!/usr/bin/env python3
"""
logfmt.py — aggregates docker compose JSON logs into a clean terminal view.

Behaviour:
  - Build output and other service logs pass through unchanged.
  - Aggregator INFO (startup/status) prints immediately.
  - Aggregator WARN is counted and flushed as a summary once per second.
  - Aggregator ERROR prints immediately in full, then the summary resumes.
  - Every second: tick rate per exchange/symbol is fetched from /metrics.

Usage:
  docker compose up --build 2>&1 | python3 scripts/logfmt.py
  docker compose up --build 2>&1 | python3 scripts/logfmt.py --verbose
"""
import json
import re
import sys
import threading
import time
import urllib.request
import urllib.error
from collections import defaultdict
from datetime import datetime, timezone

# ── constants ──────────────────────────────────────────────────────────────────

_PREFIX = re.compile(r'^\S+-\d+\s+\|\s+')
_METRICS_URL = "http://localhost:8080/metrics"

_PASSTHROUGH_INFO = frozenset({
    "aggregator starting",
    "aggregator: kucoin enabled",
    "aggregator: bybit enabled",
    "aggregator: HTTP server started",
    "coordinator: book live",
    "aggregator: all feeds confirmed",
    "aggregator: startup gate failed",
    "aggregator: shutdown signal received",
    "aggregator: shutdown complete",
})

# ── shared state (guarded by _lock) ───────────────────────────────────────────

_lock = threading.Lock()
_warns: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
_pending_errors: list[dict] = []
_start_time = time.monotonic()

# previous tick counter values for computing per-second rate
_prev_ticks: dict[str, float] = {}   # "exchange/symbol" -> counter value

# ── metrics scrape ─────────────────────────────────────────────────────────────

def _scrape_ticks() -> dict[str, float]:
    """Fetch aggregator_ticks_total from /metrics. Returns {} on any error."""
    try:
        with urllib.request.urlopen(_METRICS_URL, timeout=0.5) as r:
            body = r.read().decode()
    except Exception:
        return {}

    counts: dict[str, float] = {}
    for line in body.splitlines():
        if not line.startswith("aggregator_ticks_total{"):
            continue
        # aggregator_ticks_total{exchange="kucoin",symbol="BTC-USDT"} 12345.0
        m = re.match(r'aggregator_ticks_total\{exchange="([^"]+)",symbol="([^"]+)"\}\s+([\d.]+)', line)
        if m:
            key = f"{m.group(1)}/{m.group(2)}"
            counts[key] = float(m.group(3))
    return counts

# ── terminal helpers ───────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")

def _println(msg: str):
    print(msg, flush=True)

# ── flush (called every second by background thread) ──────────────────────────

def _flush():
    global _prev_ticks

    with _lock:
        errs = _pending_errors[:]
        warn = {msg: dict(syms) for msg, syms in _warns.items()}
        _pending_errors.clear()
        _warns.clear()

    # Errors first — each one gets its own block
    for log in errs:
        ts     = log.get("time", "")[:19].replace("T", " ")
        msg    = log.get("msg", "")
        extras = [(k, v) for k, v in log.items() if k not in ("time", "level", "msg")]
        _println(f"[{ts}] \033[31m✗ ERROR\033[0m  {msg}")
        for k, v in extras:
            _println(f"          \033[2m{k}:\033[0m {v}")

    # Tick rate from Prometheus
    current = _scrape_ticks()
    tick_parts = []
    for key in sorted(current):
        prev  = _prev_ticks.get(key, current[key])
        rate  = max(0, current[key] - prev)
        exch, sym = key.split("/", 1)
        tick_parts.append(f"{sym} \033[36m{int(rate)}/s\033[0m")
    _prev_ticks = current

    # Warnings
    warn_total = sum(sum(s.values()) for s in warn.values())
    warn_parts = []
    for msg, syms in sorted(warn.items()):
        total = sum(syms.values())
        label = msg.replace("coordinator: ", "")
        sym_bits = "  ".join(
            f"{s.split('/')[-1]}×{n}"
            for s, n in sorted(syms.items()) if s
        )
        chunk = f"\033[33m⚠ {total} {label}\033[0m"
        if sym_bits:
            chunk += f"  [{sym_bits}]"
        warn_parts.append(chunk)

    uptime = int(time.monotonic() - _start_time)
    status = f"\033[32m✓ running\033[0m  uptime {uptime}s"

    parts = [status]
    if tick_parts:
        parts.append("  ".join(tick_parts))
    parts.extend(warn_parts)

    _println(f"[{_now()}] {'  |  '.join(parts)}")

# ── line processor ─────────────────────────────────────────────────────────────

def _process(raw: str):
    line = raw.rstrip("\n")
    m = _PREFIX.match(line)

    if not m:
        _println(line)
        return

    content = line[m.end():]
    try:
        log = json.loads(content)
    except Exception:
        _println(line)
        return

    level = log.get("level", "").upper()
    msg   = log.get("msg", "")

    if level == "ERROR":
        with _lock:
            _pending_errors.append(log)

    elif level == "WARN":
        exch = log.get("exchange", "")
        sym  = log.get("symbol", "")
        key  = f"{exch}/{sym}" if sym else exch
        with _lock:
            _warns[msg][key] += 1

    elif level == "INFO" and msg in _PASSTHROUGH_INFO:
        ts     = log.get("time", "")[:19].replace("T", " ")
        extras = " ".join(
            f"\033[2m{k}\033[0m={v}"
            for k, v in log.items()
            if k not in ("time", "level", "msg")
        )
        _println(f"[{ts}] \033[34mℹ\033[0m  {msg}  {extras}".rstrip())

# ── entry points ──────────────────────────────────────────────────────────────

def _flush_loop():
    while True:
        time.sleep(1.0)
        _flush()

def _run_aggregated():
    t = threading.Thread(target=_flush_loop, daemon=True)
    t.start()
    try:
        for line in sys.stdin:
            _process(line)
    except KeyboardInterrupt:
        pass

def _run_verbose():
    try:
        for line in sys.stdin:
            sys.stdout.write(line)
            sys.stdout.flush()
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    if "--verbose" in sys.argv or "-v" in sys.argv:
        _run_verbose()
    else:
        _run_aggregated()

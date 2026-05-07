#!/usr/bin/env python3
"""
logfmt.py — aggregates docker compose JSON logs into a clean terminal view.

Behaviour:
  - Build output and other service logs pass through unchanged.
  - Aggregator INFO (startup/status) prints immediately.
  - Aggregator WARN is counted and flushed as a summary once per second.
  - Aggregator ERROR prints immediately in full, then the summary resumes.
  - The status line overwrites itself in-place when there is nothing notable,
    so the terminal stays clean during healthy operation.

Usage:
  docker compose up --build 2>&1 | python3 scripts/logfmt.py
  docker compose up --build 2>&1 | python3 scripts/logfmt.py --verbose
"""
import json
import re
import sys
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone

# ── constants ──────────────────────────────────────────────────────────────────

# docker compose prefixes lines with "service-N  | "
_PREFIX = re.compile(r'^\S+-\d+\s+\|\s+')

# INFO messages worth printing immediately (not aggregated)
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
_status_active = False   # True when the current terminal line is a \r status line
_start_time = time.monotonic()

# ── terminal helpers ───────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")

def _clear_status():
    global _status_active
    if _status_active:
        print(flush=True)   # commit the \r line with a newline
        _status_active = False

def _overwrite_status(msg: str):
    global _status_active
    cols = 120
    padded = msg.ljust(cols)[:cols]
    print(f"\r{padded}", end="", flush=True)
    _status_active = True

def _println(msg: str):
    _clear_status()
    print(msg, flush=True)

# ── flush (called every second by background thread) ──────────────────────────

def _flush():
    with _lock:
        errs  = _pending_errors[:]
        warn  = {msg: dict(syms) for msg, syms in _warns.items()}
        _pending_errors.clear()
        _warns.clear()

    # Errors first — each one gets its own block
    for log in errs:
        ts  = log.get("time", "")[:19].replace("T", " ")
        msg = log.get("msg", "")
        extras = [(k, v) for k, v in log.items() if k not in ("time", "level", "msg")]
        _println(f"[{ts}] \033[31m✗ ERROR\033[0m  {msg}")
        for k, v in extras:
            _println(f"          \033[2m{k}:\033[0m {v}")

    warn_total = sum(sum(s.values()) for s in warn.values())

    if warn_total == 0:
        uptime = int(time.monotonic() - _start_time)
        _overwrite_status(f"[{_now()}] \033[32m✓ running\033[0m  uptime {uptime}s")
    else:
        parts = []
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
            parts.append(chunk)
        _println(f"[{_now()}] {' | '.join(parts)}")

# ── line processor ─────────────────────────────────────────────────────────────

def _process(raw: str):
    line = raw.rstrip("\n")
    m = _PREFIX.match(line)

    if not m:
        # Build output / non-service line: pass straight through
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

def _run_aggregated():
    t = threading.Thread(target=_flush_loop, daemon=True)
    t.start()
    try:
        for line in sys.stdin:
            _process(line)
    except KeyboardInterrupt:
        pass
    finally:
        _clear_status()

def _flush_loop():
    while True:
        time.sleep(1.0)
        _flush()

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

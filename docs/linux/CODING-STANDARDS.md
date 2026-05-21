# Coding Standards — magnum-opus

Rules followed by all code in this repository. Each rule has: the rule, the rationale,
and a counterexample showing what not to do.

---

## Go services (aggregator, candle-service, gateway)

### CS-G1: Clock injection — never call time.Now() in internal packages

**Rule:** All `internal/` packages receive a `Clock` interface (`Now() time.Time`).
Only `cmd/` entry points instantiate `realClock{}` and call `time.Now()` directly.

**Rationale:** Tests that depend on `time.Now()` are either slow (sleep-based timing)
or flaky under CI load. A `MockClock` advances deterministically.

**Enforcement:** `scripts/audit-docs.sh` check #1. `internal/tools/checkdeps/main.go` static analysis.

```go
// ✗ WRONG — in internal/gapdetector/gapdetector.go
func Detect(prev, next uint64, cause Cause) *GapEvent {
    return &GapEvent{Timestamp: time.Now()}  // banned
}

// ✓ CORRECT
func Detect(prev, next uint64, cause Cause, clock Clock) *GapEvent {
    return &GapEvent{Timestamp: clock.Now()}
}
```

---

### CS-G2: Credentials use the Credential type — never raw string

**Rule:** Any configuration field holding an API key, secret, passphrase, or password
must be of type `config.Credential`. Raw `string` type for credentials is prohibited.

**Rationale:** Raw strings are logged verbatim by `fmt.Sprintf` and `slog`. The `Credential`
type intercepts all formatting paths. See NOMICON §5.

**Enforcement:** `scripts/audit-docs.sh` check #3.

```go
// ✗ WRONG
type KuCoinConfig struct {
    APIKey string  // banned
}

// ✓ CORRECT
type KuCoinConfig struct {
    APIKey config.Credential
}
```

---

### CS-G3: No goroutines in internal/orderbook — pure state machine

**Rule:** `internal/orderbook` must not start goroutines, make network calls, or use
any synchronization primitives. It is a pure state machine with single-goroutine ownership.

**Rationale:** Goroutines in the orderbook would require locks, making tests complex
and introducing latency on every tick. The single-goroutine ownership model makes it
trivially deadlock-free and race-free.

**Enforcement:** Code review. No mutex or goroutine imports in the package.

---

### CS-G4: ILP writes are fire-and-forget — never block on QuestDB

**Rule:** QuestDB ILP writes in the coordinator and candle-service must not propagate
errors up the call stack. Errors are logged with `slog.Warn`; processing continues.

**Rationale:** Blocking on QuestDB creates backpressure that propagates to the exchange
WebSocket feed. A slow QuestDB should not cause tick loss. See MR-06.

```go
// ✗ WRONG
if err := writer.Write(tick); err != nil {
    return err  // propagates and stops tick processing
}

// ✓ CORRECT
if err := writer.Write(tick); err != nil {
    slog.Warn("coordinator: ilp.Write failed", "err", err, "exchange", exch, "sym", sym)
    // continues
}
```

---

### CS-G5: Use structured slog fields — no string interpolation in log messages

**Rule:** Log messages must be the constant string describing the event. Variable data
goes into separate key-value fields.

**Rationale:** Consistent log message strings enable reliable alerting and log queries.
`"coordinator: gap detected for kucoin BTC-USDT"` is unsearchable; `"coordinator: gap detected"
exchange=kucoin symbol=BTC-USDT` is filterable.

```go
// ✗ WRONG
slog.Warn(fmt.Sprintf("gap detected for %s %s", exch, sym))

// ✓ CORRECT
slog.Warn("coordinator: gap detected", "exchange", exch, "symbol", sym, "cause", cause)
```

---

### CS-G6: All public types in internal packages have doc comments

**Rule:** Every exported type, function, and method in `internal/` must have a Go doc
comment. Unexported fields and types may be undocumented if their purpose is obvious from context.

**Rationale:** `internal/` packages are consumed by other packages in the same repo.
Without doc comments, the caller must read the implementation to understand the contract.

```go
// ✗ WRONG
type Machine struct { ... }
func (m *Machine) Feed(seq uint64) { ... }

// ✓ CORRECT
// Machine is the reconnect/merge state machine for a single symbol.
type Machine struct { ... }

// Feed delivers a delta to the state machine while in Buffering state.
// If in StateLive, this is a no-op.
func (m *Machine) Feed(seq uint64) { ... }
```

---

### CS-G7: Use context.Context as the first parameter for all IO operations

**Rule:** Any function that makes a network call, Redis call, or QuestDB call must
accept `ctx context.Context` as its first parameter and pass it to all IO calls.

**Rationale:** Context propagation enables clean shutdown (context cancellation propagates
through all IO calls). Without context, goroutines may leak or block indefinitely on shutdown.

---

### CS-G8: Shutdown is idempotent via sync.Once

**Rule:** Any `Shutdown()` or `Close()` method on a long-running component must use
`sync.Once` to ensure it is safe to call multiple times.

**Rationale:** Signal handlers and error paths may trigger shutdown from multiple goroutines.
A double-close that panics or has side effects is worse than the original error.

---

## Python (bot-service)

### CS-P1: Use pydantic SecretStr for all credentials — never raw str

**Rule:** All credential configuration fields must be `pydantic.SecretStr`. The
`redact_credentials` structlog processor handles log-level redaction.

**Enforcement:** `bot_service/config.py` — all exchange credential fields are `SecretStr`.

```python
# ✗ WRONG
class Settings(BaseSettings):
    kucoin_api_key: str  # appears in logs

# ✓ CORRECT
class Settings(BaseSettings):
    kucoin_api_key: SecretStr  # redacted everywhere
```

---

### CS-P2: Strategy handlers must not block — use asyncio for IO

**Rule:** Methods called from the strategy event loop (`on_bar`, `handle_gap`) must
not block. Any IO must be awaited or scheduled via `asyncio.create_task`.

**Rationale:** Blocking the event loop prevents heartbeat ACKs, causing the heartbeat
watchdog to fire SIGTERM after 10 seconds.

```python
# ✗ WRONG
def on_bar(self, bar: BarClose) -> None:
    response = requests.get("http://...")  # blocks event loop

# ✓ CORRECT
def on_bar(self, bar: BarClose) -> None:
    asyncio.create_task(self._fetch_data())  # non-blocking
```

---

### CS-P3: add_indicators mutates df in-place — never reassign

**Rule:** `add_indicators(self, df: pd.DataFrame)` must mutate `df` in-place only.
Reassigning the local `df` variable (e.g., `df = df.dropna()`) has no effect on
`self._dfs` because Python passes DataFrames by reference to the object, not by copy.

```python
# ✗ WRONG — reassignment has no effect on self._dfs
def add_indicators(self, df: pd.DataFrame) -> None:
    df = df.dropna()  # local variable only; self._dfs[key] unchanged
    df.ta.rsi(length=14, append=True)

# ✓ CORRECT
def add_indicators(self, df: pd.DataFrame) -> None:
    df.ta.rsi(length=14, append=True)  # mutates in-place
```

---

### CS-P4: Gap-aware signal guards — check has_gap before acting

**Rule:** Strategy handlers must check `df.iloc[-1]["has_gap"]` (or `_signal_invalid`)
before emitting trading signals. Do not trade on bars that contain gap data.

```python
# ✓ CORRECT
def _handle_bar(self, df: pd.DataFrame) -> None:
    if self._signal_invalid.get(self._symbol, False):
        return  # BaseStrategy already handles this via register_bar_handler
    if df.iloc[-1]["has_gap"]:
        return  # extra guard for clarity
    # ... signal logic
```

---

### CS-P5: register_bar_handler for all signal computation — never on_bar directly

**Rule:** Use `register_bar_handler(symbol, tf, handler)` for all signal handlers.
Do not override `on_bar` directly for signal logic.

**Rationale:** `register_bar_handler` wraps the handler with the lookback gate and NaN guard.
Overriding `on_bar` directly bypasses these safety wrappers.

```python
# ✗ WRONG
def on_bar(self, bar: BarClose) -> None:
    # No NaN guard, no lookback gate
    self._place_order(...)

# ✓ CORRECT
def subscribe(self) -> None:
    self.register_bar_handler("BTCUSDT", "1s", self._handle_signal)

def _handle_signal(self, df: pd.DataFrame) -> None:
    # NaN guard and lookback gate already applied
    self._place_order(...)
```

---

### CS-P6: Use structlog — never print() or logging module

**Rule:** All log output must use `structlog.get_logger()`. The `logging` module and
`print()` bypass the `redact_credentials` processor and produce unstructured output.

```python
# ✗ WRONG
import logging
logging.warning("order placed: key=%s", api_key)  # may leak credentials

# ✓ CORRECT
import structlog
log = structlog.get_logger()
log.info("order_placed", strategy=self._name, symbol=symbol)
```

---

## Cross-language conventions

### CS-X1: Redis stream keys follow the naming pattern

All Redis stream keys must follow: `{type}:{exchange}:{symbol}` or `{type}:{exchange}:{symbol}:{tf}`

Types: `ticks`, `candles:close`, `candles:ob`, `alerts`, `funding`

```
ticks:kucoin:BTC-USDT
candles:close:bybit:BTCUSDT:1s
alerts:flush_failure
funding:bybit:BTCUSDT
```

**Enforcement:** `scripts/audit-docs.sh` check #5.

---

### CS-X2: String precision for prices and sizes

Prices and sizes are always passed as exact strings between services. Never use
`float64`/`float32`/`float` for serialization of financial values.

- Aggregator → Redis: `"29500.50"` (string)
- Redis → Candle-service: string parsed to float for arithmetic only
- QuestDB ILP: `DOUBLE` (float64) — precision loss is acceptable for analytics

**Rationale:** Float rounding on serialization creates phantom price levels in the order book.
A price of `29500.50` serialized as `float64` may become `29500.499999999...` on deserialization.

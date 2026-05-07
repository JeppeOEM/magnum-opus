-- raw_ticks: complete L2 delta audit trail written by the aggregator via ILP.
--
-- Field notes:
--   exchange    exchange name: "kucoin" | "bybit"
--   symbol      canonical symbol, e.g. "BTC-USDT"
--   seq         exchange sequence number (uint64 in Go, stored as LONG/int64; KuCoin and Bybit
--               sequence numbers are well within int64 range in practice — values above 2^63-1
--               would store as negative and must never occur)
--   ts_exchange timestamp of the event on the exchange (designated timestamp for partitioning)
--   ts_local    timestamp when the aggregator received the event (source: nanosecond clock,
--               written at millisecond resolution via ILP)
--   side        "bid" | "ask" — empty for trade events
--   price       exact wire string, e.g. "29500.50" — never stored as float to preserve precision
--   size        exact wire string — "0" means a level was removed (L2 updates only)
--   event_type  "update" (L2 delta) | "snapshot" (book initialisation) | "trade"
--   is_gap      true when this row represents a gap marker, not a real tick
--   gap_cause   non-null only when is_gap = true; one of four values:
--               "internal_buffer_overflow" | "internal_merge_error" |
--               "external_disconnect"       | "external_rate_limit"
--
-- Idempotent: IF NOT EXISTS ensures safe re-run on an existing deployment.
-- Applied via REST /exec on QuestDB port 9000 (not ILP port 9009).
CREATE TABLE IF NOT EXISTS raw_ticks (
    exchange    SYMBOL,
    symbol      SYMBOL,
    seq         LONG,
    ts_exchange TIMESTAMP,
    ts_local    TIMESTAMP,
    side        SYMBOL,
    price       STRING,
    size        STRING,
    event_type  SYMBOL,
    is_gap      BOOLEAN,
    gap_cause   SYMBOL
) TIMESTAMP(ts_exchange) PARTITION BY DAY WAL;

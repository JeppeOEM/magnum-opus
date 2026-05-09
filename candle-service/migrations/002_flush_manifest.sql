CREATE TABLE IF NOT EXISTS flush_manifest (
    ts_flush     TIMESTAMP,
    exchange     SYMBOL,
    date_flushed TIMESTAMP,
    row_count    LONG,
    b2_path      STRING,
    success      BOOLEAN,
    error_msg    STRING,
    duration_ms  LONG
) TIMESTAMP(ts_flush) PARTITION BY YEAR WAL

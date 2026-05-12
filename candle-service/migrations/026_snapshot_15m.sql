CREATE TABLE IF NOT EXISTS snapshot_15m (

    -- Identity (3)
    ts               TIMESTAMP,
    exchange         SYMBOL CAPACITY 8   INDEX,
    symbol           SYMBOL CAPACITY 256 INDEX,

    -- OHLCV (8)
    open             DOUBLE,
    high             DOUBLE,
    low              DOUBLE,
    close            DOUBLE,
    volume           DOUBLE,
    quote_volume     DOUBLE,
    trade_count      INT,
    twap             DOUBLE,

    -- Mid-price path (4)
    mid_price_open   DOUBLE,
    mid_price_high   DOUBLE,
    mid_price_low    DOUBLE,
    vwmp             DOUBLE,

    -- Spread (4)
    spread_high      DOUBLE,
    spread_low       DOUBLE,
    spread_mean      DOUBLE,
    effective_spread DOUBLE,

    -- OB best quotes open + close (4)
    best_bid_open    DOUBLE,
    best_ask_open    DOUBLE,
    best_bid         DOUBLE,
    best_ask         DOUBLE,

    -- OB depth at open (8)
    bid_depth_l1_open     DOUBLE,
    ask_depth_l1_open     DOUBLE,
    bid_depth_l2_open     DOUBLE,
    ask_depth_l2_open     DOUBLE,
    bid_depth_top10_open  DOUBLE,
    ask_depth_top10_open  DOUBLE,
    bid_depth_total_open  DOUBLE,
    ask_depth_total_open  DOUBLE,

    -- OB depth at close (8)
    bid_depth_l1_close    DOUBLE,
    ask_depth_l1_close    DOUBLE,
    bid_depth_l2_close    DOUBLE,
    ask_depth_l2_close    DOUBLE,
    bid_depth_top10_close DOUBLE,
    ask_depth_top10_close DOUBLE,
    bid_depth_total_close DOUBLE,
    ask_depth_total_close DOUBLE,

    -- Book shape (2)
    weighted_bid_price    DOUBLE,
    weighted_ask_price    DOUBLE,

    -- Market impact (2)
    depth_to_1pct_bid     DOUBLE,
    depth_to_1pct_ask     DOUBLE,

    -- Order flow imbalance (2)
    ofi                   DOUBLE,
    ofi_l1                DOUBLE,

    -- Trade flow (3)
    buy_volume            DOUBLE,
    sell_volume           DOUBLE,
    buy_count             INT,

    -- Block trades (2)
    block_buy_volume      DOUBLE,
    block_sell_volume     DOUBLE,

    -- Trade distribution (7)
    max_trade_size              DOUBLE,
    large_bid_orders            INT,
    large_ask_orders            INT,
    first_trade_offset_ms       INT,
    last_trade_offset_ms        INT,
    trade_clustering            DOUBLE,
    max_consecutive_run         INT,

    -- Volatility (4)
    realized_vol                DOUBLE,
    realized_skewness           DOUBLE,
    uptick_count                INT,
    downtick_count              INT,

    -- OB activity (10)
    bid_order_arrivals          INT,
    ask_order_arrivals          INT,
    bid_cancel_count            INT,
    ask_cancel_count            INT,
    ob_modify_count             INT,
    avg_bid_order_size          DOUBLE,
    avg_ask_order_size          DOUBLE,
    best_bid_changes            INT,
    best_ask_changes            INT,
    quote_stuff_ratio           DOUBLE,

    -- Trade microstructure (3)
    trade_sign_autocorr         DOUBLE,
    inter_trade_interval_std_ms DOUBLE,
    num_trade_price_levels      INT,

    -- Footprint (13)
    footprint_json              VARCHAR,
    poc_price                   DOUBLE,
    value_area_high             DOUBLE,
    value_area_low              DOUBLE,
    poc_volume                  DOUBLE,
    imbalance_buy_count         INT,
    imbalance_sell_count        INT,
    imbalance_stack_buy         INT,
    imbalance_stack_sell        INT,
    imbalance_ratio             DOUBLE,
    single_print_count          INT,
    single_print_levels_json    VARCHAR,
    unfinished_top              BOOLEAN,
    unfinished_bottom           BOOLEAN,
    absorption_detected         BOOLEAN,
    footprint_delta_divergence  BYTE,

    -- CVD (2)
    cum_delta                   DOUBLE,
    cvd_divergence              BYTE,

    -- Iceberg (3)
    iceberg_bid_detected        BOOLEAN,
    iceberg_ask_detected        BOOLEAN,
    iceberg_price               DOUBLE,

    -- Quality (3)
    is_partial                  BOOLEAN,
    gap_count                   INT,
    bar_count                   INT

) TIMESTAMP(ts) PARTITION BY DAY TTL 1825d WAL
DEDUP UPSERT KEYS(ts, exchange, symbol)

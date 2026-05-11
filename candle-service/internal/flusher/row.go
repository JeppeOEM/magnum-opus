package flusher

// Snapshot1sRow mirrors every column of the snapshot_1s QuestDB table.
// Column order matches the DDL exactly (used for CSV position parsing).
// Pointer types are used for nullable columns (DOUBLE, INT except quality fields).
// Non-pointer types are used for non-null columns: ts, exchange, symbol, is_partial, gap_count, bar_count.
type Snapshot1sRow struct {
	// Identity
	TS       int64  `parquet:"ts"`
	Exchange string `parquet:"exchange"`
	Symbol   string `parquet:"symbol"`

	// OHLCV
	Open        *float64 `parquet:"open"`
	High        *float64 `parquet:"high"`
	Low         *float64 `parquet:"low"`
	Close       *float64 `parquet:"close"`
	Volume      *float64 `parquet:"volume"`
	QuoteVolume *float64 `parquet:"quote_volume"`
	TradeCount  *int32   `parquet:"trade_count"`
	Twap        *float64 `parquet:"twap"`

	// Mid-price path
	MidPriceOpen *float64 `parquet:"mid_price_open"`
	MidPriceHigh *float64 `parquet:"mid_price_high"`
	MidPriceLow  *float64 `parquet:"mid_price_low"`
	Vwmp         *float64 `parquet:"vwmp"`

	// Spread
	SpreadHigh      *float64 `parquet:"spread_high"`
	SpreadLow       *float64 `parquet:"spread_low"`
	SpreadMean      *float64 `parquet:"spread_mean"`
	EffectiveSpread *float64 `parquet:"effective_spread"`

	// OB best quotes open + close
	BestBidOpen *float64 `parquet:"best_bid_open"`
	BestAskOpen *float64 `parquet:"best_ask_open"`
	BestBid     *float64 `parquet:"best_bid"`
	BestAsk     *float64 `parquet:"best_ask"`

	// OB depth at open
	BidDepthL1Open     *float64 `parquet:"bid_depth_l1_open"`
	AskDepthL1Open     *float64 `parquet:"ask_depth_l1_open"`
	BidDepthL2Open     *float64 `parquet:"bid_depth_l2_open"`
	AskDepthL2Open     *float64 `parquet:"ask_depth_l2_open"`
	BidDepthTop10Open  *float64 `parquet:"bid_depth_top10_open"`
	AskDepthTop10Open  *float64 `parquet:"ask_depth_top10_open"`
	BidDepthTotalOpen  *float64 `parquet:"bid_depth_total_open"`
	AskDepthTotalOpen  *float64 `parquet:"ask_depth_total_open"`

	// OB depth at close
	BidDepthL1Close     *float64 `parquet:"bid_depth_l1_close"`
	AskDepthL1Close     *float64 `parquet:"ask_depth_l1_close"`
	BidDepthL2Close     *float64 `parquet:"bid_depth_l2_close"`
	AskDepthL2Close     *float64 `parquet:"ask_depth_l2_close"`
	BidDepthTop10Close  *float64 `parquet:"bid_depth_top10_close"`
	AskDepthTop10Close  *float64 `parquet:"ask_depth_top10_close"`
	BidDepthTotalClose  *float64 `parquet:"bid_depth_total_close"`
	AskDepthTotalClose  *float64 `parquet:"ask_depth_total_close"`

	// Book shape
	WeightedBidPrice *float64 `parquet:"weighted_bid_price"`
	WeightedAskPrice *float64 `parquet:"weighted_ask_price"`

	// Market impact
	DepthTo1PctBid *float64 `parquet:"depth_to_1pct_bid"`
	DepthTo1PctAsk *float64 `parquet:"depth_to_1pct_ask"`

	// Order flow imbalance
	OFI   *float64 `parquet:"ofi"`
	OFIL1 *float64 `parquet:"ofi_l1"`

	// Trade flow
	BuyVolume     *float64 `parquet:"buy_volume"`
	SellVolume    *float64 `parquet:"sell_volume"`
	BuyCount      *int32   `parquet:"buy_count"`
	FootprintJSON *string  `parquet:"footprint_json"`

	// Value Area (Signal Group C)
	POCPrice      *float64 `parquet:"poc_price"`
	ValueAreaHigh *float64 `parquet:"value_area_high"`
	ValueAreaLow  *float64 `parquet:"value_area_low"`
	POCVolume     *float64 `parquet:"poc_volume"`

	// Imbalance Signals (Signal Group A)
	ImbalanceBuyCount  *int32   `parquet:"imbalance_buy_count"`
	ImbalanceSellCount *int32   `parquet:"imbalance_sell_count"`
	ImbalanceStackBuy  *int32   `parquet:"imbalance_stack_buy"`
	ImbalanceStackSell *int32   `parquet:"imbalance_stack_sell"`
	ImbalanceRatio     *float64 `parquet:"imbalance_ratio"`

	// Auction Signals (Signal Group B)
	SinglePrintCount      *int32  `parquet:"single_print_count"`
	SinglePrintLevelsJSON *string `parquet:"single_print_levels_json"`
	UnfinishedTop         *bool   `parquet:"unfinished_top"`
	UnfinishedBottom      *bool   `parquet:"unfinished_bottom"`
	AbsorptionDetected    *bool   `parquet:"absorption_detected"`

	// Block trades
	BlockBuyVolume  *float64 `parquet:"block_buy_volume"`
	BlockSellVolume *float64 `parquet:"block_sell_volume"`

	// Trade distribution
	MaxTradeSize          *float64 `parquet:"max_trade_size"`
	LargeBidOrders        *int32   `parquet:"large_bid_orders"`
	LargeAskOrders        *int32   `parquet:"large_ask_orders"`
	FirstTradeOffsetMs    *int32   `parquet:"first_trade_offset_ms"`
	LastTradeOffsetMs     *int32   `parquet:"last_trade_offset_ms"`
	TradeClustering       *float64 `parquet:"trade_clustering"`
	MaxConsecutiveRun     *int32   `parquet:"max_consecutive_run"`

	// Volatility
	RealizedVol      *float64 `parquet:"realized_vol"`
	RealizedSkewness *float64 `parquet:"realized_skewness"`
	UptickCount      *int32   `parquet:"uptick_count"`
	DowntickCount    *int32   `parquet:"downtick_count"`

	// OB activity
	BidOrderArrivals        *int32   `parquet:"bid_order_arrivals"`
	AskOrderArrivals        *int32   `parquet:"ask_order_arrivals"`
	BidCancelCount          *int32   `parquet:"bid_cancel_count"`
	AskCancelCount          *int32   `parquet:"ask_cancel_count"`
	OBModifyCount           *int32   `parquet:"ob_modify_count"`
	AvgBidOrderSize         *float64 `parquet:"avg_bid_order_size"`
	AvgAskOrderSize         *float64 `parquet:"avg_ask_order_size"`
	BestBidChanges          *int32   `parquet:"best_bid_changes"`
	BestAskChanges          *int32   `parquet:"best_ask_changes"`
	QuoteStuffRatio         *float64 `parquet:"quote_stuff_ratio"`

	// Trade microstructure
	TradeSignAutocorr        *float64 `parquet:"trade_sign_autocorr"`
	InterTradeIntervalStdMs  *float64 `parquet:"inter_trade_interval_std_ms"`
	NumTradePriceLevels      *int32   `parquet:"num_trade_price_levels"`

	// Quality (always written, non-pointer)
	IsPartial bool  `parquet:"is_partial"`
	GapCount  int32 `parquet:"gap_count"`
	BarCount  int32 `parquet:"bar_count"`
}

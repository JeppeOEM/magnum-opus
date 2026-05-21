ALTER TABLE snapshot_1s ADD COLUMN IF NOT EXISTS buy_vwap_deviation_bps  DOUBLE;
ALTER TABLE snapshot_1s ADD COLUMN IF NOT EXISTS sell_vwap_deviation_bps DOUBLE;

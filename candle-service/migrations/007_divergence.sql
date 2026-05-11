ALTER TABLE snapshot_1s ADD COLUMN IF NOT EXISTS footprint_delta_divergence BYTE;
ALTER TABLE snapshot_1s ADD COLUMN IF NOT EXISTS cum_delta DOUBLE;
ALTER TABLE snapshot_1s ADD COLUMN IF NOT EXISTS cvd_divergence BYTE;
ALTER TABLE snapshot_1s ADD COLUMN IF NOT EXISTS iceberg_bid_detected BOOLEAN;
ALTER TABLE snapshot_1s ADD COLUMN IF NOT EXISTS iceberg_ask_detected BOOLEAN;
ALTER TABLE snapshot_1s ADD COLUMN IF NOT EXISTS iceberg_price DOUBLE;

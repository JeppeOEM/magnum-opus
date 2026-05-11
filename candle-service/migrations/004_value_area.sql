-- 004_value_area.sql
-- Adds POC and Value Area columns to snapshot_1s.
ALTER TABLE snapshot_1s ADD COLUMN IF NOT EXISTS poc_price DOUBLE;
ALTER TABLE snapshot_1s ADD COLUMN IF NOT EXISTS value_area_high DOUBLE;
ALTER TABLE snapshot_1s ADD COLUMN IF NOT EXISTS value_area_low DOUBLE;
ALTER TABLE snapshot_1s ADD COLUMN IF NOT EXISTS poc_volume DOUBLE;

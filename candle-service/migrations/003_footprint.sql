-- 003_footprint.sql
-- Adds sell_volume and footprint_json columns to snapshot_1s.
ALTER TABLE snapshot_1s ADD COLUMN IF NOT EXISTS sell_volume DOUBLE;
ALTER TABLE snapshot_1s ADD COLUMN IF NOT EXISTS footprint_json VARCHAR;

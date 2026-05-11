-- 005_imbalance.sql
-- Adds imbalance signal columns to snapshot_1s.
ALTER TABLE snapshot_1s ADD COLUMN IF NOT EXISTS imbalance_buy_count INT;
ALTER TABLE snapshot_1s ADD COLUMN IF NOT EXISTS imbalance_sell_count INT;
ALTER TABLE snapshot_1s ADD COLUMN IF NOT EXISTS imbalance_stack_buy INT;
ALTER TABLE snapshot_1s ADD COLUMN IF NOT EXISTS imbalance_stack_sell INT;
ALTER TABLE snapshot_1s ADD COLUMN IF NOT EXISTS imbalance_ratio DOUBLE;

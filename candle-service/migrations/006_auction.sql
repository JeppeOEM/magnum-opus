ALTER TABLE snapshot_1s ADD COLUMN IF NOT EXISTS single_print_count INT;
ALTER TABLE snapshot_1s ADD COLUMN IF NOT EXISTS single_print_levels_json VARCHAR;
ALTER TABLE snapshot_1s ADD COLUMN IF NOT EXISTS unfinished_top BOOLEAN;
ALTER TABLE snapshot_1s ADD COLUMN IF NOT EXISTS unfinished_bottom BOOLEAN;
ALTER TABLE snapshot_1s ADD COLUMN IF NOT EXISTS absorption_detected BOOLEAN;

package migrator

import (
	"os"
	"strings"
	"testing"
)

func TestSnapshot1sDDL_RequiredClauses(t *testing.T) {
	data, err := os.ReadFile("../../migrations/001_snapshot_1s.sql")
	if err != nil {
		t.Fatalf("cannot read migrations/001_snapshot_1s.sql: %v", err)
	}
	content := string(data)

	required := []string{
		"CREATE TABLE IF NOT EXISTS snapshot_1s",
		"TIMESTAMP(ts)",
		"PARTITION BY DAY",
		"TTL 30d",
		"WAL",
		"DEDUP UPSERT KEYS(ts, exchange, symbol)",
		"SYMBOL CAPACITY 8",
		"SYMBOL CAPACITY 256",
	}
	for _, clause := range required {
		if !strings.Contains(content, clause) {
			t.Errorf("DDL missing required clause: %q", clause)
		}
	}
}

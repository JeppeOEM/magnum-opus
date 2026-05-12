package main

import (
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
)

// TestBarOpenMs_Truncation1m verifies the barOpenMs formula used in flushParallelTF
// for 1m bars: time.Unix(tsSecMs/1000, 0).UTC().Truncate(time.Minute).UnixMilli().
// tsSecMs is the 1s bar close time at which IsBarClose(tsSecMs, TF1m) fired.
func TestBarOpenMs_Truncation1m(t *testing.T) {
	cases := []struct {
		name    string
		tsSecMs int64 // 1s close time (ms) — last second of the 1m bar
		wantMs  int64 // expected barOpenMs (minute start, ms)
	}{
		{"exactly at 1m boundary (0:00:59→flush at 0:01:00)", 59_000, 0},
		{"second minute boundary (0:01:59→flush at 0:02:00)", 119_000, 60_000},
		{"15th minute (0:14:59→flush at 0:15:00)", 899_000, 840_000},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			got := time.Unix(c.tsSecMs/1000, 0).UTC().Truncate(time.Minute).UnixMilli()
			assert.Equal(t, c.wantMs, got, "barOpenMs for tsSecMs=%d", c.tsSecMs)
		})
	}
}

// TestBarOpenMs_Truncation15m verifies the barOpenMs formula for 15m bars:
// time.Unix(tsSecMs/1000, 0).UTC().Truncate(15 * time.Minute).UnixMilli().
func TestBarOpenMs_Truncation15m(t *testing.T) {
	cases := []struct {
		name    string
		tsSecMs int64
		wantMs  int64
	}{
		{"first 15m boundary (0:14:59→flush at 0:15:00)", 899_000, 0},
		{"second 15m boundary (0:29:59→flush at 0:30:00)", 1_799_000, 900_000},
		{"third 15m boundary (0:44:59→flush at 0:45:00)", 2_699_000, 1_800_000},
		{"hour boundary (0:59:59→flush at 1:00:00)", 3_599_000, 3_600_000 - 900_000},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			got := time.Unix(c.tsSecMs/1000, 0).UTC().Truncate(15 * time.Minute).UnixMilli()
			assert.Equal(t, c.wantMs, got, "barOpenMs for tsSecMs=%d", c.tsSecMs)
		})
	}
}

package flusher

import (
	"errors"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestNextFlushTime_BeforeFlushTime(t *testing.T) {
	// 02:30 UTC — before the 03:00 flush time → should return today 03:00
	now := time.Date(2026, 5, 7, 2, 30, 0, 0, time.UTC)
	got, err := nextFlushTime(now, "03:00")
	require.NoError(t, err)
	want := time.Date(2026, 5, 7, 3, 0, 0, 0, time.UTC)
	assert.Equal(t, want, got)
}

func TestNextFlushTime_AfterFlushTime(t *testing.T) {
	// 04:00 UTC — after the 03:00 flush time → should return tomorrow 03:00
	now := time.Date(2026, 5, 7, 4, 0, 0, 0, time.UTC)
	got, err := nextFlushTime(now, "03:00")
	require.NoError(t, err)
	want := time.Date(2026, 5, 8, 3, 0, 0, 0, time.UTC)
	assert.Equal(t, want, got)
}

func TestNextFlushTime_ExactlyAtFlushTime(t *testing.T) {
	// Exactly 03:00 UTC — not strictly after, so returns tomorrow 03:00
	now := time.Date(2026, 5, 7, 3, 0, 0, 0, time.UTC)
	got, err := nextFlushTime(now, "03:00")
	require.NoError(t, err)
	want := time.Date(2026, 5, 8, 3, 0, 0, 0, time.UTC)
	assert.Equal(t, want, got)
}

func TestNextFlushTime_InvalidFormat(t *testing.T) {
	now := time.Date(2026, 5, 7, 2, 0, 0, 0, time.UTC)
	_, err := nextFlushTime(now, "3pm")
	assert.Error(t, err)
}

func TestFlushDateRange_MissingThreeDays(t *testing.T) {
	// lastFlush = 2026-05-04, today = 2026-05-07 → missing [05, 06]
	last := time.Date(2026, 5, 4, 0, 0, 0, 0, time.UTC)
	today := time.Date(2026, 5, 7, 0, 0, 0, 0, time.UTC)
	got := flushDateRange(last, today)
	require.Len(t, got, 2)
	assert.Equal(t, time.Date(2026, 5, 5, 0, 0, 0, 0, time.UTC), got[0])
	assert.Equal(t, time.Date(2026, 5, 6, 0, 0, 0, 0, time.UTC), got[1])
}

func TestFlushDateRange_NothingMissing(t *testing.T) {
	// lastFlush = yesterday → nothing to catch up
	today := time.Date(2026, 5, 7, 0, 0, 0, 0, time.UTC)
	yesterday := today.AddDate(0, 0, -1)
	got := flushDateRange(yesterday, today)
	assert.Empty(t, got)
}

func TestFlushDateRange_ExcludesToday(t *testing.T) {
	// lastFlush = 2026-05-05, today = 2026-05-07 → only [06], not [07]
	last := time.Date(2026, 5, 5, 0, 0, 0, 0, time.UTC)
	today := time.Date(2026, 5, 7, 0, 0, 0, 0, time.UTC)
	got := flushDateRange(last, today)
	require.Len(t, got, 1)
	assert.Equal(t, time.Date(2026, 5, 6, 0, 0, 0, 0, time.UTC), got[0])
}

func TestB2PathFormat(t *testing.T) {
	date := time.Date(2026, 5, 7, 0, 0, 0, 0, time.UTC)
	got := b2Path(date)
	assert.Equal(t, "snapshot_1s/date=2026-05-07/data.parquet", got)
}

func TestB2PathFormat_DifferentDate(t *testing.T) {
	date := time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)
	got := b2Path(date)
	assert.Equal(t, "snapshot_1s/date=2026-01-01/data.parquet", got)
}

func TestParseFloat_Empty(t *testing.T) {
	assert.Nil(t, parseFloat(""))
}

func TestParseFloat_Value(t *testing.T) {
	got := parseFloat("60000.5")
	require.NotNil(t, got)
	assert.InDelta(t, 60000.5, *got, 1e-9)
}

func TestParseInt32_Empty(t *testing.T) {
	assert.Nil(t, parseInt32(""))
}

func TestParseInt32_Value(t *testing.T) {
	got := parseInt32("42")
	require.NotNil(t, got)
	assert.Equal(t, int32(42), *got)
}

// ── Story 9-3: alert field correctness and ordering ───────────────────────────

func TestAlertFieldsOnFailure(t *testing.T) {
	date := time.Date(2026, 5, 6, 0, 0, 0, 0, time.UTC)
	path := b2Path(date)
	flushErr := errors.New("upload timeout")
	nowMs := int64(1_746_489_600_000) // arbitrary fixed millisecond timestamp

	fields := buildAlertValues(date, path, flushErr, nowMs)

	assert.Equal(t, "2026-05-06", fields["date"])
	assert.Equal(t, "snapshot_1s/date=2026-05-06/data.parquet", fields["b2_path"])
	errMsg, okMsg := fields["error_msg"].(string)
	assert.True(t, okMsg, "error_msg must be a string")
	assert.Contains(t, errMsg, "upload timeout")
	ts, okTs := fields["ts"].(string)
	assert.True(t, okTs, "ts must be a string")
	assert.Equal(t, "1746489600000", ts)
}

func TestManifestBeforeAlert(t *testing.T) {
	var calls []string
	manifestFn := func() { calls = append(calls, "manifest") }
	alertFn := func() { calls = append(calls, "alert") }
	failureFn := func() { calls = append(calls, "failure") }

	runFailureSequence(manifestFn, alertFn, failureFn)

	require.Len(t, calls, 3)
	assert.Equal(t, "manifest", calls[0], "manifest write must precede alert publish")
	assert.Equal(t, "alert", calls[1], "alert must precede failure counter")
	assert.Equal(t, "failure", calls[2])
}

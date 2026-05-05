package reconnect_test

import (
	"math"
	"testing"

	"github.com/mrqdt/magnum-opus/aggregator/internal/reconnect"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// TestInitialState verifies the machine starts in StateInitial.
func TestInitialState(t *testing.T) {
	m := reconnect.New()
	assert.Equal(t, reconnect.StateInitial, m.State())
}

// TestColdStart covers the initial snapshot path (first connection, not a reconnect).
// Distinct scenario from reconnect-merge per AC.
func TestColdStart(t *testing.T) {
	m := reconnect.New()

	// Signal startup — machine should enter Buffering.
	sig := m.NeedsSnapshotNow("initial")
	require.NotNil(t, sig)
	assert.Equal(t, "initial", sig.Reason)
	assert.Equal(t, reconnect.StateBuffering, m.State())

	// Buffer a couple of deltas while snapshot is fetched.
	m.Feed(101)
	m.Feed(102)

	// Snapshot arrives at seq 100 — deltas 101, 102 are > 100 so they replay.
	replay, err := m.MergeSnapshot(100)
	require.Nil(t, err, "merge should succeed for cold start")
	assert.Equal(t, reconnect.StateLive, m.State())
	assert.Equal(t, []uint64{101, 102}, replay)
}

// TestReconnectMerge_SnapshotArrivesEarly: snapshot arrives before all deltas accumulated.
func TestReconnectMerge_SnapshotArrivesEarly(t *testing.T) {
	m := reconnect.New()
	m.NeedsSnapshotNow("disconnect")

	// Feed one delta, then snapshot arrives (snapshot-arrives-early).
	m.Feed(201)

	replay, err := m.MergeSnapshot(200)
	require.Nil(t, err)
	assert.Equal(t, reconnect.StateLive, m.State())
	assert.Equal(t, []uint64{201}, replay)
	assert.Equal(t, 0, m.BufferLen())
}

// TestReconnectMerge_SnapshotArrivesLate: snapshot arrives after additional deltas accumulated.
func TestReconnectMerge_SnapshotArrivesLate(t *testing.T) {
	m := reconnect.New()
	m.NeedsSnapshotNow("disconnect")

	// Many deltas accumulated before snapshot arrives (snapshot-arrives-late).
	for seq := uint64(301); seq <= 310; seq++ {
		m.Feed(seq)
	}

	// Snapshot at seq 305 — deltas 306..310 replay, 301..305 dropped (already in snapshot).
	replay, err := m.MergeSnapshot(305)
	require.Nil(t, err)
	assert.Equal(t, reconnect.StateLive, m.State())
	assert.Equal(t, []uint64{306, 307, 308, 309, 310}, replay)
}

// TestStaleSnapshot: snapshot seq has a gap before the oldest buffered delta — merge window missed.
// oldest=400, snapshot=398: next expected after snapshot is 399, but buffer starts at 400 → gap at 399.
func TestStaleSnapshot(t *testing.T) {
	m := reconnect.New()
	m.NeedsSnapshotNow("disconnect")

	m.Feed(400) // oldest buffered delta (buffer starts here)
	m.Feed(401)

	// Snapshot at 398: snapshot covers through 398, next expected is 399, buffer starts at 400 → gap.
	replay, err := m.MergeSnapshot(398)
	assert.Nil(t, replay)
	require.NotNil(t, err)
	assert.Equal(t, "merge_error", err.Reason)

	// Machine must still be Buffering after stale snapshot — caller retries.
	assert.Equal(t, reconnect.StateBuffering, m.State())
}

// TestDoubleNeedsSnapshot: second gap detected while first snapshot is in-flight.
// Buffer from first wait is preserved; new merge validates against new snapshot seq.
func TestDoubleNeedsSnapshot(t *testing.T) {
	m := reconnect.New()

	// First gap.
	sig1 := m.NeedsSnapshotNow("disconnect")
	require.NotNil(t, sig1)
	m.Feed(501)
	m.Feed(502)

	// Second gap before first snapshot arrives — double-NeedsSnapshot.
	sig2 := m.NeedsSnapshotNow("disconnect")
	require.NotNil(t, sig2)

	// Deltas from the first wait (501, 502) must still be in the buffer.
	// Add more deltas during the second wait.
	m.Feed(503)
	m.Feed(504)

	// New snapshot arrives at seq 502 (covers deltas 501..502).
	// Only 503, 504 should replay — not 501, 502.
	replay, err := m.MergeSnapshot(502)
	require.Nil(t, err)
	assert.Equal(t, reconnect.StateLive, m.State())
	assert.Equal(t, []uint64{503, 504}, replay)
}

// TestDoubleNeedsSnapshot_NewSnapshotSeqValidation: the stale-snapshot check after
// double-NeedsSnapshot uses the NEW snapshot's seq, not the old one.
// oldest=601, snapshot=599: gap at 600 → stale. Verifies the new snapshot seq is used.
func TestDoubleNeedsSnapshot_NewSnapshotSeqValidation(t *testing.T) {
	m := reconnect.New()

	// First gap — buffer deltas 601..603.
	m.NeedsSnapshotNow("disconnect")
	m.Feed(601)
	m.Feed(602)
	m.Feed(603)

	// Second gap.
	m.NeedsSnapshotNow("disconnect")
	m.Feed(604)

	// New snapshot at seq 599: next expected after snapshot is 600, but buffer starts at 601 → gap at 600.
	_, err := m.MergeSnapshot(599)
	require.NotNil(t, err, "snapshot seq 599 + 1 = 600 < oldest delta 601: must be stale")
	assert.Equal(t, "merge_error", err.Reason)
	assert.Equal(t, reconnect.StateBuffering, m.State())
}

// TestSequenceRollover: uint64 near max — rollover must not produce false gap.
func TestSequenceRollover(t *testing.T) {
	m := reconnect.New()
	m.NeedsSnapshotNow("initial")

	maxSeq := uint64(math.MaxUint64)

	// Buffer delta at max.
	m.Feed(maxSeq)

	// Snapshot at maxSeq-1 — delta maxSeq is > snapshot, so it replays.
	replay, err := m.MergeSnapshot(maxSeq - 1)
	require.Nil(t, err)
	assert.Equal(t, []uint64{maxSeq}, replay)
	assert.Equal(t, reconnect.StateLive, m.State())
}

// TestMergeSnapshot_NoBuffer: snapshot arrives when buffer is empty (immediate).
func TestMergeSnapshot_NoBuffer(t *testing.T) {
	m := reconnect.New()
	m.NeedsSnapshotNow("initial")

	// No deltas buffered — snapshot arrives immediately.
	replay, err := m.MergeSnapshot(1000)
	require.Nil(t, err)
	assert.Empty(t, replay)
	assert.Equal(t, reconnect.StateLive, m.State())
}

// TestFeed_LiveStateNoOp: Feed() is a no-op when in StateLive.
func TestFeed_LiveStateNoOp(t *testing.T) {
	m := reconnect.New()
	m.NeedsSnapshotNow("initial")
	m.MergeSnapshot(100)
	assert.Equal(t, reconnect.StateLive, m.State())

	m.Feed(200) // should not buffer anything
	assert.Equal(t, 0, m.BufferLen())
}

// TestGoLive_ClearsBuffer: GoLive clears buffer and sets Live.
func TestGoLive_ClearsBuffer(t *testing.T) {
	m := reconnect.New()
	m.NeedsSnapshotNow("initial")
	m.Feed(1)
	m.Feed(2)

	m.GoLive()
	assert.Equal(t, reconnect.StateLive, m.State())
	assert.Equal(t, 0, m.BufferLen())
}

// TestMergeSnapshot_AlreadyLive: MergeSnapshot is a no-op when already live.
func TestMergeSnapshot_AlreadyLive(t *testing.T) {
	m := reconnect.New()
	m.NeedsSnapshotNow("initial")
	m.MergeSnapshot(100)

	// Already live — second merge call must be no-op.
	replay, err := m.MergeSnapshot(200)
	assert.Nil(t, err)
	assert.Nil(t, replay)
	assert.Equal(t, reconnect.StateLive, m.State())
}

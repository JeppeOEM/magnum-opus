// Package reconnect implements a pure snapshot/delta merge state machine.
// No IO, no imports from other internal packages — testable at L1 with zero mocks.
//
// Lifecycle:
//
//	Initial → (NeedsSnapshot emitted) → Buffering → (snapshot arrives) → Live
//	Live → (gap detected) → (NeedsSnapshot emitted) → Buffering → …
package reconnect

import "math"

// State represents the current phase of the reconnect/merge cycle.
type State uint8

const (
	// StateInitial is the state before the first snapshot has been requested.
	StateInitial State = iota
	// StateBuffering means a NeedsSnapshot was emitted and deltas are being buffered.
	StateBuffering
	// StateLive means the book is up-to-date and deltas are applied directly.
	StateLive
)

// NeedsSnapshot is returned by the state machine when a new REST snapshot must be fetched.
type NeedsSnapshot struct {
	// Reason is "initial", "disconnect", or "merge_error" (stale snapshot).
	Reason string
}

// Result is returned by Feed() after processing a delta.
type Result struct {
	// Signal is non-nil when the caller must act (fetch a new snapshot).
	Signal *NeedsSnapshot
	// ReplayDeltas contains the deltas to apply after a successful merge.
	// Non-empty only on the transition from Buffering → Live.
	ReplayDeltas []uint64 // seq numbers of deltas to replay from the buffer
}

// Machine is the reconnect/merge state machine for a single symbol.
// Not safe for concurrent use — owned by a per-symbol goroutine.
type Machine struct {
	state  State
	buffer []bufferedDelta // deltas accumulated in Buffering state
}

type bufferedDelta struct {
	seq uint64
	// raw delta payload is held by the caller; we only track sequence numbers
	// for merge decisions. The caller replays deltas by seq after merge.
}

// New returns a new Machine in StateInitial.
func New() *Machine {
	return &Machine{state: StateInitial}
}

// State returns the current state.
func (m *Machine) State() State { return m.state }

// NeedsSnapshotNow signals the machine that the feed has started or a gap
// has occurred and a new snapshot must be fetched.
// Returns a NeedsSnapshot signal the caller must act on.
//
// If called while already Buffering (double-NeedsSnapshot), the existing buffer
// is preserved and a fresh NeedsSnapshot is returned. The new merge will
// validate against the NEW snapshot's seq, not the old one.
func (m *Machine) NeedsSnapshotNow(reason string) *NeedsSnapshot {
	// Always transition to Buffering, preserving any existing buffer so deltas
	// that arrived during the first wait remain available for the new merge.
	m.state = StateBuffering
	return &NeedsSnapshot{Reason: reason}
}

// Feed delivers a delta to the state machine while in Buffering state.
// If in StateLive, this is a no-op (caller applies deltas directly to the book).
// Returns nothing — buffered deltas are released only via MergeSnapshot.
func (m *Machine) Feed(seq uint64) {
	if m.state != StateBuffering {
		return
	}
	m.buffer = append(m.buffer, bufferedDelta{seq: seq})
}

// MergeSnapshot attempts to merge a REST snapshot with the buffered deltas.
//
// snapshotSeq is the sequence number of the received snapshot.
//
// Returns:
//   - (seqs, nil): seqs are the delta sequence numbers to replay (seq > snapshotSeq).
//     The machine transitions to StateLive.
//   - (nil, &NeedsSnapshot{Reason:"merge_error"}): the snapshot is stale —
//     its seq is below the oldest buffered delta, meaning the merge window was missed.
//     The machine stays in Buffering. Caller must fetch a new snapshot.
func (m *Machine) MergeSnapshot(snapshotSeq uint64) ([]uint64, *NeedsSnapshot) {
	if m.state != StateBuffering {
		// Already live — nothing to merge.
		return nil, nil
	}

	// Stale snapshot: there's a gap between the snapshot and the start of the buffer.
	// snapshot seq S covers messages up to S; the next expected message is S+1.
	// If the oldest buffered delta is > S+1, messages S+1 through oldest-1 are lost.
	// Guard: snapshotSeq == MaxUint64 causes snapshotSeq+1 to wrap to 0, which
	// would falsely classify every buffer entry as stale. Skip the check at max.
	if len(m.buffer) > 0 && snapshotSeq < math.MaxUint64 && m.buffer[0].seq > snapshotSeq+1 {
		// Stale: merge window missed. Keep buffer, return error signal.
		return nil, &NeedsSnapshot{Reason: "merge_error"}
	}

	// Collect deltas whose seq > snapshotSeq for replay.
	var replay []uint64
	for _, d := range m.buffer {
		if d.seq > snapshotSeq {
			replay = append(replay, d.seq)
		}
	}

	// Merge successful — clear buffer and go live.
	m.buffer = m.buffer[:0]
	m.state = StateLive
	return replay, nil
}

// GoLive forces the machine into StateLive without a merge (used for testing
// cold-start paths where the initial snapshot is applied directly).
func (m *Machine) GoLive() {
	m.buffer = m.buffer[:0]
	m.state = StateLive
}

// BufferLen returns the number of deltas currently buffered.
func (m *Machine) BufferLen() int { return len(m.buffer) }

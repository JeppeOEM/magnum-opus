package testutil

// CapturedWSSession records a sequence of exchange WebSocket messages
// for replay in L1 reconnect and merge tests. No IO — pure value data.
//
// Use NewCapturedWSSession to build fixtures with named scenarios, then
// feed the Deltas into reconnect.Machine.Feed() in your test.
type CapturedWSSession struct {
	// Name identifies this fixture in test output.
	Name string
	// Deltas holds the sequence numbers of messages that arrived over the feed,
	// in arrival order. The reconnect state machine only needs seq numbers for
	// merge decisions; raw payloads are stored by the caller.
	Deltas []uint64
	// SnapshotSeq is the sequence number of the REST snapshot fetched during this session.
	SnapshotSeq uint64
	// GapAt, if non-zero, is the seq number where the disconnect/gap was detected.
	GapAt uint64
}

// NewCapturedWSSession returns a session fixture with the given name.
func NewCapturedWSSession(name string) *CapturedWSSession {
	return &CapturedWSSession{Name: name}
}

// WithDeltas sets the delta sequence numbers for this session.
func (s *CapturedWSSession) WithDeltas(seqs ...uint64) *CapturedWSSession {
	s.Deltas = seqs
	return s
}

// WithSnapshot sets the REST snapshot sequence number for this session.
func (s *CapturedWSSession) WithSnapshot(seq uint64) *CapturedWSSession {
	s.SnapshotSeq = seq
	return s
}

// WithGapAt records the sequence number where a gap/disconnect occurred.
func (s *CapturedWSSession) WithGapAt(seq uint64) *CapturedWSSession {
	s.GapAt = seq
	return s
}

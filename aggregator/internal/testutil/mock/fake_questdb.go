//go:build l2

package mock

import (
	"context"
	"sync"
)

// FakeQuestDB simulates QuestDB ILP write behaviour for L2 tests.
// Supports WAL suspension simulation per the pre-mortem analysis in Story 3.3.
//
// Accepted-but-not-committed semantics mirror QuestDB's WAL behaviour:
// rows are accepted (Write succeeds) but not committed until a flush is issued.
// When WAL is suspended, Flush fails with errWALSuspended.
//
// Implements writer/questdb.ILPSender and writer/questdb.WALChecker via structural typing.
type FakeQuestDB struct {
	mu         sync.Mutex
	rows       []FakeRow
	walSuspend bool
	// SuspendAfter, if > 0, suspends WAL after this many committed rows.
	SuspendAfter int
	committed    int
}

// FakeRow represents one ILP write received by the fake.
type FakeRow struct {
	Table  string
	Fields map[string]interface{}
}

// NewFakeQuestDB returns an empty FakeQuestDB.
func NewFakeQuestDB() *FakeQuestDB { return &FakeQuestDB{} }

// Write accepts a row (always succeeds — WAL suspension affects commits, not accepts).
// Satisfies writer/questdb.ILPSender (structural typing — no import needed).
func (f *FakeQuestDB) Write(_ context.Context, table string, fields map[string]interface{}) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.rows = append(f.rows, FakeRow{Table: table, Fields: fields})
	return nil
}

// Flush commits buffered rows, unless WAL is suspended.
// Satisfies writer/questdb.ILPSender (structural typing — no import needed).
func (f *FakeQuestDB) Flush(_ context.Context) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.walSuspend {
		return errWALSuspended
	}
	f.committed += len(f.rows)
	if f.SuspendAfter > 0 && f.committed >= f.SuspendAfter {
		f.walSuspend = true
	}
	f.rows = f.rows[:0]
	return nil
}

// Close is a no-op for the fake.
// Satisfies writer/questdb.ILPSender (structural typing — no import needed).
func (f *FakeQuestDB) Close(_ context.Context) error { return nil }

// IsWALSuspended reports whether WAL suspension is active.
// Satisfies writer/questdb.WALChecker (structural typing — no import needed).
func (f *FakeQuestDB) IsWALSuspended(_ context.Context) (bool, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.walSuspend, nil
}

// ResumeWAL deactivates WAL suspension.
// Satisfies writer/questdb.WALChecker (structural typing — no import needed).
func (f *FakeQuestDB) ResumeWAL(_ context.Context) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.walSuspend = false
	return nil
}

// SuspendWAL activates WAL suspension — subsequent Flush calls fail.
// Test-helper only; not in any interface.
func (f *FakeQuestDB) SuspendWAL() {
	f.mu.Lock()
	f.walSuspend = true
	f.mu.Unlock()
}

// RowCount returns the number of uncommitted rows.
func (f *FakeQuestDB) RowCount() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return len(f.rows)
}

// CommittedCount returns the total number of committed rows.
func (f *FakeQuestDB) CommittedCount() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.committed
}

const errWALSuspended fakeError = "WAL suspended"

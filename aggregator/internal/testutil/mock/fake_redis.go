//go:build l2

// Package mock contains L2 test infrastructure: FakeRedis and FakeQuestDB.
// These files carry //go:build l2 and are never compiled in L1 test runs.
// Must not be imported from internal/testutil/ (which is L1-safe).
package mock

// FakeRedis is a partial Redis Streams fake for L2 tests.
// Supports XADD and XREAD simulation with optional partial-failure injection.
// Full implementation added in Story 3.2 when writer/redis is built.
type FakeRedis struct {
	streams map[string][]FakeEntry
	// FailAfter, if > 0, causes XADD to fail after this many successful calls.
	FailAfter int
	calls     int
}

// FakeEntry represents one entry in a fake stream.
type FakeEntry struct {
	ID     string
	Fields map[string]string
}

// NewFakeRedis returns an empty FakeRedis.
func NewFakeRedis() *FakeRedis {
	return &FakeRedis{streams: make(map[string][]FakeEntry)}
}

// XADD appends an entry to the named stream, respecting FailAfter.
func (f *FakeRedis) XADD(stream string, fields map[string]string) error {
	f.calls++
	if f.FailAfter > 0 && f.calls > f.FailAfter {
		return errFakeFailure
	}
	id := generateID(f.calls)
	f.streams[stream] = append(f.streams[stream], FakeEntry{ID: id, Fields: fields})
	return nil
}

// Len returns the number of entries in the named stream.
func (f *FakeRedis) Len(stream string) int { return len(f.streams[stream]) }

// Entries returns all entries in the named stream.
func (f *FakeRedis) Entries(stream string) []FakeEntry { return f.streams[stream] }

type fakeError string

func (e fakeError) Error() string { return string(e) }

const errFakeFailure fakeError = "fake redis failure"

func generateID(n int) string {
	return "0-" + itoa(n)
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	b := make([]byte, 0, 10)
	for n > 0 {
		b = append([]byte{byte('0' + n%10)}, b...)
		n /= 10
	}
	return string(b)
}

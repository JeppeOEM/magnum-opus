//go:build l2

// Package mock contains L2 test infrastructure: FakeRedis and FakeQuestDB.
// These files carry //go:build l2 and are never compiled in L1 test runs.
// Must not be imported from internal/testutil/ (which is L1-safe).
package mock

import (
	"context"
	"fmt"
	"sync"
)

// FakeRedis is a Redis Streams fake for L2 tests.
// Supports XAdd, XREADGROUP, XACK, and CreateGroup with optional partial-failure injection.
// Implements writer/redis.RedisClient via structural typing (no import required).
type FakeRedis struct {
	mu      sync.Mutex
	streams map[string][]FakeEntry
	// groups[stream][group] = last-delivered index (exclusive upper bound already read)
	groups map[string]map[string]int
	// FailFirst, if > 0, causes XAdd to fail for the first N calls.
	FailFirst int
	// FailAfter, if > 0, causes XAdd to fail after this many successful calls.
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
	return &FakeRedis{
		streams: make(map[string][]FakeEntry),
		groups:  make(map[string]map[string]int),
	}
}

// XAdd appends an entry to the named stream, respecting FailFirst and FailAfter.
// Satisfies writer/redis.RedisClient (structural typing — no import needed).
func (f *FakeRedis) XAdd(_ context.Context, stream string, fields map[string]any, _ int64) (string, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.calls++
	if f.FailFirst > 0 && f.calls <= f.FailFirst {
		return "", errFakeFailure
	}
	if f.FailAfter > 0 && f.calls > f.FailAfter {
		return "", errFakeFailure
	}
	id := generateID(f.calls)
	entry := FakeEntry{ID: id, Fields: make(map[string]string, len(fields))}
	for k, v := range fields {
		entry.Fields[k] = fmt.Sprint(v)
	}
	f.streams[stream] = append(f.streams[stream], entry)
	return id, nil
}

// CreateGroup registers a consumer group starting at the current end of the stream.
// Subsequent XREADGROUP calls for this group will only return entries written after CreateGroup.
func (f *FakeRedis) CreateGroup(stream, group string) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	if _, ok := f.groups[stream]; !ok {
		f.groups[stream] = make(map[string]int)
	}
	f.groups[stream][group] = len(f.streams[stream])
	return nil
}

// XREADGROUP returns all undelivered entries for the given group on the stream.
// Each call advances the group's read position — entries are not re-delivered.
// Returns a deep copy; callers may safely mutate the returned Fields maps.
func (f *FakeRedis) XREADGROUP(group, _ string, stream string) ([]FakeEntry, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if _, ok := f.groups[stream]; !ok {
		return nil, fmt.Errorf("NOGROUP: group %q does not exist on stream %q", group, stream)
	}
	pos := f.groups[stream][group]
	entries := f.streams[stream]
	if pos >= len(entries) {
		return nil, nil
	}
	batch := deepCopyEntries(entries[pos:])
	f.groups[stream][group] = len(entries)
	return batch, nil
}

// XACK acknowledges a message. Position is already advanced by XREADGROUP so this is a no-op.
func (f *FakeRedis) XACK(_, _, _ string) error { return nil }

// Len returns the number of entries in the named stream.
func (f *FakeRedis) Len(stream string) int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return len(f.streams[stream])
}

// Entries returns a deep copy of all entries in the named stream.
// Callers may safely mutate the returned FakeEntry.Fields maps.
func (f *FakeRedis) Entries(stream string) []FakeEntry {
	f.mu.Lock()
	defer f.mu.Unlock()
	return deepCopyEntries(f.streams[stream])
}

// ResetCalls resets the call counter without clearing streams — useful for multi-phase tests.
func (f *FakeRedis) ResetCalls() {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.calls = 0
}

// deepCopyEntries returns a deep copy of entries, including each entry's Fields map.
func deepCopyEntries(src []FakeEntry) []FakeEntry {
	if len(src) == 0 {
		return nil
	}
	dst := make([]FakeEntry, len(src))
	for i, e := range src {
		dst[i] = FakeEntry{ID: e.ID, Fields: make(map[string]string, len(e.Fields))}
		for k, v := range e.Fields {
			dst[i].Fields[k] = v
		}
	}
	return dst
}

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

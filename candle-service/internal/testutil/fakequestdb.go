//go:build l2

package testutil

import (
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
)

// FakeQuestDB is a minimal httptest server that simulates QuestDB REST /exec.
// It supports WAL suspension simulation for writer/questdb L2 tests.
type FakeQuestDB struct {
	srv       *httptest.Server
	mu        sync.Mutex
	queries   []string
	suspended bool
}

// NewFakeQuestDB creates and starts a FakeQuestDB server.
func NewFakeQuestDB() *FakeQuestDB {
	fq := &FakeQuestDB{}
	mux := http.NewServeMux()
	mux.HandleFunc("/exec", fq.handleExec)
	fq.srv = httptest.NewServer(mux)
	return fq
}

// Addr returns the HTTP address of the fake server (host:port).
func (fq *FakeQuestDB) Addr() string {
	return fq.srv.Listener.Addr().String()
}

// Close shuts down the server.
func (fq *FakeQuestDB) Close() {
	fq.srv.Close()
}

// SetSuspended controls whether wal_tables() reports snapshot_1s as suspended.
func (fq *FakeQuestDB) SetSuspended(v bool) {
	fq.mu.Lock()
	defer fq.mu.Unlock()
	fq.suspended = v
}

// Queries returns a copy of all /exec queries received so far.
func (fq *FakeQuestDB) Queries() []string {
	fq.mu.Lock()
	defer fq.mu.Unlock()
	out := make([]string, len(fq.queries))
	copy(out, fq.queries)
	return out
}

func (fq *FakeQuestDB) handleExec(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query().Get("query")
	fq.mu.Lock()
	fq.queries = append(fq.queries, q)
	susp := fq.suspended
	fq.mu.Unlock()

	switch {
	case strings.Contains(q, "wal_tables()"):
		fmt.Fprintf(w, `{"dataset":[["snapshot_1s",%v,"ACTIVE",null,null]]}`, susp)

	case strings.Contains(q, "RESUME WAL"):
		fq.SetSuspended(false)
		fmt.Fprint(w, `{"ddl":"OK"}`)

	default:
		w.WriteHeader(http.StatusOK)
		fmt.Fprint(w, `{}`)
	}
}

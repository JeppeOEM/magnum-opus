//go:build l2

package migrator

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func checksumBytes(data []byte) string {
	h := sha256.Sum256(data)
	return hex.EncodeToString(h[:])
}

func TestRun_FreshAppliesTwoMigrations(t *testing.T) {
	dir := t.TempDir()
	require.NoError(t, os.WriteFile(filepath.Join(dir, "001_first.sql"), []byte("CREATE TABLE a (id LONG)"), 0644))
	require.NoError(t, os.WriteFile(filepath.Join(dir, "002_second.sql"), []byte("CREATE TABLE b (id LONG)"), 0644))

	var mu sync.Mutex
	applied := []string{}

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		q := r.URL.Query().Get("query")
		switch {
		case strings.Contains(q, "CREATE TABLE IF NOT EXISTS schema_migrations"):
			w.WriteHeader(http.StatusOK)
			fmt.Fprint(w, `{"ddl":"OK"}`)
		case strings.Contains(q, "SELECT version, checksum FROM schema_migrations"):
			w.WriteHeader(http.StatusOK)
			fmt.Fprint(w, `{"dataset":[]}`)
		case strings.Contains(q, "CREATE TABLE a") || strings.Contains(q, "CREATE TABLE b"):
			mu.Lock()
			applied = append(applied, q)
			mu.Unlock()
			w.WriteHeader(http.StatusOK)
			fmt.Fprint(w, `{"ddl":"OK"}`)
		case strings.Contains(q, "INSERT INTO schema_migrations"):
			w.WriteHeader(http.StatusOK)
			fmt.Fprint(w, `{"ddl":"OK"}`)
		default:
			w.WriteHeader(http.StatusOK)
			fmt.Fprint(w, `{"ddl":"OK"}`)
		}
	}))
	defer srv.Close()

	m := New(&http.Client{}, srv.Listener.Addr().String(), dir)
	require.NoError(t, m.Run(t.Context()))

	mu.Lock()
	defer mu.Unlock()
	assert.Len(t, applied, 2, "expected both migration SQL statements to be executed")
}

func TestRun_IdempotentRerun(t *testing.T) {
	dir := t.TempDir()
	content1 := []byte("CREATE TABLE a (id LONG)")
	content2 := []byte("CREATE TABLE b (id LONG)")
	require.NoError(t, os.WriteFile(filepath.Join(dir, "001_first.sql"), content1, 0644))
	require.NoError(t, os.WriteFile(filepath.Join(dir, "002_second.sql"), content2, 0644))

	sum1 := checksumBytes(content1)
	sum2 := checksumBytes(content2)

	var mu sync.Mutex
	migrationExecCount := 0

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		q := r.URL.Query().Get("query")
		switch {
		case strings.Contains(q, "CREATE TABLE IF NOT EXISTS schema_migrations"):
			w.WriteHeader(http.StatusOK)
			fmt.Fprint(w, `{"ddl":"OK"}`)
		case strings.Contains(q, "SELECT version, checksum FROM schema_migrations"):
			w.WriteHeader(http.StatusOK)
			resp := map[string]interface{}{
				"dataset": [][]interface{}{
					{float64(1), sum1},
					{float64(2), sum2},
				},
			}
			json.NewEncoder(w).Encode(resp)
		case strings.Contains(q, "CREATE TABLE a") || strings.Contains(q, "CREATE TABLE b"):
			mu.Lock()
			migrationExecCount++
			mu.Unlock()
			w.WriteHeader(http.StatusOK)
			fmt.Fprint(w, `{"ddl":"OK"}`)
		default:
			w.WriteHeader(http.StatusOK)
			fmt.Fprint(w, `{"ddl":"OK"}`)
		}
	}))
	defer srv.Close()

	m := New(&http.Client{}, srv.Listener.Addr().String(), dir)
	require.NoError(t, m.Run(t.Context()))

	mu.Lock()
	defer mu.Unlock()
	assert.Equal(t, 0, migrationExecCount, "no migration SQL should execute on idempotent rerun")
}

func TestRun_ChecksumMismatch(t *testing.T) {
	dir := t.TempDir()
	require.NoError(t, os.WriteFile(filepath.Join(dir, "001_first.sql"), []byte("CREATE TABLE a (id LONG)"), 0644))

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		q := r.URL.Query().Get("query")
		switch {
		case strings.Contains(q, "CREATE TABLE IF NOT EXISTS schema_migrations"):
			w.WriteHeader(http.StatusOK)
			fmt.Fprint(w, `{"ddl":"OK"}`)
		case strings.Contains(q, "SELECT version, checksum FROM schema_migrations"):
			w.WriteHeader(http.StatusOK)
			resp := map[string]interface{}{
				"dataset": [][]interface{}{
					{float64(1), "0000000000000000000000000000000000000000000000000000000000000000"},
				},
			}
			json.NewEncoder(w).Encode(resp)
		default:
			w.WriteHeader(http.StatusOK)
			fmt.Fprint(w, `{"ddl":"OK"}`)
		}
	}))
	defer srv.Close()

	m := New(&http.Client{}, srv.Listener.Addr().String(), dir)
	err := m.Run(t.Context())
	require.Error(t, err)
	assert.Contains(t, err.Error(), "checksum mismatch")
	assert.Contains(t, err.Error(), "001_first.sql")
}

func TestRun_QuestDBHTTPError(t *testing.T) {
	dir := t.TempDir()
	require.NoError(t, os.WriteFile(filepath.Join(dir, "001_first.sql"), []byte("CREATE TABLE a (id LONG)"), 0644))

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
		fmt.Fprint(w, `{"error":"internal server error"}`)
	}))
	defer srv.Close()

	m := New(&http.Client{}, srv.Listener.Addr().String(), dir)
	err := m.Run(t.Context())
	assert.Error(t, err)
}

func TestRun_ContextCancellation(t *testing.T) {
	dir := t.TempDir()
	require.NoError(t, os.WriteFile(filepath.Join(dir, "001_first.sql"), []byte("CREATE TABLE a (id LONG)"), 0644))

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		fmt.Fprint(w, `{"ddl":"OK"}`)
	}))
	defer srv.Close()

	ctx, cancel := context.WithCancel(context.Background())
	cancel() // cancel before Run is called

	m := New(&http.Client{}, srv.Listener.Addr().String(), dir)
	err := m.Run(ctx)
	assert.ErrorIs(t, err, context.Canceled)
}

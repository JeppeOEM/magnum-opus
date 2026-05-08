package migrator

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestFileChecksum_KnownValue(t *testing.T) {
	content := []byte("CREATE TABLE test (id LONG)")
	dir := t.TempDir()
	f := filepath.Join(dir, "test.sql")
	require.NoError(t, os.WriteFile(f, content, 0644))

	m := New(&http.Client{}, "localhost:9000", dir)
	got, err := m.fileChecksum(f)
	require.NoError(t, err)

	h := sha256.Sum256(content)
	want := hex.EncodeToString(h[:])
	assert.Equal(t, want, got)
}

func TestDiscoverFiles_SortedAscending(t *testing.T) {
	dir := t.TempDir()
	// Non-padded filenames prove integer sort, not lexicographic:
	// lexicographically "10_c" < "2_b" < "9_a" — integer order is 2, 9, 10.
	for _, name := range []string{"9_a.sql", "2_b.sql", "10_c.sql"} {
		require.NoError(t, os.WriteFile(filepath.Join(dir, name), []byte("SELECT 1"), 0644))
	}

	m := New(&http.Client{}, "localhost:9000", dir)
	files, err := m.discoverFiles()
	require.NoError(t, err)
	require.Len(t, files, 3)
	assert.Equal(t, int64(2), files[0].version)
	assert.Equal(t, int64(9), files[1].version)
	assert.Equal(t, int64(10), files[2].version)
}

func TestDiscoverFiles_SkipsNonSQL(t *testing.T) {
	dir := t.TempDir()
	require.NoError(t, os.WriteFile(filepath.Join(dir, "001_a.sql"), []byte("SELECT 1"), 0644))
	require.NoError(t, os.WriteFile(filepath.Join(dir, "002_b.txt"), []byte("ignore"), 0644))
	require.NoError(t, os.WriteFile(filepath.Join(dir, "003_c.go"), []byte("package x"), 0644))

	m := New(&http.Client{}, "localhost:9000", dir)
	files, err := m.discoverFiles()
	require.NoError(t, err)
	require.Len(t, files, 1)
	assert.Equal(t, int64(1), files[0].version)
}

func TestDiscoverFiles_BadFilename(t *testing.T) {
	dir := t.TempDir()
	require.NoError(t, os.WriteFile(filepath.Join(dir, "abc_bad.sql"), []byte("SELECT 1"), 0644))

	m := New(&http.Client{}, "localhost:9000", dir)
	_, err := m.discoverFiles()
	assert.Error(t, err)
}

func TestDiscoverFiles_DuplicateVersion(t *testing.T) {
	dir := t.TempDir()
	require.NoError(t, os.WriteFile(filepath.Join(dir, "001_first.sql"), []byte("SELECT 1"), 0644))
	require.NoError(t, os.WriteFile(filepath.Join(dir, "001_second.sql"), []byte("SELECT 2"), 0644))

	m := New(&http.Client{}, "localhost:9000", dir)
	_, err := m.discoverFiles()
	require.Error(t, err)
	assert.Contains(t, err.Error(), "duplicate")
}

func TestDiscoverFiles_VersionZeroRejected(t *testing.T) {
	dir := t.TempDir()
	require.NoError(t, os.WriteFile(filepath.Join(dir, "0_init.sql"), []byte("SELECT 1"), 0644))

	m := New(&http.Client{}, "localhost:9000", dir)
	_, err := m.discoverFiles()
	require.Error(t, err)
	assert.Contains(t, err.Error(), "version must be > 0")
}

func TestDiscoverFiles_UnsafeCharacterRejected(t *testing.T) {
	dir := t.TempDir()
	// apostrophe in name would inject SQL
	require.NoError(t, os.WriteFile(filepath.Join(dir, "001_o'brien.sql"), []byte("SELECT 1"), 0644))

	m := New(&http.Client{}, "localhost:9000", dir)
	_, err := m.discoverFiles()
	require.Error(t, err)
	assert.Contains(t, err.Error(), "unsafe character")
}

func TestChecksumMismatch_ReturnsError(t *testing.T) {
	dir := t.TempDir()
	sqlFile := filepath.Join(dir, "001_snapshot_1s.sql")
	require.NoError(t, os.WriteFile(sqlFile, []byte("CREATE TABLE snap (id LONG)"), 0644))

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
	assert.Contains(t, err.Error(), "001_snapshot_1s.sql")
}

func TestRun_EmptyMigrationsDir(t *testing.T) {
	dir := t.TempDir() // no SQL files

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		q := r.URL.Query().Get("query")
		if strings.Contains(q, "SELECT version, checksum") {
			w.WriteHeader(http.StatusOK)
			fmt.Fprint(w, `{"dataset":[]}`)
		} else {
			w.WriteHeader(http.StatusOK)
			fmt.Fprint(w, `{"ddl":"OK"}`)
		}
	}))
	defer srv.Close()

	m := New(&http.Client{}, srv.Listener.Addr().String(), dir)
	assert.NoError(t, m.Run(t.Context()))
}

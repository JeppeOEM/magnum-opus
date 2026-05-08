// Package migrator applies QuestDB schema migrations on service startup.
// Each migration file (NNN_description.sql) is applied exactly once; a
// SHA-256 checksum guards against post-apply file modification.
// QuestDB REST does NOT accept multiple semicolon-separated statements in
// one request — each migration file must contain exactly one DDL statement.
package migrator

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
)

type migrationFile struct {
	version  int64
	name     string // filename without .sql extension, e.g. "001_snapshot_1s"
	filename string // full filename, e.g. "001_snapshot_1s.sql"
	path     string // absolute path
}

// Migrator applies SQL migration files to a QuestDB instance via HTTP REST.
type Migrator struct {
	client   *http.Client
	httpAddr string
	migsDir  string
}

// New constructs a Migrator. client should have a reasonable Timeout set.
func New(client *http.Client, httpAddr, migsDir string) *Migrator {
	return &Migrator{
		client:   client,
		httpAddr: httpAddr,
		migsDir:  migsDir,
	}
}

// Run applies all pending migrations in ascending version order.
// Two-pass design per AC5: all stored checksums are validated before any
// new migration is applied. Returns ctx.Err() on cancellation, a descriptive
// error on checksum mismatch, or an HTTP/QuestDB error on query failure.
func (m *Migrator) Run(ctx context.Context) error {
	if err := ctx.Err(); err != nil {
		return err
	}

	if err := m.ensureSchemaTable(ctx); err != nil {
		return err
	}

	applied, err := m.loadApplied(ctx)
	if err != nil {
		return err
	}

	files, err := m.discoverFiles()
	if err != nil {
		return err
	}

	// Pass 1: validate checksums of all previously-applied migrations.
	// No new migrations are applied if any mismatch is detected.
	for _, mf := range files {
		stored, ok := applied[mf.version]
		if !ok {
			continue
		}
		computed, err := m.fileChecksum(mf.path)
		if err != nil {
			return fmt.Errorf("migrator: checksum %s: %w", mf.filename, err)
		}
		if stored != computed {
			return fmt.Errorf(
				"migrator: checksum mismatch for migration %d (%s): stored=%s, computed=%s — file may have been modified after apply",
				mf.version, mf.filename, stored, computed,
			)
		}
	}

	// Pass 2: apply new (unapplied) migrations in version order.
	for _, mf := range files {
		if _, ok := applied[mf.version]; ok {
			continue
		}
		content, err := os.ReadFile(mf.path)
		if err != nil {
			return fmt.Errorf("migrator: read %s: %w", mf.filename, err)
		}
		h := sha256.Sum256(content)
		checksum := hex.EncodeToString(h[:])
		if err := m.applyOne(ctx, mf, content, checksum); err != nil {
			return err
		}
	}
	return nil
}

// exec sends a single SQL statement to QuestDB REST and returns nil on HTTP 200.
func (m *Migrator) exec(ctx context.Context, query string) error {
	u := fmt.Sprintf("http://%s/exec?query=%s", m.httpAddr, url.QueryEscape(query))
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, u, nil)
	if err != nil {
		return fmt.Errorf("migrator: build request: %w", err)
	}
	resp, err := m.client.Do(req)
	if err != nil {
		return fmt.Errorf("migrator: exec: %w", err)
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return fmt.Errorf("migrator: exec: read response: %w", err)
	}
	if resp.StatusCode != http.StatusOK {
		var e struct {
			Error string `json:"error"`
		}
		if json.Unmarshal(body, &e) == nil && e.Error != "" {
			return fmt.Errorf("migrator: exec: questdb error: %s", e.Error)
		}
		return fmt.Errorf("migrator: exec: HTTP %d", resp.StatusCode)
	}
	return nil
}

// queryRows sends a SELECT and returns the dataset rows.
func (m *Migrator) queryRows(ctx context.Context, query string) ([][]interface{}, error) {
	u := fmt.Sprintf("http://%s/exec?query=%s", m.httpAddr, url.QueryEscape(query))
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, u, nil)
	if err != nil {
		return nil, fmt.Errorf("migrator: build request: %w", err)
	}
	resp, err := m.client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("migrator: queryRows: %w", err)
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("migrator: queryRows: read response: %w", err)
	}
	if resp.StatusCode != http.StatusOK {
		var e struct {
			Error string `json:"error"`
		}
		if json.Unmarshal(body, &e) == nil && e.Error != "" {
			return nil, fmt.Errorf("migrator: queryRows: questdb error: %s", e.Error)
		}
		return nil, fmt.Errorf("migrator: queryRows: HTTP %d", resp.StatusCode)
	}
	var result struct {
		Dataset [][]interface{} `json:"dataset"`
	}
	if err := json.Unmarshal(body, &result); err != nil {
		return nil, fmt.Errorf("migrator: queryRows: parse response: %w", err)
	}
	return result.Dataset, nil
}

func (m *Migrator) ensureSchemaTable(ctx context.Context) error {
	ddl := `CREATE TABLE IF NOT EXISTS schema_migrations (
    version LONG,
    name STRING,
    applied_at TIMESTAMP,
    checksum STRING
) TIMESTAMP(applied_at) PARTITION BY YEAR WAL`
	if err := m.exec(ctx, ddl); err != nil {
		return fmt.Errorf("migrator: ensure schema table: %w", err)
	}
	return nil
}

// loadApplied returns a map of version → stored checksum for all applied migrations.
func (m *Migrator) loadApplied(ctx context.Context) (map[int64]string, error) {
	rows, err := m.queryRows(ctx, "SELECT version, checksum FROM schema_migrations")
	if err != nil {
		return nil, fmt.Errorf("migrator: load applied: %w", err)
	}
	result := make(map[int64]string, len(rows))
	for i, row := range rows {
		if len(row) < 2 {
			return nil, fmt.Errorf("migrator: loadApplied: row %d has %d columns, expected 2", i, len(row))
		}
		v, ok := row[0].(float64)
		if !ok {
			return nil, fmt.Errorf("migrator: loadApplied: row %d: unexpected type for version: %T", i, row[0])
		}
		chk, ok := row[1].(string)
		if !ok {
			return nil, fmt.Errorf("migrator: loadApplied: row %d: unexpected type for checksum: %T", i, row[1])
		}
		result[int64(v)] = chk
	}
	return result, nil
}

// discoverFiles reads migsDir, filters *.sql files, parses versions, and
// returns them sorted by version ascending (integer sort, not lexicographic).
// Returns an error if any filename is invalid, uses version ≤ 0, contains
// unsafe characters, or if duplicate version numbers are found.
func (m *Migrator) discoverFiles() ([]migrationFile, error) {
	entries, err := os.ReadDir(m.migsDir)
	if err != nil {
		return nil, fmt.Errorf("migrator: discover files: %w", err)
	}
	var files []migrationFile
	for _, e := range entries {
		if e.IsDir() {
			continue
		}
		fname := e.Name()
		if !strings.HasSuffix(fname, ".sql") {
			continue
		}
		parts := strings.SplitN(fname, "_", 2)
		if len(parts) < 2 {
			return nil, fmt.Errorf("migrator: invalid migration filename %q: must start with NNN_", fname)
		}
		ver, err := strconv.ParseInt(parts[0], 10, 64)
		if err != nil {
			return nil, fmt.Errorf("migrator: invalid migration filename %q: leading segment %q is not an integer", fname, parts[0])
		}
		if ver <= 0 {
			return nil, fmt.Errorf("migrator: invalid migration filename %q: version must be > 0, got %d", fname, ver)
		}
		name := strings.TrimSuffix(fname, ".sql")
		if err := validateMigrationName(name); err != nil {
			return nil, fmt.Errorf("migrator: invalid migration filename %q: %w", fname, err)
		}
		files = append(files, migrationFile{
			version:  ver,
			name:     name,
			filename: fname,
			path:     filepath.Join(m.migsDir, fname),
		})
	}
	sort.Slice(files, func(i, j int) bool {
		return files[i].version < files[j].version
	})
	for i := 1; i < len(files); i++ {
		if files[i].version == files[i-1].version {
			return nil, fmt.Errorf("migrator: duplicate migration version %d: %q and %q",
				files[i].version, files[i-1].filename, files[i].filename)
		}
	}
	return files, nil
}

// validateMigrationName rejects names containing characters that are unsafe in
// a SQL literal (single quotes, semicolons, etc.). Only [A-Za-z0-9_-] allowed.
func validateMigrationName(name string) error {
	for _, c := range name {
		if !((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '_' || c == '-') {
			return fmt.Errorf("unsafe character %q — only [A-Za-z0-9_-] allowed", c)
		}
	}
	return nil
}

// fileChecksum computes the hex-encoded SHA-256 of the file at path.
func (m *Migrator) fileChecksum(path string) (string, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return "", fmt.Errorf("migrator: read file: %w", err)
	}
	h := sha256.Sum256(data)
	return hex.EncodeToString(h[:]), nil
}

// applyOne executes a migration's SQL content and records it in schema_migrations.
// content is passed in (already read by Run) to avoid reading the file twice.
func (m *Migrator) applyOne(ctx context.Context, mf migrationFile, content []byte, checksum string) error {
	if err := m.exec(ctx, string(content)); err != nil {
		return fmt.Errorf("migrator: apply %s: %w", mf.filename, err)
	}
	insert := fmt.Sprintf(
		"INSERT INTO schema_migrations VALUES (%d, '%s', systimestamp(), '%s')",
		mf.version, mf.name, checksum,
	)
	if err := m.exec(ctx, insert); err != nil {
		return fmt.Errorf("migrator: record %s: %w", mf.filename, err)
	}
	return nil
}

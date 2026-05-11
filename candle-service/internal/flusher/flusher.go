// Package flusher exports daily snapshot_1s data to Backblaze B2 as zstd-compressed Parquet.
// Catch-up on startup ensures no days are silently skipped when Redis state is lost.
// AbortMultipartUpload on upload failure always uses a fresh context (not the cancelled one).
package flusher

import (
	"bytes"
	"context"
	"encoding/csv"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/service/s3"
	s3types "github.com/aws/aws-sdk-go-v2/service/s3/types"
	"github.com/redis/go-redis/v9"

	parquet "github.com/parquet-go/parquet-go"
)

const (
	redisLastFlushKey = "candle:last_flush_date"
	alertStream       = "alerts:flush_failure"
	partSize          = 50 * 1024 * 1024 // 50 MB per multipart chunk
	smallFileLimit    = 5 * 1024 * 1024  // files ≤ 5 MB use PutObject (no multipart)
)

// Clock provides the current time. Injected so Run() is testable.
type Clock interface {
	Now() time.Time
}

// Config holds all B2 and flush scheduler settings for the flusher.
type Config struct {
	B2KeyID           string
	B2AppKey          string
	B2Bucket          string
	B2Endpoint        string
	FlushTimeUTC      string // "HH:MM", default "03:00"
	FlushDateOverride string // "YYYY-MM-DD"; when set, flush only that date and return
	QuestDBHTTPAddr   string
}

// Flusher runs the daily snapshot_1s → B2 Parquet pipeline.
type Flusher struct {
	cfg            Config
	rdb            *redis.Client
	logger         *slog.Logger
	clk            Clock
	httpClient     *http.Client
	successFn      func()
	failureFn      func()
	alertFailureFn func()
}

// New constructs a Flusher. Pass nil callbacks to disable counter increments.
func New(cfg Config, rdb *redis.Client, logger *slog.Logger, clk Clock,
	successFn, failureFn, alertFailureFn func()) *Flusher {
	return &Flusher{
		cfg:            cfg,
		rdb:            rdb,
		logger:         logger,
		clk:            clk,
		httpClient:     &http.Client{}, // no global timeout; per-request contexts control deadlines
		successFn:      successFn,
		failureFn:      failureFn,
		alertFailureFn: alertFailureFn,
	}
}

// Run is the main loop. Returns when ctx is cancelled.
// FLUSH_DATE_OVERRIDE: flush exactly that date, write manifest, return immediately.
// Otherwise: run catch-up then wait for daily FLUSH_TIME_UTC.
func (f *Flusher) Run(ctx context.Context) error {
	// One-shot override mode for testing / manual backfill.
	if f.cfg.FlushDateOverride != "" {
		date, err := time.Parse("2006-01-02", f.cfg.FlushDateOverride)
		if err != nil {
			return fmt.Errorf("FLUSH_DATE_OVERRIDE parse: %w", err)
		}
		return f.flushDate(ctx, date)
	}

	// Catch-up: flush any days we missed since last success.
	if err := f.runCatchup(ctx); err != nil {
		f.logger.ErrorContext(ctx, "catch-up failed", "error", err)
	}

	for {
		next, err := nextFlushTime(f.clk.Now(), f.cfg.FlushTimeUTC)
		if err != nil {
			return fmt.Errorf("nextFlushTime: %w", err)
		}
		f.logger.InfoContext(ctx, "flush scheduled", "next", next.Format(time.RFC3339))

		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(time.Until(next)):
		}

		// Flush yesterday (the completed day before the flush time fires).
		yesterday := f.clk.Now().UTC().AddDate(0, 0, -1).Truncate(24 * time.Hour)
		if err := f.flushDate(ctx, yesterday); err != nil {
			f.logger.ErrorContext(ctx, "daily flush failed", "date", yesterday.Format("2006-01-02"), "error", err)
		}
	}
}

// runCatchup flushes all missing days from the last successful flush date up to (but not including) today.
func (f *Flusher) runCatchup(ctx context.Context) error {
	lastFlush, ok, err := f.readLastFlushDate(ctx)
	if err != nil {
		return err
	}
	if !ok {
		f.logger.InfoContext(ctx, "catch-up: no prior flush found, skipping")
		return nil
	}

	today := f.clk.Now().UTC().Truncate(24 * time.Hour)
	missing := flushDateRange(lastFlush, today)
	if len(missing) == 0 {
		f.logger.InfoContext(ctx, "catch-up: nothing to catch up")
		return nil
	}

	f.logger.InfoContext(ctx, "catch-up: flushing missing dates", "count", len(missing),
		"from", missing[0].Format("2006-01-02"), "to", missing[len(missing)-1].Format("2006-01-02"))

	for _, d := range missing {
		// Idempotency: skip if flush_manifest already has a success row for this date.
		if already, err := f.manifestHasSuccess(ctx, d); err != nil {
			f.logger.WarnContext(ctx, "catch-up: manifest check failed, attempting flush anyway",
				"date", d.Format("2006-01-02"), "error", err)
		} else if already {
			f.logger.InfoContext(ctx, "catch-up: already flushed, skipping", "date", d.Format("2006-01-02"))
			continue
		}

		if err := f.flushDate(ctx, d); err != nil {
			f.logger.ErrorContext(ctx, "catch-up: flush failed, continuing",
				"date", d.Format("2006-01-02"), "error", err)
		}
	}
	return nil
}

// flushDate exports one day of snapshot_1s to B2 and writes a flush_manifest row.
func (f *Flusher) flushDate(ctx context.Context, date time.Time) error {
	start := f.clk.Now()
	path := b2Path(date)

	rows, err := f.queryRows(ctx, date)
	if err != nil {
		elapsed := f.clk.Now().Sub(start).Milliseconds()
		runFailureSequence(
			func() { f.writeManifestRow(ctx, date, 0, path, false, err.Error(), elapsed) },
			func() { f.publishFlushAlert(date, path, err) },
			f.failureFn,
		)
		return fmt.Errorf("query rows: %w", err)
	}

	if len(rows) == 0 {
		f.logger.InfoContext(ctx, "flush: no data rows for date, skipping",
			"date", date.Format("2006-01-02"))
		return nil
	}

	parquetBytes, err := f.writeParquet(rows)
	if err != nil {
		elapsed := f.clk.Now().Sub(start).Milliseconds()
		runFailureSequence(
			func() { f.writeManifestRow(ctx, date, len(rows), path, false, err.Error(), elapsed) },
			func() { f.publishFlushAlert(date, path, err) },
			f.failureFn,
		)
		return fmt.Errorf("write parquet: %w", err)
	}

	uploadCtx, cancel := context.WithTimeout(ctx, 4*time.Hour)
	defer cancel()

	if err := f.uploadToB2(uploadCtx, path, parquetBytes); err != nil {
		elapsed := f.clk.Now().Sub(start).Milliseconds()
		runFailureSequence(
			func() { f.writeManifestRow(ctx, date, len(rows), path, false, err.Error(), elapsed) },
			func() { f.publishFlushAlert(date, path, err) },
			f.failureFn,
		)
		return fmt.Errorf("upload to B2: %w", err)
	}

	elapsed := f.clk.Now().Sub(start).Milliseconds()
	f.writeManifestRow(ctx, date, len(rows), path, true, "", elapsed)

	// Persist last successful flush date to Redis.
	if err := f.rdb.Set(ctx, redisLastFlushKey, date.Format("2006-01-02"), 0).Err(); err != nil {
		f.logger.WarnContext(ctx, "flush: failed to update last_flush_date in Redis",
			"date", date.Format("2006-01-02"), "error", err)
	}

	f.logger.InfoContext(ctx, "flush complete",
		"date", date.Format("2006-01-02"), "rows", len(rows),
		"bytes", len(parquetBytes), "b2_path", path, "elapsed_ms", elapsed)

	if f.successFn != nil {
		f.successFn()
	}
	return nil
}

// queryRows fetches all snapshot_1s rows for the given UTC date via QuestDB /exp (CSV).
func (f *Flusher) queryRows(ctx context.Context, date time.Time) ([]Snapshot1sRow, error) {
	startUs := date.UTC().UnixMicro()
	endUs := date.UTC().Add(24 * time.Hour).UnixMicro()

	query := fmt.Sprintf(
		"SELECT * FROM snapshot_1s WHERE ts >= %d AND ts < %d ORDER BY ts",
		startUs, endUs,
	)
	u := "http://" + f.cfg.QuestDBHTTPAddr + "/exp?query=" + url.QueryEscape(query)
	// Use a 4-hour deadline for the streaming CSV response — large days can transfer slowly.
	queryCtx, cancel := context.WithTimeout(ctx, 4*time.Hour)
	defer cancel()
	req, err := http.NewRequestWithContext(queryCtx, http.MethodGet, u, nil)
	if err != nil {
		return nil, err
	}
	resp, err := f.httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(io.LimitReader(resp.Body, 512))
		return nil, fmt.Errorf("QuestDB /exp returned %d: %s", resp.StatusCode, body)
	}

	r := csv.NewReader(resp.Body)
	// First row is header — build column→index map for robustness.
	header, err := r.Read()
	if err != nil {
		return nil, fmt.Errorf("CSV header: %w", err)
	}
	idx := make(map[string]int, len(header))
	for i, col := range header {
		idx[col] = i
	}

	var rows []Snapshot1sRow
	for {
		rec, err := r.Read()
		if err == io.EOF {
			break
		}
		if err != nil {
			return nil, fmt.Errorf("CSV read: %w", err)
		}
		row, err := parseCSVRow(rec, idx)
		if err != nil {
			return nil, fmt.Errorf("CSV parse: %w", err)
		}
		rows = append(rows, row)
	}
	return rows, nil
}

// writeParquet serializes rows to a zstd-compressed Parquet file in memory.
func (f *Flusher) writeParquet(rows []Snapshot1sRow) ([]byte, error) {
	buf := &bytes.Buffer{}
	w := parquet.NewGenericWriter[Snapshot1sRow](buf,
		parquet.Compression(&parquet.Zstd),
		parquet.PageBufferSize(1*1024*1024),
	)
	if _, err := w.Write(rows); err != nil {
		return nil, fmt.Errorf("parquet write: %w", err)
	}
	if err := w.Close(); err != nil {
		return nil, fmt.Errorf("parquet close: %w", err)
	}
	return buf.Bytes(), nil
}

// s3Client builds an S3-compatible client pointed at B2.
func (f *Flusher) s3Client() *s3.Client {
	awsCfg := aws.Config{
		Region:      "us-east-1",
		Credentials: staticCreds{keyID: f.cfg.B2KeyID, secret: f.cfg.B2AppKey},
	}
	return s3.NewFromConfig(awsCfg, func(o *s3.Options) {
		o.BaseEndpoint = aws.String(f.cfg.B2Endpoint)
		o.UsePathStyle = true
	})
}

// uploadToB2 uploads data to B2. For small files uses PutObject; for larger files
// uses manual multipart so we hold the upload ID for clean AbortMultipartUpload on failure.
// Critical: AbortMultipartUpload uses a fresh context, not the potentially-cancelled uploadCtx.
func (f *Flusher) uploadToB2(uploadCtx context.Context, key string, data []byte) error {
	client := f.s3Client()

	if len(data) <= smallFileLimit {
		_, err := client.PutObject(uploadCtx, &s3.PutObjectInput{
			Bucket:      aws.String(f.cfg.B2Bucket),
			Key:         aws.String(key),
			Body:        bytes.NewReader(data),
			ContentType: aws.String("application/octet-stream"),
		})
		return err
	}

	// Multipart upload for large files.
	createResp, err := client.CreateMultipartUpload(uploadCtx, &s3.CreateMultipartUploadInput{
		Bucket:      aws.String(f.cfg.B2Bucket),
		Key:         aws.String(key),
		ContentType: aws.String("application/octet-stream"),
	})
	if err != nil {
		return fmt.Errorf("create multipart upload: %w", err)
	}
	uploadID := aws.ToString(createResp.UploadId)

	abort := func() {
		abortCtx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
		defer cancel()
		_, _ = client.AbortMultipartUpload(abortCtx, &s3.AbortMultipartUploadInput{
			Bucket:   aws.String(f.cfg.B2Bucket),
			Key:      aws.String(key),
			UploadId: aws.String(uploadID),
		})
	}

	var parts []s3types.CompletedPart
	for i := 0; i*partSize < len(data); i++ {
		start := i * partSize
		end := start + partSize
		if end > len(data) {
			end = len(data)
		}
		partNum := int32(i + 1)

		resp, err := client.UploadPart(uploadCtx, &s3.UploadPartInput{
			Bucket:     aws.String(f.cfg.B2Bucket),
			Key:        aws.String(key),
			UploadId:   aws.String(uploadID),
			PartNumber: aws.Int32(partNum),
			Body:       bytes.NewReader(data[start:end]),
		})
		if err != nil {
			abort()
			return fmt.Errorf("upload part %d: %w", partNum, err)
		}
		parts = append(parts, s3types.CompletedPart{
			ETag:       resp.ETag,
			PartNumber: aws.Int32(partNum),
		})
	}

	_, err = client.CompleteMultipartUpload(uploadCtx, &s3.CompleteMultipartUploadInput{
		Bucket:   aws.String(f.cfg.B2Bucket),
		Key:      aws.String(key),
		UploadId: aws.String(uploadID),
		MultipartUpload: &s3types.CompletedMultipartUpload{
			Parts: parts,
		},
	})
	if err != nil {
		abort()
		return fmt.Errorf("complete multipart upload: %w", err)
	}
	return nil
}

// writeManifestRow inserts a row into flush_manifest via QuestDB HTTP /exec.
func (f *Flusher) writeManifestRow(ctx context.Context, date time.Time, rowCount int, b2Path string, success bool, errMsg string, durationMs int64) {
	dateUs := date.UTC().UnixMicro()
	successStr := "false"
	if success {
		successStr = "true"
	}
	escaped := strings.ReplaceAll(errMsg, "'", "''")
	escapedPath := strings.ReplaceAll(b2Path, "'", "''")

	query := fmt.Sprintf(
		"INSERT INTO flush_manifest VALUES (systimestamp(), 'all', %d, %d, '%s', %s, '%s', %d)",
		dateUs, rowCount, escapedPath, successStr, escaped, durationMs,
	)
	u := "http://" + f.cfg.QuestDBHTTPAddr + "/exec?query=" + url.QueryEscape(query)
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, u, nil)
	if err != nil {
		f.logger.ErrorContext(ctx, "flush manifest: request build failed", "error", err)
		return
	}
	resp, err := f.httpClient.Do(req)
	if err != nil {
		f.logger.ErrorContext(ctx, "flush manifest: write failed", "error", err)
		return
	}
	defer resp.Body.Close()
	var execResp struct {
		Error string `json:"error"`
	}
	if err := json.NewDecoder(io.LimitReader(resp.Body, 4096)).Decode(&execResp); err == nil && execResp.Error != "" {
		f.logger.ErrorContext(ctx, "flush manifest: QuestDB exec error", "error", execResp.Error)
	}
}

// publishFlushAlert XADDs to alerts:flush_failure. On XADD failure, increments alertFailureFn.
func (f *Flusher) publishFlushAlert(date time.Time, b2Path string, flushErr error) {
	alertCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	values := buildAlertValues(date, b2Path, flushErr, f.clk.Now().UnixMilli())
	err := f.rdb.XAdd(alertCtx, &redis.XAddArgs{
		Stream: alertStream,
		MaxLen: 1000,
		Approx: true,
		Values: values,
	}).Err()
	if err != nil {
		f.logger.ErrorContext(alertCtx, "flush alert publish failed",
			"date", date.Format("2006-01-02"), "alert_error", err)
		if f.alertFailureFn != nil {
			f.alertFailureFn()
		}
	}
}

// buildAlertValues constructs the Redis stream message fields for a flush failure alert.
// Pure function — no IO; directly testable in L1.
func buildAlertValues(date time.Time, b2Path string, flushErr error, nowMs int64) map[string]any {
	msg := flushErr.Error()
	if runes := []rune(msg); len(runes) > 512 {
		msg = string(runes[:512])
	}
	return map[string]any{
		"ts":        strconv.FormatInt(nowMs, 10),
		"date":      date.Format("2006-01-02"),
		"error_msg": msg,
		"b2_path":   b2Path,
	}
}

// runFailureSequence executes the canonical failure response in the required order:
// manifest write → alert publish → failure counter. AC 4 mandates this sequence.
// Pure function — no IO; directly testable in L1.
func runFailureSequence(manifestFn, alertFn, failureFn func()) {
	manifestFn()
	alertFn()
	if failureFn != nil {
		failureFn()
	}
}

// readLastFlushDate reads Redis first, falls back to flush_manifest, returns (date, found, err).
func (f *Flusher) readLastFlushDate(ctx context.Context) (time.Time, bool, error) {
	val, err := f.rdb.Get(ctx, redisLastFlushKey).Result()
	if err == nil && val != "" {
		d, err := time.Parse("2006-01-02", val)
		if err == nil {
			return d, true, nil
		}
		f.logger.WarnContext(ctx, "catch-up: Redis last_flush_date unparseable, falling back to manifest",
			"raw", val, "error", err)
	}

	// Redis key absent or unreadable — query flush_manifest.
	return f.readManifestLastSuccess(ctx)
}

// readManifestLastSuccess queries flush_manifest for the most recent success=true row.
func (f *Flusher) readManifestLastSuccess(ctx context.Context) (time.Time, bool, error) {
	query := "SELECT date_flushed FROM flush_manifest WHERE success = true ORDER BY date_flushed DESC LIMIT 1"
	u := "http://" + f.cfg.QuestDBHTTPAddr + "/exec?query=" + url.QueryEscape(query)
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, u, nil)
	if err != nil {
		return time.Time{}, false, err
	}
	resp, err := f.httpClient.Do(req)
	if err != nil {
		return time.Time{}, false, err
	}
	defer resp.Body.Close()

	var result struct {
		Dataset [][]any `json:"dataset"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return time.Time{}, false, err
	}
	if len(result.Dataset) == 0 || len(result.Dataset[0]) == 0 {
		return time.Time{}, false, nil // first run — no rows
	}

	// QuestDB returns TIMESTAMP as a string in ISO 8601 or as microseconds.
	// Accept either format.
	raw := fmt.Sprintf("%v", result.Dataset[0][0])
	// Try ISO 8601 first.
	for _, layout := range []string{time.RFC3339Nano, "2006-01-02T15:04:05.999999Z"} {
		if d, err := time.Parse(layout, raw); err == nil {
			return d.UTC().Truncate(24 * time.Hour), true, nil
		}
	}
	// Try microseconds integer.
	if us, err := strconv.ParseInt(raw, 10, 64); err == nil {
		return time.UnixMicro(us).UTC().Truncate(24 * time.Hour), true, nil
	}
	return time.Time{}, false, fmt.Errorf("cannot parse date_flushed: %q", raw)
}

// manifestHasSuccess returns true if flush_manifest has a success=true row for the given date.
func (f *Flusher) manifestHasSuccess(ctx context.Context, date time.Time) (bool, error) {
	startUs := date.UTC().UnixMicro()
	endUs := date.UTC().Add(24 * time.Hour).UnixMicro()
	query := fmt.Sprintf(
		"SELECT count() FROM flush_manifest WHERE success = true AND date_flushed >= %d AND date_flushed < %d",
		startUs, endUs,
	)
	u := "http://" + f.cfg.QuestDBHTTPAddr + "/exec?query=" + url.QueryEscape(query)
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, u, nil)
	if err != nil {
		return false, err
	}
	resp, err := f.httpClient.Do(req)
	if err != nil {
		return false, err
	}
	defer resp.Body.Close()

	var result struct {
		Dataset [][]any `json:"dataset"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return false, err
	}
	if len(result.Dataset) == 0 || len(result.Dataset[0]) == 0 {
		return false, nil
	}
	v, _ := result.Dataset[0][0].(float64)
	return v > 0, nil
}

// ── Pure helpers (no IO — directly testable in L1) ───────────────────────────

// nextFlushTime returns the next wall-clock moment when the daily flush should fire.
func nextFlushTime(now time.Time, flushTimeUTC string) (time.Time, error) {
	parts := strings.SplitN(flushTimeUTC, ":", 2)
	if len(parts) != 2 {
		return time.Time{}, fmt.Errorf("invalid FLUSH_TIME_UTC %q: expected HH:MM", flushTimeUTC)
	}
	hour, err := strconv.Atoi(parts[0])
	if err != nil || hour < 0 || hour > 23 {
		return time.Time{}, fmt.Errorf("invalid hour in FLUSH_TIME_UTC %q", flushTimeUTC)
	}
	minute, err := strconv.Atoi(parts[1])
	if err != nil || minute < 0 || minute > 59 {
		return time.Time{}, fmt.Errorf("invalid minute in FLUSH_TIME_UTC %q", flushTimeUTC)
	}

	n := now.UTC()
	candidate := time.Date(n.Year(), n.Month(), n.Day(), hour, minute, 0, 0, time.UTC)
	if !candidate.After(n) {
		candidate = candidate.Add(24 * time.Hour)
	}
	return candidate, nil
}

// flushDateRange returns dates (lastFlush+1, ..., today-1) in ascending order.
// today is exclusive (the currently-running day is not flushed).
func flushDateRange(lastFlush, today time.Time) []time.Time {
	start := lastFlush.UTC().Truncate(24 * time.Hour).Add(24 * time.Hour)
	end := today.UTC().Truncate(24 * time.Hour)
	var dates []time.Time
	for d := start; d.Before(end); d = d.Add(24 * time.Hour) {
		dates = append(dates, d)
	}
	return dates
}

// b2Path returns the Hive-partitioned B2 object key for the given date.
func b2Path(date time.Time) string {
	return "snapshot_1s/date=" + date.UTC().Format("2006-01-02") + "/data.parquet"
}

// ── CSV parsing helpers ───────────────────────────────────────────────────────

// staticCreds implements aws.CredentialsProvider for B2 key/secret.
type staticCreds struct{ keyID, secret string }

func (s staticCreds) Retrieve(_ context.Context) (aws.Credentials, error) {
	return aws.Credentials{
		AccessKeyID:     s.keyID,
		SecretAccessKey: s.secret,
	}, nil
}

func parseCSVRow(rec []string, idx map[string]int) (Snapshot1sRow, error) {
	get := func(col string) string {
		i, ok := idx[col]
		if !ok || i >= len(rec) {
			return ""
		}
		return rec[i]
	}

	// ts: QuestDB /exp returns ISO 8601 strings for TIMESTAMP columns.
	tsStr := get("ts")
	var tsUs int64
	parsed := false
	for _, layout := range []string{"2006-01-02T15:04:05.999999Z", time.RFC3339Nano} {
		if t, err := time.Parse(layout, tsStr); err == nil {
			tsUs = t.UnixMicro()
			parsed = true
			break
		}
	}
	if !parsed {
		return Snapshot1sRow{}, fmt.Errorf("cannot parse ts %q", tsStr)
	}

	row := Snapshot1sRow{
		TS:       tsUs,
		Exchange: get("exchange"),
		Symbol:   get("symbol"),
	}

	// Nullable DOUBLE columns.
	row.Open = parseFloat(get("open"))
	row.High = parseFloat(get("high"))
	row.Low = parseFloat(get("low"))
	row.Close = parseFloat(get("close"))
	row.Volume = parseFloat(get("volume"))
	row.QuoteVolume = parseFloat(get("quote_volume"))
	row.Twap = parseFloat(get("twap"))
	row.MidPriceOpen = parseFloat(get("mid_price_open"))
	row.MidPriceHigh = parseFloat(get("mid_price_high"))
	row.MidPriceLow = parseFloat(get("mid_price_low"))
	row.Vwmp = parseFloat(get("vwmp"))
	row.SpreadHigh = parseFloat(get("spread_high"))
	row.SpreadLow = parseFloat(get("spread_low"))
	row.SpreadMean = parseFloat(get("spread_mean"))
	row.EffectiveSpread = parseFloat(get("effective_spread"))
	row.BestBidOpen = parseFloat(get("best_bid_open"))
	row.BestAskOpen = parseFloat(get("best_ask_open"))
	row.BestBid = parseFloat(get("best_bid"))
	row.BestAsk = parseFloat(get("best_ask"))
	row.BidDepthL1Open = parseFloat(get("bid_depth_l1_open"))
	row.AskDepthL1Open = parseFloat(get("ask_depth_l1_open"))
	row.BidDepthL2Open = parseFloat(get("bid_depth_l2_open"))
	row.AskDepthL2Open = parseFloat(get("ask_depth_l2_open"))
	row.BidDepthTop10Open = parseFloat(get("bid_depth_top10_open"))
	row.AskDepthTop10Open = parseFloat(get("ask_depth_top10_open"))
	row.BidDepthTotalOpen = parseFloat(get("bid_depth_total_open"))
	row.AskDepthTotalOpen = parseFloat(get("ask_depth_total_open"))
	row.BidDepthL1Close = parseFloat(get("bid_depth_l1_close"))
	row.AskDepthL1Close = parseFloat(get("ask_depth_l1_close"))
	row.BidDepthL2Close = parseFloat(get("bid_depth_l2_close"))
	row.AskDepthL2Close = parseFloat(get("ask_depth_l2_close"))
	row.BidDepthTop10Close = parseFloat(get("bid_depth_top10_close"))
	row.AskDepthTop10Close = parseFloat(get("ask_depth_top10_close"))
	row.BidDepthTotalClose = parseFloat(get("bid_depth_total_close"))
	row.AskDepthTotalClose = parseFloat(get("ask_depth_total_close"))
	row.WeightedBidPrice = parseFloat(get("weighted_bid_price"))
	row.WeightedAskPrice = parseFloat(get("weighted_ask_price"))
	row.DepthTo1PctBid = parseFloat(get("depth_to_1pct_bid"))
	row.DepthTo1PctAsk = parseFloat(get("depth_to_1pct_ask"))
	row.OFI = parseFloat(get("ofi"))
	row.OFIL1 = parseFloat(get("ofi_l1"))
	row.BuyVolume = parseFloat(get("buy_volume"))
	row.SellVolume = parseFloat(get("sell_volume"))
	row.FootprintJSON = getString(get("footprint_json"))
	row.POCPrice = parseFloat(get("poc_price"))
	row.ValueAreaHigh = parseFloat(get("value_area_high"))
	row.ValueAreaLow = parseFloat(get("value_area_low"))
	row.POCVolume = parseFloat(get("poc_volume"))
	row.ImbalanceBuyCount = parseInt32(get("imbalance_buy_count"))
	row.ImbalanceSellCount = parseInt32(get("imbalance_sell_count"))
	row.ImbalanceStackBuy = parseInt32(get("imbalance_stack_buy"))
	row.ImbalanceStackSell = parseInt32(get("imbalance_stack_sell"))
	row.ImbalanceRatio = parseFloat(get("imbalance_ratio"))
	row.SinglePrintCount = parseInt32(get("single_print_count"))
	row.SinglePrintLevelsJSON = getString(get("single_print_levels_json"))
	row.UnfinishedTop = parseBool(get("unfinished_top"))
	row.UnfinishedBottom = parseBool(get("unfinished_bottom"))
	row.AbsorptionDetected = parseBool(get("absorption_detected"))
	row.FootprintDeltaDivergence = parseInt32(get("footprint_delta_divergence"))
	row.CumDelta = parseFloat(get("cum_delta"))
	row.CVDDivergence = parseInt32(get("cvd_divergence"))
	row.IcebergBidDetected = parseBool(get("iceberg_bid_detected"))
	row.IcebergAskDetected = parseBool(get("iceberg_ask_detected"))
	row.IcebergPrice = parseFloat(get("iceberg_price"))
	row.BlockBuyVolume = parseFloat(get("block_buy_volume"))
	row.BlockSellVolume = parseFloat(get("block_sell_volume"))
	row.MaxTradeSize = parseFloat(get("max_trade_size"))
	row.TradeClustering = parseFloat(get("trade_clustering"))
	row.RealizedVol = parseFloat(get("realized_vol"))
	row.RealizedSkewness = parseFloat(get("realized_skewness"))
	row.AvgBidOrderSize = parseFloat(get("avg_bid_order_size"))
	row.AvgAskOrderSize = parseFloat(get("avg_ask_order_size"))
	row.QuoteStuffRatio = parseFloat(get("quote_stuff_ratio"))
	row.TradeSignAutocorr = parseFloat(get("trade_sign_autocorr"))
	row.InterTradeIntervalStdMs = parseFloat(get("inter_trade_interval_std_ms"))

	// Nullable INT columns.
	row.TradeCount = parseInt32(get("trade_count"))
	row.BuyCount = parseInt32(get("buy_count"))
	row.LargeBidOrders = parseInt32(get("large_bid_orders"))
	row.LargeAskOrders = parseInt32(get("large_ask_orders"))
	row.FirstTradeOffsetMs = parseInt32(get("first_trade_offset_ms"))
	row.LastTradeOffsetMs = parseInt32(get("last_trade_offset_ms"))
	row.MaxConsecutiveRun = parseInt32(get("max_consecutive_run"))
	row.UptickCount = parseInt32(get("uptick_count"))
	row.DowntickCount = parseInt32(get("downtick_count"))
	row.BidOrderArrivals = parseInt32(get("bid_order_arrivals"))
	row.AskOrderArrivals = parseInt32(get("ask_order_arrivals"))
	row.BidCancelCount = parseInt32(get("bid_cancel_count"))
	row.AskCancelCount = parseInt32(get("ask_cancel_count"))
	row.OBModifyCount = parseInt32(get("ob_modify_count"))
	row.BestBidChanges = parseInt32(get("best_bid_changes"))
	row.BestAskChanges = parseInt32(get("best_ask_changes"))
	row.NumTradePriceLevels = parseInt32(get("num_trade_price_levels"))

	// Quality fields — always written, non-nullable.
	row.IsPartial = get("is_partial") == "true"
	if v := get("gap_count"); v != "" {
		if n, err := strconv.ParseInt(v, 10, 32); err == nil {
			row.GapCount = int32(n)
		}
	}
	if v := get("bar_count"); v != "" {
		if n, err := strconv.ParseInt(v, 10, 32); err == nil {
			row.BarCount = int32(n)
		}
	}

	return row, nil
}

func getString(s string) *string {
	if s == "" {
		return nil
	}
	return &s
}

func parseFloat(s string) *float64 {
	if s == "" {
		return nil
	}
	v, err := strconv.ParseFloat(s, 64)
	if err != nil {
		return nil
	}
	return &v
}

func parseBool(s string) *bool {
	if s == "" {
		return nil
	}
	v := s == "true"
	return &v
}

func parseInt32(s string) *int32 {
	if s == "" {
		return nil
	}
	v, err := strconv.ParseInt(s, 10, 32)
	if err != nil {
		return nil
	}
	n := int32(v)
	return &n
}

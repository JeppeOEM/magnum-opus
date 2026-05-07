//go:build l4

// Package l4_test exercises fault injection scenarios using toxiproxy.
// Requires: docker compose -f docker-compose.test.yml up -d
// Run via: make test-l4
package l4_test

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"testing"
	"time"

	goredis "github.com/redis/go-redis/v9"
	"github.com/stretchr/testify/require"

	"github.com/mrqdt/magnum-opus/aggregator/internal/exchange"
	"github.com/mrqdt/magnum-opus/aggregator/internal/symbol"
	"github.com/mrqdt/magnum-opus/aggregator/internal/testutil"
	rediswriter "github.com/mrqdt/magnum-opus/aggregator/internal/writer/redis"
)

const (
	toxiproxyAPI        = "http://localhost:8474"
	redisProxyAddr      = "localhost:6380"
	questdbILPProxyAddr = "localhost:9010"
	// Docker-internal upstream addresses (resolved by toxiproxy inside the compose network).
	redisUpstream      = "redis:6379"
	questdbILPUpstream = "questdb:9009"
)

func TestMain(m *testing.M) {
	if err := setupProxies(); err != nil {
		fmt.Fprintf(os.Stderr, "l4: proxy setup failed: %v\n", err)
		os.Exit(1)
	}
	code := m.Run()
	teardownProxies()
	os.Exit(code)
}

// TestL4_RedisPartition verifies that when the Redis TCP connection is cut,
// the writer exhausts retries, emits a best-effort gap marker, and resumes
// writing after the partition is healed.
func TestL4_RedisPartition(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()

	clk := testutil.NewMockClock(time.Now())
	rc := goredis.NewClient(&goredis.Options{Addr: redisProxyAddr})
	defer rc.Close()

	require.NoError(t, rc.Ping(ctx).Err(), "redis not reachable via proxy")

	realClient := rediswriter.NewRealClient(rc)
	// Short retry duration so the partition is detected within the test budget.
	w := rediswriter.New(realClient, clk, 2*time.Second)

	sym := symbol.Normalize("bybit", "BTCUSDT")
	makeTick := func(seq uint64) exchange.Tick {
		return exchange.Tick{
			Exchange:   "bybit",
			Symbol:     sym,
			Side:       "ask",
			Price:      "50000.00",
			Size:       "1.5",
			Seq:        seq,
			TsExchange: time.Now().UnixNano(),
			TsLocal:    time.Now().UnixNano(),
		}
	}

	// Write a tick before the partition — must succeed.
	require.NoError(t, w.Write(ctx, makeTick(1)))

	// Cut the Redis TCP connection via toxiproxy.
	require.NoError(t, disableProxy("redis-proxy"))
	t.Cleanup(func() { _ = enableProxy("redis-proxy") })

	// Writing should fail after maxRetryDur (2s).
	writeErr := w.Write(ctx, makeTick(2))
	require.Error(t, writeErr, "expected write to fail during Redis partition")

	// Heal the partition.
	require.NoError(t, enableProxy("redis-proxy"))

	// After healing, new writes should succeed.
	require.Eventually(t, func() bool {
		return w.Write(ctx, makeTick(100)) == nil
	}, 5*time.Second, 200*time.Millisecond, "write should succeed after partition healed")

	// Verify a gap marker is present in the stream.
	entries, err := rc.XRange(ctx, "ticks:bybit:BTCUSDT", "-", "+").Result()
	require.NoError(t, err)
	var foundGap bool
	for _, e := range entries {
		if e.Values["type"] == "gap" {
			foundGap = true
			break
		}
	}
	require.True(t, foundGap, "expected a gap marker in the stream after Redis partition")
}

// TestL4_QuestDBPartition verifies that a QuestDB ILP partition does not
// affect tick capture — Redis writes continue unaffected while QuestDB is down.
func TestL4_QuestDBPartition(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()

	rc := goredis.NewClient(&goredis.Options{Addr: redisProxyAddr})
	defer rc.Close()

	if err := rc.Ping(ctx).Err(); err != nil {
		t.Skipf("redis not reachable via proxy: %v", err)
	}

	// Cut QuestDB ILP.
	require.NoError(t, disableProxy("questdb-ilp-proxy"))
	t.Cleanup(func() { _ = enableProxy("questdb-ilp-proxy") })

	realClient := rediswriter.NewRealClient(rc)
	clk := testutil.NewMockClock(time.Now())
	w := rediswriter.New(realClient, clk, 2*time.Second)

	sym := symbol.Normalize("bybit", "ETHUSDT")
	tick := exchange.Tick{
		Exchange:   "bybit",
		Symbol:     sym,
		Side:       "bid",
		Price:      "3000.00",
		Size:       "10.0",
		Seq:        1,
		TsExchange: time.Now().UnixNano(),
		TsLocal:    time.Now().UnixNano(),
	}

	// Redis writes must succeed even while QuestDB is partitioned.
	for i := range 5 {
		tick.Seq = uint64(i + 1)
		require.NoError(t, w.Write(ctx, tick), "redis write should succeed despite QuestDB partition (seq=%d)", tick.Seq)
	}
}

// TestL4_NetworkFlap verifies reconnect behavior when the Redis connection
// is briefly interrupted and then restored within the retry window.
func TestL4_NetworkFlap(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()

	rc := goredis.NewClient(&goredis.Options{Addr: redisProxyAddr})
	defer rc.Close()
	require.NoError(t, rc.Ping(ctx).Err())

	realClient := rediswriter.NewRealClient(rc)
	clk := testutil.NewMockClock(time.Now())
	// 3s retry — a 500ms flap should be healed before the retry window expires.
	w := rediswriter.New(realClient, clk, 3*time.Second)

	sym := symbol.Normalize("kucoin", "BTC-USDT")
	makeTick := func(seq uint64) exchange.Tick {
		return exchange.Tick{
			Exchange: "kucoin", Symbol: sym, Side: "ask",
			Price: "50000.00", Size: "1.0",
			Seq: seq, TsExchange: time.Now().UnixNano(), TsLocal: time.Now().UnixNano(),
		}
	}

	// Write a tick before the flap.
	require.NoError(t, w.Write(ctx, makeTick(1)))

	// Short flap: cut and restore within retry window.
	require.NoError(t, disableProxy("redis-proxy"))
	time.Sleep(500 * time.Millisecond)
	require.NoError(t, enableProxy("redis-proxy"))

	// Writer should retry and succeed (flap shorter than maxRetryDur).
	require.Eventually(t, func() bool {
		return w.Write(ctx, makeTick(2)) == nil
	}, 5*time.Second, 200*time.Millisecond, "write should succeed after short flap")

	// Both ticks should appear in the stream (possibly with or without gap marker).
	entries, err := rc.XRange(ctx, "ticks:kucoin:BTCUSDT", "-", "+").Result()
	require.NoError(t, err)
	require.GreaterOrEqual(t, len(entries), 2, "expected at least 2 stream entries after flap")
}

// ── Toxiproxy HTTP helpers ────────────────────────────────────────────────────

type proxyConfig struct {
	Name     string `json:"name"`
	Listen   string `json:"listen"`
	Upstream string `json:"upstream"`
	Enabled  bool   `json:"enabled"`
}

func setupProxies() error {
	if err := createProxy("redis-proxy", "0.0.0.0:6380", redisUpstream); err != nil {
		return fmt.Errorf("redis proxy: %w", err)
	}
	if err := createProxy("questdb-ilp-proxy", "0.0.0.0:9010", questdbILPUpstream); err != nil {
		return fmt.Errorf("questdb-ilp proxy: %w", err)
	}
	return nil
}

func teardownProxies() {
	deleteProxy("redis-proxy")
	deleteProxy("questdb-ilp-proxy")
}

func createProxy(name, listen, upstream string) error {
	body, _ := json.Marshal(proxyConfig{Name: name, Listen: listen, Upstream: upstream, Enabled: true})
	resp, err := http.Post(toxiproxyAPI+"/proxies", "application/json", bytes.NewReader(body))
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	io.Copy(io.Discard, resp.Body)
	// 201=created, 409=already exists (idempotent).
	if resp.StatusCode != http.StatusCreated && resp.StatusCode != http.StatusConflict {
		return fmt.Errorf("createProxy %s: HTTP %d", name, resp.StatusCode)
	}
	return nil
}

func disableProxy(name string) error { return setProxyEnabled(name, false) }
func enableProxy(name string) error  { return setProxyEnabled(name, true) }

func setProxyEnabled(name string, enabled bool) error {
	body, _ := json.Marshal(map[string]bool{"enabled": enabled})
	req, _ := http.NewRequest(http.MethodPost, toxiproxyAPI+"/proxies/"+name, bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	io.Copy(io.Discard, resp.Body)
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("setProxyEnabled %s enabled=%v: HTTP %d", name, enabled, resp.StatusCode)
	}
	return nil
}

func deleteProxy(name string) {
	req, _ := http.NewRequest(http.MethodDelete, toxiproxyAPI+"/proxies/"+name, nil)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return
	}
	resp.Body.Close()
}

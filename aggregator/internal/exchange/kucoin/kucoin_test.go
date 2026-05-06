//go:build l3

// Package kucoin provides L3 acceptance tests for the KuCoin exchange adapter.
// White-box (package kucoin rather than kucoin_test) is required for:
//   - newWithAPIBase() — test constructor that overrides REST API base URL
//   - a.confirmed — unexported map verified in 200-symbol and spurious-ack tests
//   - a.confirmTimeout — overridden to 50ms in unconfirmed-timeout test
//   - a.ackMu — used to safely read a.confirmed in assertions
//   - a.dispatch() — used to inject wire messages directly in some tests
package kucoin

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"sync"
	"testing"
	"time"

	"github.com/coder/websocket"
	"github.com/coder/websocket/wsjson"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"

	"github.com/mrqdt/magnum-opus/aggregator/internal/config"
	"github.com/mrqdt/magnum-opus/aggregator/internal/exchange"
	"github.com/mrqdt/magnum-opus/aggregator/internal/symbol"
	"github.com/mrqdt/magnum-opus/aggregator/internal/testutil"
)

// ── connDropper ───────────────────────────────────────────────────────────────

// connDropper lets a test forcefully close the current WebSocket connection from
// outside the server handler. A fresh dropper is created per WS connection so
// subsequent reconnects can be dropped independently.
type connDropper struct {
	once sync.Once
	ch   chan struct{}
}

func newConnDropper() *connDropper          { return &connDropper{ch: make(chan struct{})} }
func (d *connDropper) Drop()               { d.once.Do(func() { close(d.ch) }) }
func (d *connDropper) C() <-chan struct{}   { return d.ch }

// ── Mock Server ───────────────────────────────────────────────────────────────

type mockWSServer struct {
	srv            *httptest.Server
	pingIntervalMs int
	pingTimeoutMs  int

	mu                sync.Mutex
	dropResponses     bool // when true: suppress ack and pong responses
	subsSeen          int  // incremented for every subscribe message received
	tokenFails        int  // >0: fail next N token requests; -1: always fail
	tokenRequestCount int  // total token requests received

	dropMu  sync.Mutex
	dropper *connDropper // current connection's dropper; replaced on each new WS conn
}

func newMockWSServer(t *testing.T, pingIntervalMs, pingTimeoutMs int) *mockWSServer {
	t.Helper()
	s := &mockWSServer{
		pingIntervalMs: pingIntervalMs,
		pingTimeoutMs:  pingTimeoutMs,
	}
	mux := http.NewServeMux()
	mux.HandleFunc("/api/v1/bullet-private", s.handleToken)
	mux.HandleFunc("/", s.handleWebSocket)
	s.srv = httptest.NewServer(mux)
	t.Cleanup(s.srv.Close)
	return s
}

func (s *mockWSServer) setDropResponses(v bool) {
	s.mu.Lock()
	s.dropResponses = v
	s.mu.Unlock()
}

func (s *mockWSServer) getDropResponses() bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.dropResponses
}

func (s *mockWSServer) getSubsSeen() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.subsSeen
}

func (s *mockWSServer) setTokenFails(n int) {
	s.mu.Lock()
	s.tokenFails = n
	s.mu.Unlock()
}

func (s *mockWSServer) getTokenRequestCount() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.tokenRequestCount
}

func (s *mockWSServer) handleToken(w http.ResponseWriter, r *http.Request) {
	s.mu.Lock()
	s.tokenRequestCount++
	fail := false
	if s.tokenFails > 0 {
		s.tokenFails--
		fail = true
	} else if s.tokenFails < 0 {
		fail = true
	}
	s.mu.Unlock()

	if fail {
		http.Error(w, "server error", http.StatusInternalServerError)
		return
	}

	wsEndpoint := "ws" + s.srv.URL[len("http"):]
	body := fmt.Sprintf(
		`{"code":"200000","data":{"token":"test-token","instanceServers":[{"endpoint":%q,"pingInterval":%d,"pingTimeout":%d}]}}`,
		wsEndpoint, s.pingIntervalMs, s.pingTimeoutMs,
	)
	w.Header().Set("Content-Type", "application/json")
	_, _ = w.Write([]byte(body))
}

func (s *mockWSServer) handleWebSocket(w http.ResponseWriter, r *http.Request) {
	conn, err := websocket.Accept(w, r, nil)
	if err != nil {
		return
	}

	// Create a fresh dropper for this connection so tests can signal a drop.
	d := newConnDropper()
	s.dropMu.Lock()
	s.dropper = d
	s.dropMu.Unlock()

	// Watcher goroutine: CloseNow closes the underlying net.Conn without a WS
	// frame — safe to call concurrently with wsjson.Write in the handler goroutine.
	go func() {
		select {
		case <-d.C():
			conn.CloseNow()
		case <-r.Context().Done():
		}
	}()

	defer conn.Close(websocket.StatusNormalClosure, "")
	ctx := r.Context()

	_ = wsjson.Write(ctx, conn, map[string]string{"type": "welcome", "id": ""})

	for {
		var msg map[string]any
		if err := wsjson.Read(ctx, conn, &msg); err != nil {
			return
		}
		msgType, _ := msg["type"].(string)
		msgID, _ := msg["id"].(string)
		drop := s.getDropResponses()

		switch msgType {
		case "subscribe":
			s.mu.Lock()
			s.subsSeen++
			s.mu.Unlock()
			if !drop {
				_ = wsjson.Write(ctx, conn, map[string]string{"id": msgID, "type": "ack"})
			}
		case "ping":
			if !drop {
				_ = wsjson.Write(ctx, conn, map[string]string{"id": msgID, "type": "pong"})
			}
		}
	}
}

// DropConn closes the current WebSocket connection via CloseNow.
// The HTTP server remains alive so the adapter's reconnect loop can fetch a new token.
func (s *mockWSServer) DropConn() {
	s.dropMu.Lock()
	d := s.dropper
	s.dropMu.Unlock()
	if d != nil {
		d.Drop()
	}
}

// ── Helpers ───────────────────────────────────────────────────────────────────

func newTestAdapter(t *testing.T, srv *mockWSServer) *Adapter {
	t.Helper()
	clk := testutil.NewMockClock(time.Now())
	a := newWithAPIBase(config.KuCoinConfig{}, http.DefaultClient, srv.srv.URL, clk)
	t.Cleanup(func() { a.Close() }) // ensure cleanup even when test fails early
	return a
}

func closeWithDeadline(t *testing.T, a *Adapter, d time.Duration) {
	t.Helper()
	done := make(chan struct{})
	go func() { a.Close(); close(done) }()
	select {
	case <-done:
	case <-time.After(d):
		t.Fatalf("Close() did not return within %s", d)
	}
}

// waitConfirmed polls a.confirmed until sym is true or deadline elapses.
// Uses white-box access to a.ackMu + a.confirmed.
func waitConfirmed(t *testing.T, a *Adapter, sym string, deadline time.Duration) {
	t.Helper()
	require.Eventually(t, func() bool {
		a.ackMu.Lock()
		defer a.ackMu.Unlock()
		return a.confirmed[sym]
	}, deadline, 5*time.Millisecond, "symbol %q not confirmed within %s", sym, deadline)
}

// waitConfirmedN polls a.confirmed until at least n entries are true.
func waitConfirmedN(t *testing.T, a *Adapter, n int, deadline time.Duration) {
	t.Helper()
	require.Eventually(t, func() bool {
		a.ackMu.Lock()
		defer a.ackMu.Unlock()
		c := 0
		for _, ok := range a.confirmed {
			if ok {
				c++
			}
		}
		return c >= n
	}, deadline, 5*time.Millisecond, "expected %d confirmed symbols within %s", n, deadline)
}

// ── Parser Tests (AC: 5) ──────────────────────────────────────────────────────

func TestParseL2Update_FromFixture(t *testing.T) {
	raw, err := os.ReadFile("testdata/fixtures/l2_update_btc_usdt.json")
	require.NoError(t, err)

	var msg wireMessage
	require.NoError(t, json.Unmarshal(raw, &msg))

	update, err := parseL2Update(msg)
	require.NoError(t, err)

	assert.Equal(t, symbol.Normalize("kucoin", "BTC-USDT"), update.Symbol)
	require.Len(t, update.Deltas, 2)

	wantTs := int64(1620000000000) * int64(time.Millisecond)

	bid := update.Deltas[0]
	assert.Equal(t, "29500.50", bid.Price)
	assert.Equal(t, "1.5", bid.Size)
	assert.Equal(t, uint64(100), bid.Seq)
	assert.Equal(t, wantTs, bid.TsExchange)

	ask := update.Deltas[1]
	assert.Equal(t, "29501.00", ask.Price)
	assert.Equal(t, "0", ask.Size)
	assert.Equal(t, uint64(101), ask.Seq)
	assert.Equal(t, wantTs, ask.TsExchange)
}

func TestParseL2Update_EmptyChanges(t *testing.T) {
	msg := wireMessage{
		Type:  "message",
		Topic: "/market/level2:BTC-USDT",
		Data:  json.RawMessage(`{"sequenceStart":1,"sequenceEnd":1,"changes":{"bids":[],"asks":[]},"time":1620000000000}`),
	}
	update, err := parseL2Update(msg)
	require.NoError(t, err)
	assert.Nil(t, update.Deltas)
}

func TestParseTrade_BuySide(t *testing.T) {
	raw, err := os.ReadFile("testdata/fixtures/trade_btc_usdt.json")
	require.NoError(t, err)

	var msg wireMessage
	require.NoError(t, json.Unmarshal(raw, &msg))

	trade, err := parseTrade(msg)
	require.NoError(t, err)
	assert.Equal(t, "bid", trade.Side)
	assert.Equal(t, "29500.00", trade.Price)
	assert.Equal(t, "0.1", trade.Size)
	assert.Equal(t, symbol.Normalize("kucoin", "BTC-USDT"), trade.Symbol)
}

func TestParseTrade_SellSide(t *testing.T) {
	raw, err := os.ReadFile("testdata/fixtures/trade_sell_btc_usdt.json")
	require.NoError(t, err)

	var msg wireMessage
	require.NoError(t, json.Unmarshal(raw, &msg))

	trade, err := parseTrade(msg)
	require.NoError(t, err)
	assert.Equal(t, "ask", trade.Side)
}

func TestParseL2Update_MalformedData(t *testing.T) {
	msg := wireMessage{
		Type:  "message",
		Topic: "/market/level2:BTC-USDT",
		Data:  json.RawMessage(`not valid json`),
	}
	_, err := parseL2Update(msg)
	assert.Error(t, err)
}

func TestParseTrade_BadSequence(t *testing.T) {
	msg := wireMessage{
		Type:  "message",
		Topic: "/market/match:BTC-USDT",
		Data:  json.RawMessage(`{"sequence":"not-a-number","price":"100","size":"1","side":"buy","time":"1620000000000000000"}`),
	}
	_, err := parseTrade(msg)
	assert.Error(t, err)
}

func TestParseTrade_UnknownSide(t *testing.T) {
	msg := wireMessage{
		Type:  "message",
		Topic: "/market/match:BTC-USDT",
		Data:  json.RawMessage(`{"sequence":"1","price":"100","size":"1","side":"unknown","time":"1620000000000000000"}`),
	}
	_, err := parseTrade(msg)
	assert.Error(t, err)
	assert.Contains(t, err.Error(), "unknown trade side")
}

// ── Adapter: Happy Path (AC: 1, 3) ───────────────────────────────────────────

func TestAdapter_ConnectSubscribeReceiveTick(t *testing.T) {
	srv := newMockWSServer(t, 30000, 5000)
	a := newTestAdapter(t, srv)

	ctx := context.Background()
	require.NoError(t, a.Connect(ctx))
	require.NoError(t, a.Subscribe([]string{"BTC-USDT"}, []exchange.FeedType{exchange.FeedTypeOrderBook}))

	// Poll a.confirmed directly — avoids the server-send/adapter-receive race.
	waitConfirmed(t, a, "BTC-USDT", 2*time.Second)

	// Inject L2 update via dispatch (white-box) — tests the dispatch→tick pipeline.
	// The WS read path is exercised by transport L3 tests; dispatch avoids needing
	// a server-side push mechanism in the mock.
	a.dispatch(wireMessage{
		Type:    "message",
		Topic:   "/market/level2:BTC-USDT",
		Subject: "trade.l2update",
		Data:    json.RawMessage(`{"sequenceStart":100,"sequenceEnd":101,"changes":{"bids":[["29500.50","1.5",100]],"asks":[]},"time":1620000000000}`),
	})

	select {
	case tick := <-a.Ticks():
		assert.Equal(t, exchange.EventTypeUpdate, tick.Type)
		assert.Equal(t, symbol.Normalize("kucoin", "BTC-USDT"), tick.Symbol)
		assert.Equal(t, "29500.50", tick.Price)
		assert.Equal(t, "1.5", tick.Size)
		assert.Equal(t, "bid", tick.Side)
	case <-time.After(2 * time.Second):
		t.Fatal("tick not received within 2s")
	}

	closeWithDeadline(t, a, 2*time.Second)
}

// ── Adapter: Heartbeat Pong Timeout (AC: 2) ───────────────────────────────────

func TestAdapter_HeartbeatPongTimeout(t *testing.T) {
	srv := newMockWSServer(t, 100, 100) // pingInterval=100ms, pingTimeout=100ms
	a := newTestAdapter(t, srv)

	ctx := context.Background()
	require.NoError(t, a.Connect(ctx))
	require.NoError(t, a.Subscribe([]string{"BTC-USDT"}, []exchange.FeedType{exchange.FeedTypeOrderBook}))

	// Ensure the ack was processed by the adapter before stopping pongs.
	// Without this, heartbeat fires before confirmed is set and NeedsSnapshot is
	// never emitted (emitNeedsSnapshot iterates only confirmed symbols).
	waitConfirmed(t, a, "BTC-USDT", 2*time.Second)

	// Stop responding to pings — next heartbeat (≤100ms ping + 100ms timeout)
	// fires reconnectTrigger → runLoop calls emitNeedsSnapshot.
	srv.setDropResponses(true)

	select {
	case sig := <-a.Signals():
		assert.Equal(t, exchange.SignalNeedsSnapshot, sig.Type)
		assert.Equal(t, symbol.Normalize("kucoin", "BTC-USDT"), sig.Symbol)
	case <-time.After(2 * time.Second):
		t.Fatal("SignalNeedsSnapshot not received within 2s after pong timeout")
	}

	closeWithDeadline(t, a, 2*time.Second)
}

// ── Adapter: 200-Symbol Batch Subscription (AC: 4) ────────────────────────────

func TestAdapter_Subscribe200Symbols(t *testing.T) {
	srv := newMockWSServer(t, 30000, 5000)
	a := newTestAdapter(t, srv)

	ctx := context.Background()
	require.NoError(t, a.Connect(ctx))

	syms := make([]string, 200)
	for i := range syms {
		syms[i] = fmt.Sprintf("SYM%d-USDT", i+1)
	}
	require.NoError(t, a.Subscribe(syms, []exchange.FeedType{exchange.FeedTypeOrderBook}))

	// Poll a.confirmed directly — avoids race between server-send and adapter-process.
	waitConfirmedN(t, a, 200, 5*time.Second)

	assert.Equal(t, 2, srv.getSubsSeen(), "expected exactly 2 subscribe messages (2 batches of 100)")

	closeWithDeadline(t, a, 2*time.Second)
}

// ── Adapter: Spurious Ack (AC: 3) ─────────────────────────────────────────────

func TestAdapter_SpuriousAck(t *testing.T) {
	srv := newMockWSServer(t, 30000, 5000)
	a := newTestAdapter(t, srv)

	ctx := context.Background()
	require.NoError(t, a.Connect(ctx))

	// Inject spurious ack for unknown msgID without any prior subscribe.
	a.dispatch(wireMessage{ID: "unknown-id", Type: "ack"})

	// No symbols confirmed; adapter must not panic.
	a.ackMu.Lock()
	confirmed := len(a.confirmed)
	a.ackMu.Unlock()
	assert.Equal(t, 0, confirmed)

	closeWithDeadline(t, a, 2*time.Second)
}

// ── Adapter: Unconfirmed Symbol Timeout (AC: 3) ───────────────────────────────

func TestAdapter_UnconfirmedTimeout(t *testing.T) {
	srv := newMockWSServer(t, 30000, 5000)
	srv.setDropResponses(true) // suppress acks so symbol stays unconfirmed

	a := newTestAdapter(t, srv)
	a.confirmTimeout = 50 * time.Millisecond // override before Connect

	ctx := context.Background()
	require.NoError(t, a.Connect(ctx))
	require.NoError(t, a.Subscribe([]string{"BTC-USDT"}, []exchange.FeedType{exchange.FeedTypeOrderBook}))

	// confirmWatcher fires after 50ms and retries the subscribe.
	// Server receives: 1 (initial) + 1 (retry) = 2 subscribe messages.
	assert.Eventually(t, func() bool {
		return srv.getSubsSeen() >= 2
	}, 500*time.Millisecond, 10*time.Millisecond,
		"expected confirmWatcher retry; subscribesSeen=%d", srv.getSubsSeen())

	closeWithDeadline(t, a, 2*time.Second)
}

// ── Adapter: Disconnect → NeedsSnapshot (AC: 6) ──────────────────────────────

func TestAdapter_DisconnectEmitsNeedsSnapshot(t *testing.T) {
	srv := newMockWSServer(t, 100, 100) // short intervals so heartbeat detects drop fast
	a := newTestAdapter(t, srv)

	ctx := context.Background()
	require.NoError(t, a.Connect(ctx))
	require.NoError(t, a.Subscribe([]string{"BTC-USDT"}, []exchange.FeedType{exchange.FeedTypeOrderBook}))

	// Wait for confirmed before dropping — emitNeedsSnapshot only emits for confirmed syms.
	waitConfirmed(t, a, "BTC-USDT", 2*time.Second)

	// Drop the WS connection via CloseNow (white-box mock).
	// The HTTP server stays alive so the reconnect loop can fetch a new token.
	srv.DropConn()

	// heartbeatLoop detects write failure on next ping (≤100ms) → fireTrigger
	// → runLoop calls emitNeedsSnapshot → NeedsSnapshot on a.Signals().
	expectedSym := symbol.Normalize("kucoin", "BTC-USDT")
	select {
	case sig := <-a.Signals():
		assert.Equal(t, exchange.SignalNeedsSnapshot, sig.Type)
		assert.Equal(t, expectedSym, sig.Symbol)
	case <-time.After(2 * time.Second):
		t.Fatal("SignalNeedsSnapshot not received within 2s after connection drop")
	}

	closeWithDeadline(t, a, 2*time.Second)
}

// ── Token Renewal (AC: 1, 2, 3, 4) ───────────────────────────────────────────

// TestTokenRenewal_HappyPath verifies the renewal goroutine fetches a new token
// when the MockClock is advanced past tokenTTL − renewalLeadTime (23.5 h).
func TestTokenRenewal_HappyPath(t *testing.T) {
	srv := newMockWSServer(t, 30000, 5000)
	a := newTestAdapter(t, srv)
	a.renewalCheckInterval = 1 * time.Millisecond
	a.renewalLeadTime = 30 * time.Minute

	clk, ok := a.clk.(*testutil.MockClock)
	require.True(t, ok, "expected MockClock")

	require.NoError(t, a.Connect(context.Background()))
	require.NoError(t, a.Subscribe([]string{"BTC-USDT"}, []exchange.FeedType{exchange.FeedTypeOrderBook}))
	waitConfirmed(t, a, "BTC-USDT", 2*time.Second)

	a.tokMu.RLock()
	initialFetchedAt := a.tok.fetchedAt
	a.tokMu.RUnlock()

	// Advance past 24h − 30min = 23h30min threshold.
	clk.Advance(23*time.Hour + 31*time.Minute)

	require.Eventually(t, func() bool {
		a.tokMu.RLock()
		defer a.tokMu.RUnlock()
		return a.tok.fetchedAt.After(initialFetchedAt)
	}, 2*time.Second, 5*time.Millisecond, "token was not renewed after clock advanced past threshold")

	closeWithDeadline(t, a, 2*time.Second)
}

// TestTokenRenewal_RetryThenSucceed verifies that transient token fetch failures
// are retried and a successful renewal is eventually stored in a.tok.
func TestTokenRenewal_RetryThenSucceed(t *testing.T) {
	srv := newMockWSServer(t, 30000, 5000)
	a := newTestAdapter(t, srv)
	a.renewalCheckInterval = 1 * time.Millisecond
	a.renewalLeadTime = 30 * time.Minute
	a.renewalMaxAttempts = 3

	clk, ok := a.clk.(*testutil.MockClock)
	require.True(t, ok, "expected MockClock")

	require.NoError(t, a.Connect(context.Background()))

	a.tokMu.RLock()
	initialFetchedAt := a.tok.fetchedAt
	a.tokMu.RUnlock()

	// Fail the first renewal attempt; the second attempt should succeed.
	srv.setTokenFails(1)
	clk.Advance(23*time.Hour + 31*time.Minute)

	// Deadline accounts for ~1 s real backoff sleep after attempt 0 failure.
	require.Eventually(t, func() bool {
		a.tokMu.RLock()
		defer a.tokMu.RUnlock()
		return a.tok.fetchedAt.After(initialFetchedAt)
	}, 3*time.Second, 10*time.Millisecond, "token was not renewed after retry")

	closeWithDeadline(t, a, 2*time.Second)
}

// TestTokenRenewal_ExhaustRetries verifies that exhausted renewal retries trigger
// a reconnect (fireTrigger), causing runLoop to re-authenticate from scratch.
func TestTokenRenewal_ExhaustRetries(t *testing.T) {
	srv := newMockWSServer(t, 30000, 5000)
	a := newTestAdapter(t, srv)
	a.renewalCheckInterval = 1 * time.Millisecond
	a.renewalLeadTime = 30 * time.Minute
	a.renewalMaxAttempts = 1 // single attempt → no inter-attempt sleep → fast test

	clk, ok := a.clk.(*testutil.MockClock)
	require.True(t, ok, "expected MockClock")

	require.NoError(t, a.Connect(context.Background()))
	initialCount := srv.getTokenRequestCount()

	// All future token requests fail.
	srv.setTokenFails(-1)
	clk.Advance(23*time.Hour + 31*time.Minute)

	// After 1 failed renewal, fireTrigger() fires. runLoop tries to reconnect
	// (also fails). Token request count rises above initial + renewalMaxAttempts.
	require.Eventually(t, func() bool {
		return srv.getTokenRequestCount() > initialCount+a.renewalMaxAttempts
	}, 2*time.Second, 5*time.Millisecond, "expected reconnect attempt after renewal exhaustion")

	closeWithDeadline(t, a, 2*time.Second)
}

// TestTokenRenewal_ContextCancel verifies the renewal goroutine exits cleanly
// when the adapter context is cancelled before the renewal threshold is reached.
func TestTokenRenewal_ContextCancel(t *testing.T) {
	srv := newMockWSServer(t, 30000, 5000)
	a := newTestAdapter(t, srv)
	a.renewalCheckInterval = 1 * time.Millisecond
	a.renewalLeadTime = 30 * time.Minute

	// Do NOT advance clock — renewal threshold never crossed.
	require.NoError(t, a.Connect(context.Background()))

	// Close must return within 500 ms, proving ctx.Done() was respected.
	closeWithDeadline(t, a, 500*time.Millisecond)
}

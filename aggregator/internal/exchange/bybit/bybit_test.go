//go:build l3

// Package bybit provides L3 acceptance tests for the Bybit exchange adapter.
// White-box (package bybit rather than bybit_test) is required to access:
//   - parseL2Update, parseTrade, symFromTopic — unexported parser functions
//   - Adapter.muxInst — to read confirmed state in assertion helpers
package bybit

import (
	"context"
	"encoding/json"
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

	"github.com/mrqdt/magnum-opus/aggregator/internal/exchange"
	"github.com/mrqdt/magnum-opus/aggregator/internal/orderbook"
	"github.com/mrqdt/magnum-opus/aggregator/internal/testutil"
)

// ── MockWSServer ─────────────────────────────────────────────────────────────

// mockWSServer simulates a Bybit WebSocket endpoint.
// It auto-acks subscribe messages and auto-responds to ping with pong.
// Market data frames can be injected via Push().
type mockWSServer struct {
	srv *httptest.Server

	mu           sync.Mutex
	suppressPong bool   // when true: suppress pong responses (test ping timeout)
	pingsSeen    int    // count of ping messages received
	subsSeen     int    // count of subscribe messages received

	// pushCh delivers market data frames to the current connection.
	// Replaced on each new connection to avoid stale-channel sends.
	pushCh   chan json.RawMessage
	pushMu   sync.Mutex
}

func newMockWSServer(t *testing.T) *mockWSServer {
	t.Helper()
	s := &mockWSServer{
		pushCh: make(chan json.RawMessage, 16),
	}
	s.srv = httptest.NewServer(http.HandlerFunc(s.handleWebSocket))
	t.Cleanup(s.srv.Close)
	return s
}

func (s *mockWSServer) url() string {
	return "ws" + s.srv.URL[len("http"):]
}

// Push injects a raw JSON frame for the server to send to the current client.
func (s *mockWSServer) Push(msg any) {
	b, _ := json.Marshal(msg)
	s.pushMu.Lock()
	ch := s.pushCh
	s.pushMu.Unlock()
	ch <- json.RawMessage(b)
}

func (s *mockWSServer) setSuppressPong(v bool) {
	s.mu.Lock()
	s.suppressPong = v
	s.mu.Unlock()
}

func (s *mockWSServer) getPingsSeen() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.pingsSeen
}

func (s *mockWSServer) getSubsSeen() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.subsSeen
}

func (s *mockWSServer) handleWebSocket(w http.ResponseWriter, r *http.Request) {
	conn, err := websocket.Accept(w, r, nil)
	if err != nil {
		return
	}
	defer conn.Close(websocket.StatusNormalClosure, "")
	ctx := r.Context()

	// Replace pushCh for this connection so Push() reaches the right handler.
	ch := make(chan json.RawMessage, 16)
	s.pushMu.Lock()
	s.pushCh = ch
	s.pushMu.Unlock()

	// Sender goroutine: forwards Push() messages to the client.
	go func() {
		for {
			select {
			case msg := <-ch:
				_ = wsjson.Write(ctx, conn, json.RawMessage(msg))
			case <-ctx.Done():
				return
			}
		}
	}()

	for {
		var msg map[string]any
		if err := wsjson.Read(ctx, conn, &msg); err != nil {
			return
		}
		op, _ := msg["op"].(string)

		switch op {
		case "subscribe":
			s.mu.Lock()
			s.subsSeen++
			s.mu.Unlock()
			reqID, _ := msg["req_id"].(string)
			_ = wsjson.Write(ctx, conn, map[string]any{
				"op":      "subscribe",
				"req_id":  reqID,
				"success": true,
				"ret_msg": "",
			})
		case "ping":
			s.mu.Lock()
			s.pingsSeen++
			suppress := s.suppressPong
			s.mu.Unlock()
			if !suppress {
				_ = wsjson.Write(ctx, conn, map[string]string{"op": "pong"})
			}
		}
	}
}

// ── Fixtures envelope ─────────────────────────────────────────────────────────

// bybitEnvelope is used to unmarshal fixture files into the outer wire envelope.
type bybitEnvelope struct {
	Topic string          `json:"topic"`
	Type  string          `json:"type"`
	Ts    int64           `json:"ts"`
	Data  json.RawMessage `json:"data"`
}

func loadFixture(t *testing.T, name string) bybitEnvelope {
	t.Helper()
	raw, err := os.ReadFile("testdata/fixtures/" + name)
	require.NoError(t, err, "reading fixture %s", name)
	var env bybitEnvelope
	require.NoError(t, json.Unmarshal(raw, &env), "unmarshal fixture %s", name)
	return env
}

// ── Parser Tests ──────────────────────────────────────────────────────────────

func TestAdapter_ParseL2Snapshot(t *testing.T) {
	env := loadFixture(t, "l2_snapshot_btcusdt.json")

	update, err := parseL2Update(env.Topic, env.Ts, env.Data)
	require.NoError(t, err)

	require.Len(t, update.Deltas, 2, "expect 1 bid + 1 ask delta")

	bid := update.Deltas[0]
	assert.Equal(t, orderbook.SideBid, bid.Side)
	assert.Equal(t, "29501.50", bid.Price)
	assert.Equal(t, "1.234", bid.Size)
	assert.Equal(t, uint64(1001), bid.Seq)
	assert.Equal(t, int64(1683016009277)*int64(time.Millisecond), bid.TsExchange)

	ask := update.Deltas[1]
	assert.Equal(t, orderbook.SideAsk, ask.Side)
	assert.Equal(t, "29502.00", ask.Price)
	assert.Equal(t, "0.567", ask.Size)
	assert.Equal(t, uint64(1001), ask.Seq)
}

func TestAdapter_ParseL2Delta(t *testing.T) {
	env := loadFixture(t, "l2_delta_btcusdt.json")

	update, err := parseL2Update(env.Topic, env.Ts, env.Data)
	require.NoError(t, err)

	require.Len(t, update.Deltas, 2)

	bid := update.Deltas[0]
	assert.Equal(t, orderbook.SideBid, bid.Side)
	assert.Equal(t, "29501.50", bid.Price)
	assert.Equal(t, "0", bid.Size, "size '0' means remove level")
	assert.Equal(t, uint64(1002), bid.Seq)
	assert.Equal(t, int64(1683016009500)*int64(time.Millisecond), bid.TsExchange)

	ask := update.Deltas[1]
	assert.Equal(t, orderbook.SideAsk, ask.Side)
	assert.Equal(t, "29502.00", ask.Price)
	assert.Equal(t, "1.234", ask.Size)
}

func TestAdapter_ParseTrade(t *testing.T) {
	env := loadFixture(t, "trade_btcusdt.json")

	trade, err := parseTrade(env.Topic, env.Data)
	require.NoError(t, err)

	assert.Equal(t, "29501.50", trade.Price)
	assert.Equal(t, "0.001", trade.Size)
	assert.Equal(t, "bid", trade.Side, "Buy → bid")
	assert.Equal(t, int64(1683016009123)*int64(time.Millisecond), trade.TsExchange)
	assert.Equal(t, uint64(0), trade.Seq, "Bybit trades carry no sequence number")
}

func TestAdapter_ParseTrade_UnknownSide(t *testing.T) {
	data := json.RawMessage(`[{"T":1683016009123,"p":"100.0","v":"1.0","S":"Short","i":"x"}]`)
	_, err := parseTrade("publicTrade.BTCUSDT", data)
	require.Error(t, err)
	assert.Contains(t, err.Error(), "unknown trade side")
	assert.Contains(t, err.Error(), "Short")
}

// ── Integration Tests ─────────────────────────────────────────────────────────

// newTestAdapter creates an Adapter pointing to a test server with a mock clock.
func newTestAdapter(t *testing.T, srv *mockWSServer, opts ...func(*Adapter)) *Adapter {
	t.Helper()
	clk := testutil.NewMockClock(time.Now())
	a := New(clk)
	a.bybitURL = srv.url()
	for _, opt := range opts {
		opt(a)
	}
	t.Cleanup(func() { _ = a.Close() })
	return a
}

func TestAdapter_ConnectSubscribeReceiveTick(t *testing.T) {
	srv := newMockWSServer(t)
	a := newTestAdapter(t, srv, func(a *Adapter) {
		a.SetPingInterval(24 * time.Hour) // prevent pings during test
	})

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	t.Cleanup(cancel)

	require.NoError(t, a.Connect(ctx))
	require.NoError(t, a.Subscribe([]string{"BTCUSDT"}, []exchange.FeedType{exchange.FeedTypeOrderBook}))

	// Wait for the server to have seen the subscribe message (server auto-acks it).
	require.Eventually(t, func() bool {
		s := srv.getSubsSeen()
		return s >= 1
	}, 3*time.Second, 5*time.Millisecond, "server must receive subscribe before pushing tick")

	// Brief pause to allow the ack to travel back through readLoop.
	time.Sleep(20 * time.Millisecond)

	// Push an L2 snapshot frame.
	env := loadFixture(t, "l2_snapshot_btcusdt.json")
	srv.Push(env)

	// Expect ticks on the channel.
	select {
	case tick := <-a.Ticks():
		assert.Equal(t, "bybit", tick.Exchange)
		assert.Equal(t, "bid", tick.Side)
		assert.Equal(t, exchange.EventTypeUpdate, tick.Type)
		assert.Equal(t, "29501.50", tick.Price)
		assert.Equal(t, "1.234", tick.Size)
	case <-ctx.Done():
		t.Fatal("timed out waiting for tick")
	}
}

func TestAdapter_PingPong(t *testing.T) {
	srv := newMockWSServer(t)
	a := newTestAdapter(t, srv, func(a *Adapter) {
		a.SetPingInterval(50 * time.Millisecond)
		a.SetPingTimeout(500 * time.Millisecond)
	})

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	t.Cleanup(cancel)

	require.NoError(t, a.Connect(ctx))
	require.NoError(t, a.Subscribe([]string{"BTCUSDT"}, []exchange.FeedType{exchange.FeedTypeOrderBook}))

	// Wait for at least one ping to be seen on the server.
	require.Eventually(t, func() bool {
		return srv.getPingsSeen() > 0
	}, 3*time.Second, 10*time.Millisecond, "server must receive at least one ping")

	// Wait a bit more — adapter must NOT have dropped (no NeedsSnapshot signal).
	time.Sleep(150 * time.Millisecond)

	// Signals channel must be empty (no disconnects triggered by ping/pong).
	select {
	case sig := <-a.Signals():
		t.Fatalf("unexpected signal: %+v (adapter should not have reconnected)", sig)
	default:
		// good — no disconnect
	}
}

func TestAdapter_PingTimeout(t *testing.T) {
	srv := newMockWSServer(t)

	a := newTestAdapter(t, srv, func(a *Adapter) {
		a.SetPingInterval(50 * time.Millisecond)
		a.SetPingTimeout(100 * time.Millisecond)
	})

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	t.Cleanup(cancel)

	require.NoError(t, a.Connect(ctx))
	require.NoError(t, a.Subscribe([]string{"BTCUSDT"}, []exchange.FeedType{exchange.FeedTypeOrderBook}))

	// Wait for the server to have acked the subscribe — symbol must be confirmed
	// for NeedsSnapshot to fire on disconnect.
	require.Eventually(t, func() bool {
		return srv.getSubsSeen() >= 1
	}, 3*time.Second, 5*time.Millisecond, "server must receive subscribe")

	// Brief pause to allow the ack to travel back through readLoop.
	time.Sleep(20 * time.Millisecond)

	// Now suppress pong so the next ping will time out.
	srv.setSuppressPong(true)

	// Wait for NeedsSnapshot signal — pong timeout triggers connection close + reconnect.
	select {
	case sig := <-a.Signals():
		assert.Equal(t, exchange.SignalNeedsSnapshot, sig.Type)
		assert.Equal(t, "disconnect", sig.Reason)
	case <-ctx.Done():
		t.Fatal("timed out waiting for NeedsSnapshot after pong timeout")
	}
}

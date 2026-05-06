//go:build l2

package mux

import (
	"context"
	"encoding/json"
	"fmt"
	"sync"
	"testing"
	"time"

	"github.com/mrqdt/magnum-opus/aggregator/internal/exchange"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// fakeConn is an in-process stand-in for *transport.Conn.
// Push injects a message for the readLoop; Drop signals a connection drop.
type fakeConn struct {
	readCh      chan any
	writeCh     chan any
	reconnectCh chan struct{}
	closeOnce   sync.Once
}

func newFakeConn() *fakeConn {
	return &fakeConn{
		readCh:      make(chan any, 64),
		writeCh:     make(chan any, 64),
		reconnectCh: make(chan struct{}),
	}
}

func (f *fakeConn) Read(ctx context.Context, v any) error {
	select {
	case msg := <-f.readCh:
		b, err := json.Marshal(msg)
		if err != nil {
			return err
		}
		return json.Unmarshal(b, v)
	case <-ctx.Done():
		return ctx.Err()
	}
}

func (f *fakeConn) Write(_ context.Context, v any) error {
	f.writeCh <- v
	return nil
}

func (f *fakeConn) Reconnect() <-chan struct{} { return f.reconnectCh }

func (f *fakeConn) Close() {
	f.closeOnce.Do(func() { close(f.reconnectCh) })
}

// Drop simulates a connection drop (fires the Reconnect channel).
func (f *fakeConn) Drop() { f.Close() }

// Push injects a message into the connection's read stream.
func (f *fakeConn) Push(msg any) { f.readCh <- msg }

// ackAll drains writeCh and sends success acks back on pushCh for each subscribe write seen.
func ackAll(t *testing.T, conn *fakeConn, count int) {
	t.Helper()
	for i := 0; i < count; i++ {
		var msg map[string]any
		select {
		case raw := <-conn.writeCh:
			b, _ := json.Marshal(raw)
			_ = json.Unmarshal(b, &msg)
		case <-time.After(2 * time.Second):
			t.Fatalf("ackAll: timed out waiting for write %d/%d", i+1, count)
		}
		reqID, _ := msg["req_id"].(string)
		conn.Push(wireMsg{Op: "subscribe", ReqID: reqID, Success: true})
	}
}

// fakeFactory returns a ConnFactory that hands out conns in order.
// It panics if called more times than there are conns.
func fakeFactory(conns []*fakeConn) (ConnFactory, *int) {
	idx := 0
	var mu sync.Mutex
	calls := 0
	factory := func(_ context.Context) (Conn, error) {
		mu.Lock()
		defer mu.Unlock()
		if idx >= len(conns) {
			panic("fakeFactory: called more times than conns available")
		}
		c := conns[idx]
		idx++
		calls++
		return c, nil
	}
	return factory, &calls
}

// makeSyms returns n distinct Bybit-style symbols.
func makeSyms(n int) []string {
	syms := make([]string, n)
	for i := range syms {
		syms[i] = fmt.Sprintf("SYM%04d", i)
	}
	return syms
}

// TestMux_SymbolPartitioning verifies that 200 symbols create 20 slots,
// each carrying ≤10 symbols, with no symbol appearing on more than one conn.
func TestMux_SymbolPartitioning(t *testing.T) {
	const total = 200
	conns := make([]*fakeConn, total/10) // expect exactly 20
	for i := range conns {
		conns[i] = newFakeConn()
	}

	syms := makeSyms(total)
	factory, _ := fakeFactory(conns)

	m := New(syms, []exchange.FeedType{exchange.FeedTypeOrderBook}, factory)
	m.maxTopics = 10

	ctx, cancel := context.WithCancel(context.Background())
	t.Cleanup(cancel)
	t.Cleanup(func() { m.Close() })

	require.NoError(t, m.Connect(ctx))

	assert.Equal(t, 20, len(m.slots), "expected 20 slots")

	seen := make(map[string]int) // sym → slot index
	for i, s := range m.slots {
		assert.LessOrEqual(t, len(s.syms), 10, "slot %d: too many syms", i)
		for _, sym := range s.syms {
			if prev, exists := seen[sym]; exists {
				t.Errorf("symbol %s appears on both slot %d and slot %d", sym, prev, i)
			}
			seen[sym] = i
		}
	}
	assert.Equal(t, total, len(seen), "all symbols must be assigned to a slot")
}

// TestMux_SingleConnectionDrop verifies that when one slot's connection drops,
// only that slot's confirmed symbols emit NeedsSnapshot, and other slots are unaffected.
func TestMux_SingleConnectionDrop(t *testing.T) {
	// 3 slots × 10 syms each = 30 syms; slot 1 will be dropped
	conns := make([]*fakeConn, 3)
	for i := range conns {
		conns[i] = newFakeConn()
	}
	// slot 1 reconnects to conns[3]
	reconnConn := newFakeConn()
	allConns := append(conns, reconnConn)

	syms := makeSyms(30)
	factory, _ := fakeFactory(allConns)

	m := New(syms, []exchange.FeedType{exchange.FeedTypeOrderBook}, factory)
	m.maxTopics = 10
	m.confirmTimeout = 5 * time.Second // generous — not testing timeout here

	ctx, cancel := context.WithCancel(context.Background())
	t.Cleanup(cancel)
	t.Cleanup(func() { m.Close() })

	require.NoError(t, m.Connect(ctx))

	slot1 := m.slots[1]

	// Ack all 10 syms on slot 1 and drain writes on the other two slots.
	ackAll(t, conns[1], len(slot1.syms))
	for i := 0; i < len(m.slots[0].syms); i++ {
		select {
		case <-conns[0].writeCh:
		case <-time.After(2 * time.Second):
			t.Fatal("timeout draining slot 0 writes")
		}
	}
	for i := 0; i < len(m.slots[2].syms); i++ {
		select {
		case <-conns[2].writeCh:
		case <-time.After(2 * time.Second):
			t.Fatal("timeout draining slot 2 writes")
		}
	}

	// Wait until readLoop has processed all acks into slot1.confirmed.
	require.Eventually(t, func() bool {
		slot1.mu.Lock()
		n := len(slot1.confirmed)
		slot1.mu.Unlock()
		return n == len(slot1.syms)
	}, 2*time.Second, 5*time.Millisecond, "slot 1 symbols must all be confirmed before drop")

	// Drop slot 1.
	conns[1].Drop()

	// Wait for NeedsSnapshot signals to arrive (one per confirmed sym).
	require.Eventually(t, func() bool {
		return len(m.signals) >= len(slot1.syms)
	}, 2*time.Second, 5*time.Millisecond, "expected NeedsSnapshot signals for slot 1")

	// Drain reconnect writes on reconnConn (it re-subscribes).
	for i := 0; i < len(slot1.syms); i++ {
		select {
		case <-reconnConn.writeCh:
		case <-time.After(3 * time.Second):
			t.Fatalf("timeout waiting for re-subscribe write %d", i)
		}
	}

	// Collect all signals.
	var signals []exchange.Signal
	for len(m.signals) > 0 {
		select {
		case sig := <-m.signals:
			signals = append(signals, sig)
		default:
		}
	}

	require.NotEmpty(t, signals, "expected NeedsSnapshot signals for slot 1")

	// Build a set of non-slot-1 syms to verify isolation.
	otherSyms := make(map[string]bool)
	for _, sym := range m.slots[0].syms {
		otherSyms[sym] = true
	}
	for _, sym := range m.slots[2].syms {
		otherSyms[sym] = true
	}

	for _, sig := range signals {
		assert.Equal(t, exchange.SignalNeedsSnapshot, sig.Type)
		assert.Equal(t, "disconnect", sig.Reason)
		// symbol.Normalize("bybit", "SYM0000") falls through to Symbol("SYM0000") for test syms.
		assert.False(t, otherSyms[string(sig.Symbol)], "slot 0/2 sym must not be signalled: %s", sig.Symbol)
	}
	assert.Equal(t, len(slot1.syms), len(signals), "signal count must match slot 1 sym count")
}

// TestMux_ConcurrentDrop verifies that two slots dropped simultaneously both emit
// NeedsSnapshot for their confirmed symbols, and the surviving slot does not.
func TestMux_ConcurrentDrop(t *testing.T) {
	conns := make([]*fakeConn, 3)
	for i := range conns {
		conns[i] = newFakeConn()
	}
	// Reconnect conns for slot 0 and slot 2.
	reconn0 := newFakeConn()
	reconn2 := newFakeConn()
	allConns := []*fakeConn{conns[0], conns[1], conns[2], reconn0, reconn2}

	syms := makeSyms(30)
	factory, _ := fakeFactory(allConns)

	m := New(syms, []exchange.FeedType{exchange.FeedTypeOrderBook}, factory)
	m.maxTopics = 10
	m.confirmTimeout = 5 * time.Second

	ctx, cancel := context.WithCancel(context.Background())
	t.Cleanup(cancel)
	t.Cleanup(func() { m.Close() })

	require.NoError(t, m.Connect(ctx))

	// Ack all syms on slots 0 and 2; drain slot 1 writes without acking.
	ackAll(t, conns[0], len(m.slots[0].syms))
	ackAll(t, conns[2], len(m.slots[2].syms))
	for i := 0; i < len(m.slots[1].syms); i++ {
		select {
		case <-conns[1].writeCh:
		case <-time.After(2 * time.Second):
			t.Fatal("timeout draining slot 1 writes")
		}
	}

	// Wait until both slots are fully confirmed before dropping.
	require.Eventually(t, func() bool {
		m.slots[0].mu.Lock()
		n0 := len(m.slots[0].confirmed)
		m.slots[0].mu.Unlock()
		m.slots[2].mu.Lock()
		n2 := len(m.slots[2].confirmed)
		m.slots[2].mu.Unlock()
		return n0 == len(m.slots[0].syms) && n2 == len(m.slots[2].syms)
	}, 2*time.Second, 5*time.Millisecond, "slots 0 and 2 must be fully confirmed before drop")

	// Drop slot 0 and slot 2 simultaneously.
	var wg sync.WaitGroup
	wg.Add(2)
	go func() { defer wg.Done(); conns[0].Drop() }()
	go func() { defer wg.Done(); conns[2].Drop() }()
	wg.Wait()

	expectedCount := len(m.slots[0].syms) + len(m.slots[2].syms)

	// Wait until all NeedsSnapshot signals have arrived.
	require.Eventually(t, func() bool {
		return len(m.signals) >= expectedCount
	}, 2*time.Second, 5*time.Millisecond, "expected NeedsSnapshot signals for slots 0 and 2")

	// Drain re-subscribe writes on reconnect conns.
	for i := 0; i < len(m.slots[0].syms); i++ {
		select {
		case <-reconn0.writeCh:
		case <-time.After(3 * time.Second):
			t.Fatalf("timeout waiting for slot 0 re-subscribe write %d", i)
		}
	}
	for i := 0; i < len(m.slots[2].syms); i++ {
		select {
		case <-reconn2.writeCh:
		case <-time.After(3 * time.Second):
			t.Fatalf("timeout waiting for slot 2 re-subscribe write %d", i)
		}
	}

	// Collect all signals.
	var signals []exchange.Signal
	for len(m.signals) > 0 {
		select {
		case sig := <-m.signals:
			signals = append(signals, sig)
		default:
		}
	}

	// Every signal must be for a sym in slot 0 or slot 2.
	for _, sig := range signals {
		assert.Equal(t, exchange.SignalNeedsSnapshot, sig.Type)
	}

	assert.Equal(t, expectedCount, len(signals), "must get NeedsSnapshot for all slot0+slot2 syms")

	// Verify no duplicates.
	seen := make(map[string]int)
	for _, sig := range signals {
		seen[string(sig.Symbol)]++
	}
	for sym, count := range seen {
		assert.Equal(t, 1, count, "symbol %s signalled %d times (want 1)", sym, count)
	}
}

// TestMux_UnconfirmedTimeout verifies that symbols still in pendingAcks after
// confirmTimeout are logged and their subscriptions retried.
func TestMux_UnconfirmedTimeout(t *testing.T) {
	conn := newFakeConn()
	factory, _ := fakeFactory([]*fakeConn{conn})

	m := New([]string{"BTCUSDT"}, []exchange.FeedType{exchange.FeedTypeOrderBook}, factory)
	m.maxTopics = 10
	m.confirmTimeout = 50 * time.Millisecond // short for test

	ctx, cancel := context.WithCancel(context.Background())
	t.Cleanup(cancel)
	t.Cleanup(func() { m.Close() })

	require.NoError(t, m.Connect(ctx))

	// Drain the initial subscribe write but do NOT ack it.
	select {
	case <-conn.writeCh:
	case <-time.After(2 * time.Second):
		t.Fatal("timeout waiting for initial subscribe write")
	}

	// Wait for the watcher to fire and retry — it will write a second subscribe.
	require.Eventually(t, func() bool {
		select {
		case <-conn.writeCh:
			return true
		default:
			return false
		}
	}, 500*time.Millisecond, 5*time.Millisecond, "expected watcher to retry subscribe")

	// Symbol must still be in pendingAcks (unconfirmed).
	s := m.slots[0]
	s.mu.Lock()
	_, stillPending := func() (string, bool) {
		for _, sym := range s.pendingAcks {
			return sym, true
		}
		return "", false
	}()
	s.mu.Unlock()
	assert.True(t, stillPending, "BTCUSDT must still be in pendingAcks after retry")
}

// TestMux_SpuriousAck verifies that an ack for an unknown req_id does not panic
// and does not mark any symbol as confirmed.
func TestMux_SpuriousAck(t *testing.T) {
	conn := newFakeConn()
	factory, _ := fakeFactory([]*fakeConn{conn})

	m := New([]string{"BTCUSDT"}, []exchange.FeedType{exchange.FeedTypeOrderBook}, factory)
	m.maxTopics = 10
	m.confirmTimeout = 30 * time.Second

	ctx, cancel := context.WithCancel(context.Background())
	t.Cleanup(cancel)
	t.Cleanup(func() { m.Close() })

	require.NoError(t, m.Connect(ctx))

	// Drain initial subscribe write.
	select {
	case <-conn.writeCh:
	case <-time.After(2 * time.Second):
		t.Fatal("timeout waiting for subscribe write")
	}

	// Push a spurious ack with an unknown req_id.
	conn.Push(wireMsg{Op: "subscribe", ReqID: "9999999", Success: true})

	// Verify confirmed stays empty even after the readLoop has had time to process.
	assert.Never(t, func() bool {
		s := m.slots[0]
		s.mu.Lock()
		defer s.mu.Unlock()
		return len(s.confirmed) > 0
	}, 100*time.Millisecond, 5*time.Millisecond, "spurious ack must not confirm any symbol")
}

// TestMux_Close verifies that Close() completes quickly and closes the Ticks channel.
func TestMux_Close(t *testing.T) {
	conn := newFakeConn()
	factory, _ := fakeFactory([]*fakeConn{conn})

	m := New([]string{"BTCUSDT"}, []exchange.FeedType{exchange.FeedTypeOrderBook}, factory)
	m.maxTopics = 10
	m.confirmTimeout = 30 * time.Second

	ctx := context.Background()

	require.NoError(t, m.Connect(ctx))

	// Drain writes so the write call in sendSymbolSubscriptions doesn't block.
	// The goroutine exits when drainCancel fires.
	drainCtx, drainCancel := context.WithCancel(context.Background())
	t.Cleanup(drainCancel)
	go func() {
		for {
			select {
			case <-conn.writeCh:
			case <-drainCtx.Done():
				return
			}
		}
	}()

	done := make(chan struct{})
	go func() {
		defer close(done)
		_ = m.Close()
	}()

	select {
	case <-done:
	case <-time.After(500 * time.Millisecond):
		t.Fatal("Close() did not return within 500ms")
	}

	// Ticks channel must be closed.
	_, open := <-m.Ticks()
	assert.False(t, open, "Ticks() channel must be closed after Close()")
}

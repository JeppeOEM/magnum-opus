// Package mux distributes Bybit symbol subscriptions across multiple WebSocket
// connections to respect the exchange's 10-topics-per-connection limit.
// It is a pure routing concern — connections are provided by an injected ConnFactory;
// no live WebSocket dialing occurs inside this package.
package mux

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"sync"
	"sync/atomic"
	"time"

	"github.com/mrqdt/magnum-opus/aggregator/internal/exchange"
	"github.com/mrqdt/magnum-opus/aggregator/internal/symbol"
)

const (
	defaultMaxTopics     = 10
	defaultConfirmTimeout = 30 * time.Second
	tickBuf              = 4096
	signalBuf            = 256
)

// Conn is the minimal interface required of each slot's WebSocket connection.
// *transport.Conn satisfies this interface via structural typing.
// The interface is defined here (consuming package) so tests can inject fakes
// without importing exchange/transport.
type Conn interface {
	Read(ctx context.Context, v any) error
	Write(ctx context.Context, v any) error
	Reconnect() <-chan struct{}
	Close()
}

// ConnFactory is called once per slot on Connect and once per slot on each reconnect.
// The URL and dial options are captured in the closure by the caller (Story 2.5).
type ConnFactory func(ctx context.Context) (Conn, error)

// msgIDCounter generates unique request IDs scoped to this process.
var msgIDCounter atomic.Int64

func nextMsgID() string {
	n := msgIDCounter.Add(1)
	return fmt.Sprintf("%d", n)
}

// MsgCallback is invoked by the mux for each incoming market data frame.
// topic is the Bybit topic string (e.g. "orderbook.1.BTCUSDT"),
// ts is the outer millisecond timestamp from the wire envelope,
// msgType is "snapshot" or "delta", and data is the raw inner payload.
type MsgCallback func(topic string, ts int64, msgType string, data json.RawMessage)

// wireMsg is the subset of Bybit control-plane message fields the mux needs.
type wireMsg struct {
	Op      string          `json:"op"`
	ReqID   string          `json:"req_id"`
	Success bool            `json:"success"`
	RetMsg  string          `json:"ret_msg"`
	Data    json.RawMessage `json:"data,omitempty"`
	// market data envelope fields
	Topic string `json:"topic,omitempty"`
	Type  string `json:"type,omitempty"`
	Ts    int64  `json:"ts,omitempty"`
}

// slot holds one WebSocket connection and the symbols assigned to it.
type slot struct {
	idx  int
	syms []string // raw Bybit symbols; immutable after assignment

	connMu sync.Mutex
	conn   Conn // replaced atomically on reconnect

	mu          sync.Mutex
	confirmed   map[string]bool   // raw sym → confirmed on current connection
	pendingAcks map[string]string // reqID → raw sym

	confirmTimeout time.Duration

	pongCh chan struct{} // receives signal when pong arrives; buffered 1
}

func newSlot(idx int, syms []string, conn Conn, timeout time.Duration) *slot {
	return &slot{
		idx:            idx,
		syms:           syms,
		conn:           conn,
		confirmed:      make(map[string]bool),
		pendingAcks:    make(map[string]string),
		confirmTimeout: timeout,
		pongCh:         make(chan struct{}, 1),
	}
}

func (s *slot) getConn() Conn {
	s.connMu.Lock()
	defer s.connMu.Unlock()
	return s.conn
}

func (s *slot) setConn(c Conn) {
	s.connMu.Lock()
	s.conn = c
	s.connMu.Unlock()
}

func (s *slot) resetAcks() {
	s.mu.Lock()
	s.confirmed = make(map[string]bool)
	s.pendingAcks = make(map[string]string)
	s.mu.Unlock()
}

// Mux distributes symbol subscriptions across N WebSocket connections,
// each carrying at most maxTopics symbols.
type Mux struct {
	syms      []string
	feeds     []exchange.FeedType
	maxTopics int
	factory   ConnFactory

	slots []*slot

	ticks   chan exchange.Tick
	signals chan exchange.Signal

	cancel    context.CancelFunc
	wg        sync.WaitGroup
	closeOnce sync.Once

	confirmTimeout time.Duration

	pingInterval time.Duration
	pingTimeout  time.Duration
	onMsg        MsgCallback
}

// New creates a Mux. factory is called once per slot when Connect is called
// and once per slot on each reconnect. Connections are NOT dialled here.
func New(syms []string, feeds []exchange.FeedType, factory ConnFactory) *Mux {
	return &Mux{
		syms:           syms,
		feeds:          feeds,
		maxTopics:      defaultMaxTopics,
		factory:        factory,
		ticks:          make(chan exchange.Tick, tickBuf),
		signals:        make(chan exchange.Signal, signalBuf),
		confirmTimeout: defaultConfirmTimeout,
		pingInterval:   10 * time.Second,
		pingTimeout:    20 * time.Second,
	}
}

// WithMsgCallback registers a callback that is invoked for each incoming market
// data frame. Call before Connect. Not safe for concurrent use.
func (m *Mux) WithMsgCallback(fn MsgCallback) {
	m.onMsg = fn
}

// WithPingInterval overrides the default 10-second application-level ping interval.
// Call before Connect. Primarily used in tests to shorten the interval.
func (m *Mux) WithPingInterval(d time.Duration) {
	m.pingInterval = d
}

// WithPingTimeout overrides the default 20-second pong wait timeout.
// Call before Connect. Primarily used in tests.
func (m *Mux) WithPingTimeout(d time.Duration) {
	m.pingTimeout = d
}

// SendTick delivers a tick to the output channel.
// Non-blocking: drops with a warning if the channel is full.
func (m *Mux) SendTick(t exchange.Tick) {
	select {
	case m.ticks <- t:
	default:
		slog.Warn("bybit mux: ticks channel full, dropping tick", "sym", t.Symbol)
	}
}

func (m *Mux) Ticks() <-chan exchange.Tick     { return m.ticks }
func (m *Mux) Signals() <-chan exchange.Signal { return m.signals }

// Connect partitions symbols into slots, dials all connections via factory,
// starts per-slot goroutines, and sends initial subscriptions.
func (m *Mux) Connect(ctx context.Context) error {
	nSlots := (len(m.syms) + m.maxTopics - 1) / m.maxTopics
	if nSlots == 0 {
		nSlots = 1
	}

	adapterCtx, cancel := context.WithCancel(ctx)
	m.cancel = cancel

	m.slots = make([]*slot, nSlots)
	for i := range m.slots {
		start := i * m.maxTopics
		end := start + m.maxTopics
		if end > len(m.syms) {
			end = len(m.syms)
		}
		slotSyms := make([]string, end-start)
		copy(slotSyms, m.syms[start:end])

		conn, err := m.factory(adapterCtx)
		if err != nil {
			// Close already-opened connections and cancel.
			for j := 0; j < i; j++ {
				m.slots[j].getConn().Close()
			}
			cancel()
			return fmt.Errorf("mux: dial slot %d: %w", i, err)
		}
		m.slots[i] = newSlot(i, slotSyms, conn, m.confirmTimeout)
	}

	// Start per-slot goroutines.
	for _, s := range m.slots {
		s := s
		m.wg.Add(2)
		go func() {
			defer m.wg.Done()
			m.runSlot(adapterCtx, s)
		}()
		go func() {
			defer m.wg.Done()
			m.confirmWatcher(adapterCtx, s)
		}()
	}

	// Send initial subscriptions for all slots.
	if err := m.sendSubscriptions(adapterCtx); err != nil {
		cancel()
		m.wg.Wait()
		return fmt.Errorf("mux: initial subscribe: %w", err)
	}

	return nil
}

// Close cancels the adapter context, waits for all goroutines, then closes channels.
// Safe to call more than once.
func (m *Mux) Close() error {
	if m.cancel != nil {
		m.cancel()
	}
	m.wg.Wait()
	m.closeOnce.Do(func() {
		close(m.ticks)
		close(m.signals)
	})
	return nil
}

// runSlot manages one slot's connection lifecycle: it watches for reconnect
// events and handles re-dial + re-subscribe when the connection drops.
func (m *Mux) runSlot(ctx context.Context, s *slot) {
	for {
		conn := s.getConn()

		connCtx, connCancel := context.WithCancel(ctx)
		var inner sync.WaitGroup
		inner.Add(2)
		innerDone := make(chan struct{})
		go func() {
			defer inner.Done()
			m.readLoop(connCtx, s)
		}()
		go func() {
			defer inner.Done()
			m.pingLoop(connCtx, s)
		}()
		go func() { inner.Wait(); close(innerDone) }()

		select {
		case <-ctx.Done():
			connCancel()
			<-innerDone
			conn.Close()
			return

		case <-conn.Reconnect():
			slog.Info("bybit mux: connection dropped", "slot", s.idx)
			connCancel()
			<-innerDone
			conn.Close()

		case <-innerDone:
			// readLoop or pingLoop exited without a Reconnect() signal —
			// e.g. application-level pong timeout closed the connection.
			if ctx.Err() != nil {
				connCancel()
				return
			}
			connCancel()
			conn.Close()
			slog.Info("bybit mux: connection goroutines exited, reconnecting", "slot", s.idx)
		}

		// Emit NeedsSnapshot for all confirmed symbols on this slot.
		m.emitNeedsSnapshot(s)
		s.resetAcks()

		// Reconnect with exponential backoff.
		var newConn Conn
		for attempt := 0; ; attempt++ {
			if ctx.Err() != nil {
				return
			}
			c, err := m.factory(ctx)
			if err != nil {
				slog.Error("bybit mux: reconnect factory failed", "slot", s.idx, "attempt", attempt, "error", err)
				select {
				case <-time.After(time.Second):
				case <-ctx.Done():
					return
				}
				continue
			}
			newConn = c
			break
		}

		s.setConn(newConn)

		if err := m.sendSlotSubscriptions(ctx, s); err != nil {
			slog.Error("bybit mux: re-subscribe after reconnect", "slot", s.idx, "error", err)
		}
	}
}

// readLoop reads messages from a slot's connection and dispatches them.
func (m *Mux) readLoop(ctx context.Context, s *slot) {
	conn := s.getConn()
	for {
		var msg wireMsg
		if err := conn.Read(ctx, &msg); err != nil {
			if ctx.Err() != nil {
				return
			}
			slog.Debug("bybit mux: read error", "slot", s.idx, "error", err)
			return
		}
		m.dispatch(s, msg)
	}
}

func (m *Mux) dispatch(s *slot, msg wireMsg) {
	switch msg.Op {
	case "subscribe":
		if msg.Success {
			m.handleAck(s, msg.ReqID)
		} else {
			slog.Warn("bybit mux: subscribe nack", "slot", s.idx, "req_id", msg.ReqID, "ret_msg", msg.RetMsg)
		}
	case "pong":
		select {
		case s.pongCh <- struct{}{}:
		default:
		}
	default:
		if msg.Topic != "" && m.onMsg != nil {
			m.onMsg(msg.Topic, msg.Ts, msg.Type, msg.Data)
		}
	}
}

func (m *Mux) handleAck(s *slot, reqID string) {
	s.mu.Lock()
	sym, ok := s.pendingAcks[reqID]
	if !ok {
		s.mu.Unlock()
		slog.Warn("bybit mux: spurious ack for unknown req_id", "slot", s.idx, "req_id", reqID)
		return
	}
	s.confirmed[sym] = true
	delete(s.pendingAcks, reqID)
	s.mu.Unlock()
}

// confirmWatcher runs for the mux lifetime, periodically checking for
// symbols that have not been confirmed within confirmTimeout.
func (m *Mux) confirmWatcher(ctx context.Context, s *slot) {
	timer := time.NewTimer(s.confirmTimeout)
	defer timer.Stop()

	for {
		select {
		case <-ctx.Done():
			return
		case <-timer.C:
		}

		s.mu.Lock()
		var unconfirmed []string
		for _, sym := range s.syms {
			if findReqForSym(s, sym) != "" {
				unconfirmed = append(unconfirmed, sym)
			}
		}
		s.mu.Unlock()

		if len(unconfirmed) > 0 {
			sample := unconfirmed
			if len(sample) > 5 {
				sample = sample[:5]
			}
			slog.Error("bybit mux: symbols unconfirmed after timeout, retrying",
				"slot", s.idx, "count", len(unconfirmed), "sample", sample)
			if err := m.sendSymbolSubscriptions(ctx, s, unconfirmed); err != nil {
				slog.Error("bybit mux: confirm retry failed", "slot", s.idx, "error", err)
			}
		}

		timer.Reset(s.confirmTimeout)
	}
}

// findReqForSym returns the reqID for a given sym currently in pendingAcks.
// Caller must hold s.mu.
func findReqForSym(s *slot, sym string) string {
	for reqID, pendingSym := range s.pendingAcks {
		if pendingSym == sym {
			return reqID
		}
	}
	return ""
}

// emitNeedsSnapshot sends SignalNeedsSnapshot for all confirmed symbols on a slot.
func (m *Mux) emitNeedsSnapshot(s *slot) {
	s.mu.Lock()
	var confirmed []string
	for sym := range s.confirmed {
		confirmed = append(confirmed, sym)
	}
	s.mu.Unlock()

	for _, raw := range confirmed {
		sig := exchange.Signal{
			Symbol: symbol.Normalize("bybit", raw),
			Type:   exchange.SignalNeedsSnapshot,
			Reason: "disconnect",
		}
		select {
		case m.signals <- sig:
		default:
			slog.Warn("bybit mux: signals channel full, dropping", "sym", raw)
		}
	}
}

// sendSubscriptions sends subscriptions for all slots.
func (m *Mux) sendSubscriptions(ctx context.Context) error {
	for _, s := range m.slots {
		if err := m.sendSlotSubscriptions(ctx, s); err != nil {
			return err
		}
	}
	return nil
}

// sendSlotSubscriptions subscribes all symbols on a slot across all feeds.
func (m *Mux) sendSlotSubscriptions(ctx context.Context, s *slot) error {
	return m.sendSymbolSubscriptions(ctx, s, s.syms)
}

// sendSymbolSubscriptions sends one subscribe message per symbol per feed.
func (m *Mux) sendSymbolSubscriptions(ctx context.Context, s *slot, syms []string) error {
	conn := s.getConn()
	for _, feed := range m.feeds {
		prefix := topicPrefix(feed)
		if prefix == "" {
			continue
		}
		for _, sym := range syms {
			reqID := nextMsgID()
			topic := prefix + sym
			msg := map[string]any{
				"op":     "subscribe",
				"req_id": reqID,
				"args":   []string{topic},
			}
			s.mu.Lock()
			// Clear any stale reqIDs for this sym from previous retry cycles.
			for id, pendingSym := range s.pendingAcks {
				if pendingSym == sym {
					delete(s.pendingAcks, id)
				}
			}
			s.pendingAcks[reqID] = sym
			s.mu.Unlock()

			if err := conn.Write(ctx, msg); err != nil {
				s.mu.Lock()
				delete(s.pendingAcks, reqID)
				s.mu.Unlock()
				return fmt.Errorf("mux: slot %d write subscribe: %w", s.idx, err)
			}
		}
	}
	return nil
}

// pingLoop sends application-level pings on a fixed interval and waits for pong.
// If no pong arrives within pingTimeout, it closes the connection (triggering
// runSlot to reconnect via the innerDone path). Exits when ctx is cancelled.
func (m *Mux) pingLoop(ctx context.Context, s *slot) {
	ticker := time.NewTicker(m.pingInterval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}

		conn := s.getConn()
		if err := conn.Write(ctx, map[string]string{"op": "ping"}); err != nil {
			// Write failed — close the connection so readLoop also exits,
			// allowing runSlot to reconnect.
			conn.Close()
			return
		}

		pongTimer := time.NewTimer(m.pingTimeout)
		select {
		case <-ctx.Done():
			pongTimer.Stop()
			return
		case <-s.pongCh:
			pongTimer.Stop()
			// pong received; continue to next ping interval
		case <-pongTimer.C:
			slog.Warn("bybit mux: pong timeout, closing connection", "slot", s.idx)
			s.getConn().Close()
			return
		}
	}
}

// topicPrefix maps a FeedType to the Bybit topic prefix string.
func topicPrefix(f exchange.FeedType) string {
	switch f {
	case exchange.FeedTypeOrderBook:
		return "orderbook.1."
	case exchange.FeedTypeTrade:
		return "publicTrade."
	default:
		return ""
	}
}


package kucoin

import (
	"context"
	"fmt"
	"log/slog"
	"net/http"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/mrqdt/magnum-opus/aggregator/internal/config"
	"github.com/mrqdt/magnum-opus/aggregator/internal/exchange"
	"github.com/mrqdt/magnum-opus/aggregator/internal/exchange/transport"
	"github.com/mrqdt/magnum-opus/aggregator/internal/orderbook"
	"github.com/mrqdt/magnum-opus/aggregator/internal/symbol"
)

// Compile-time proof that Adapter satisfies the Exchange interface.
var _ exchange.Exchange = (*Adapter)(nil)

const (
	subBatchSize = 100
	tickBuf      = 4096
	signalBuf    = 256
)

// Clock abstracts time.Now() so the adapter can be driven deterministically in tests.
// The composition root supplies a real wall-clock implementation; tests supply a MockClock.
type Clock interface {
	Now() time.Time
}

// msgIDCounter produces unique message IDs scoped to the current process.
var msgIDCounter atomic.Int64

func nextMsgID() string {
	n := msgIDCounter.Add(1)
	var buf [20]byte
	pos := len(buf)
	for n >= 10 {
		pos--
		buf[pos] = byte('0' + n%10)
		n /= 10
	}
	pos--
	buf[pos] = byte('0' + n)
	return string(buf[pos:])
}

// Adapter implements exchange.Exchange for KuCoin.
// A single WebSocket connection serves all subscribed symbols.
type Adapter struct {
	name       string
	apiBase    string // overridden in tests; defaults to defaultAPIBase
	cfg        config.KuCoinConfig
	httpClient *http.Client

	ticks   chan exchange.Tick
	signals chan exchange.Signal

	// subscription targets — written by Subscribe, read by runLoop on reconnect
	subMu   sync.Mutex
	rawSyms []string
	feeds   []exchange.FeedType

	// ack tracking — reset on each (re)connect
	ackMu       sync.Mutex
	pendingAcks map[string][]string // msgID → raw symbols in that sub request
	confirmed   map[string]bool     // raw symbol → confirmed for current connection

	// pending pings — matched by ID when pong arrives
	pingsMu     sync.Mutex
	pendingPings map[string]chan struct{}

	// reconnect trigger written by heartbeatLoop; runLoop reads it
	reconnectTrigger chan struct{} // buffered 1

	// current connection — protected by connMu; written by runLoop and Connect
	connMu sync.Mutex
	conn   *transport.Conn

	// token — protected by tokMu; updated by auto-renewal (Story 2.3)
	tokMu sync.RWMutex
	tok   tokenData

	// adapterCtx is the ctx from Connect() stored so Subscribe() (no ctx param) can write
	adapterCtx context.Context

	// confirmTimeout is how long confirmWatcher waits before logging unconfirmed symbols.
	// Defaults to 30s; override in tests.
	confirmTimeout time.Duration

	clk    Clock
	cancel context.CancelFunc
	wg     sync.WaitGroup // tracks runLoop and tokenRenewalLoop

	// token renewal configuration — overridable in tests
	renewalCheckInterval time.Duration
	renewalLeadTime      time.Duration
	renewalMaxAttempts   int
}

// New returns a KuCoin adapter. clk must not be nil; the composition root supplies
// a real wall-clock and tests supply a testutil.MockClock.
// httpClient may be nil (http.DefaultClient is used).
func New(cfg config.KuCoinConfig, httpClient *http.Client, clk Clock) *Adapter {
	if httpClient == nil {
		httpClient = http.DefaultClient
	}
	if clk == nil {
		panic("kucoin.New: clk must not be nil")
	}
	return &Adapter{
		name:             "kucoin",
		apiBase:          defaultAPIBase,
		cfg:              cfg,
		httpClient:       httpClient,
		ticks:            make(chan exchange.Tick, tickBuf),
		signals:          make(chan exchange.Signal, signalBuf),
		pendingAcks:      make(map[string][]string),
		confirmed:        make(map[string]bool),
		pendingPings:     make(map[string]chan struct{}),
		reconnectTrigger: make(chan struct{}, 1),
		confirmTimeout:       30 * time.Second,
		renewalCheckInterval: 1 * time.Minute,
		renewalLeadTime:      30 * time.Minute,
		renewalMaxAttempts:   5,
		clk:                  clk,
	}
}

// newWithAPIBase creates an Adapter that overrides the KuCoin REST API base URL.
// Used in tests to point at a local mock server instead of api.kucoin.com.
func newWithAPIBase(cfg config.KuCoinConfig, httpClient *http.Client, apiBase string, clk Clock) *Adapter {
	a := New(cfg, httpClient, clk)
	a.apiBase = apiBase
	return a
}

func (a *Adapter) Name() string                   { return a.name }
func (a *Adapter) Ticks() <-chan exchange.Tick     { return a.ticks }
func (a *Adapter) Signals() <-chan exchange.Signal { return a.signals }

// Connect fetches a KuCoin WebSocket token, dials the connection, and starts
// background goroutines. ctx controls the full adapter lifetime.
func (a *Adapter) Connect(ctx context.Context) error {
	tok, err := fetchToken(ctx, a.httpClient, a.apiBase, a.cfg, a.clk)
	if err != nil {
		return fmt.Errorf("kucoin: fetch token: %w", err)
	}

	a.tokMu.Lock()
	a.tok = tok
	a.tokMu.Unlock()

	conn, err := transport.Dial(ctx, tok.wsURL(), transport.Options{
		// KuCoin uses application-level JSON ping/pong, not WebSocket frame pings.
		// Set a very long transport interval so it acts only as an emergency backup.
		PingInterval: 4 * time.Hour,
		PongTimeout:  5 * time.Second,
	})
	if err != nil {
		return fmt.Errorf("kucoin: dial: %w", err)
	}

	adapterCtx, cancel := context.WithCancel(ctx)
	a.cancel = cancel
	a.adapterCtx = adapterCtx

	a.connMu.Lock()
	a.conn = conn
	a.connMu.Unlock()

	// Reset acks before starting the goroutine so Subscribe() called immediately
	// after Connect() cannot race with resetAcks() inside runLoop.
	a.resetAcks()

	a.wg.Add(1)
	go a.runLoop(adapterCtx, conn, tok)
	a.wg.Add(1)
	go func() { defer a.wg.Done(); a.tokenRenewalLoop(adapterCtx) }()
	return nil
}

// Subscribe records the desired symbols and feeds and sends subscription messages
// over the current connection. Must be called after Connect.
func (a *Adapter) Subscribe(symbols []string, feeds []exchange.FeedType) error {
	a.subMu.Lock()
	a.rawSyms = make([]string, len(symbols))
	copy(a.rawSyms, symbols)
	a.feeds = make([]exchange.FeedType, len(feeds))
	copy(a.feeds, feeds)
	a.subMu.Unlock()

	return a.sendSubscriptions(a.adapterCtx)
}

// Close cancels the adapter context and waits for all goroutines to exit.
func (a *Adapter) Close() error {
	if a.cancel != nil {
		a.cancel()
	}
	a.wg.Wait()
	return nil
}

// runLoop manages the connection lifecycle. It is the only goroutine that updates
// a.conn. On disconnect it emits NeedsSnapshot signals and reconnects with backoff.
func (a *Adapter) runLoop(adapterCtx context.Context, conn *transport.Conn, tok tokenData) {
	defer a.wg.Done()
	defer func() {
		close(a.ticks)
		close(a.signals)
	}()

	for {
		connCtx, connCancel := context.WithCancel(adapterCtx)
		var connWg sync.WaitGroup
		connWg.Add(3)
		go func() {
			defer connWg.Done()
			a.readLoop(connCtx, conn)
		}()
		go func() {
			defer connWg.Done()
			a.heartbeatLoop(connCtx, conn, tok.pingInterval, tok.pingTimeout)
		}()
		go func() {
			defer connWg.Done()
			a.confirmWatcher(connCtx, conn)
		}()

		select {
		case <-adapterCtx.Done():
			connCancel()
			connWg.Wait()
			conn.Close()
			return
		case <-conn.Reconnect():
			slog.Info("kucoin: transport-level reconnect triggered")
		case <-a.reconnectTrigger:
			slog.Info("kucoin: heartbeat reconnect triggered")
		}

		connCancel()
		connWg.Wait()
		conn.Close()

		a.emitNeedsSnapshot("disconnect")

		// Reconnect with exponential backoff (1s, 2s, 4s … 60s max).
		for attempt := 0; ; attempt++ {
			if adapterCtx.Err() != nil {
				return
			}
			newTok, err := fetchToken(adapterCtx, a.httpClient, a.apiBase, a.cfg, a.clk)
			if err != nil {
				slog.Error("kucoin: reconnect token fetch", "attempt", attempt, "err", err)
				sleepBackoff(adapterCtx, attempt)
				continue
			}
			a.tokMu.Lock()
			a.tok = newTok
			a.tokMu.Unlock()

			newConn, err := transport.Dial(adapterCtx, newTok.wsURL(), transport.Options{
				PingInterval: 4 * time.Hour,
				PongTimeout:  5 * time.Second,
			})
			if err != nil {
				slog.Error("kucoin: reconnect dial", "attempt", attempt, "err", err)
				sleepBackoff(adapterCtx, attempt)
				continue
			}

			conn, tok = newConn, newTok
			break
		}

		a.connMu.Lock()
		a.conn = conn
		a.connMu.Unlock()

		a.resetAcks() // reset before re-subscribing on new connection

		if err := a.sendSubscriptions(adapterCtx); err != nil {
			slog.Error("kucoin: re-subscribe after reconnect", "err", err)
		}
	}
}

// readLoop reads WebSocket messages and dispatches them.
func (a *Adapter) readLoop(ctx context.Context, conn *transport.Conn) {
	for {
		var msg wireMessage
		if err := conn.Read(ctx, &msg); err != nil {
			if ctx.Err() != nil {
				return
			}
			slog.Debug("kucoin: read error", "err", err)
			return
		}
		a.dispatch(msg)
	}
}

func (a *Adapter) dispatch(msg wireMessage) {
	switch msg.Type {
	case "welcome":
		// Connection ready; subscriptions are sent by Subscribe().

	case "pong":
		// Notify the heartbeatLoop goroutine that is waiting on this ping ID.
		a.pingsMu.Lock()
		ch := a.pendingPings[msg.ID]
		a.pingsMu.Unlock()
		if ch != nil {
			select {
			case ch <- struct{}{}:
			default:
			}
		}

	case "ack":
		a.handleAck(msg.ID)

	case "message":
		a.handleMarketData(msg)

	case "error":
		slog.Error("kucoin: server error message", "id", msg.ID, "data", string(msg.Data))
	}
}

func (a *Adapter) handleAck(msgID string) {
	a.ackMu.Lock()
	syms, ok := a.pendingAcks[msgID]
	if !ok {
		a.ackMu.Unlock()
		slog.Warn("kucoin: spurious ack for unknown subscription ID", "id", msgID)
		return
	}
	for _, s := range syms {
		a.confirmed[s] = true
	}
	delete(a.pendingAcks, msgID)
	a.ackMu.Unlock()
}

func (a *Adapter) handleMarketData(msg wireMessage) {
	tsLocal := a.clk.Now().UnixNano()

	switch {
	case isL2Topic(msg.Topic):
		update, err := parseL2Update(msg)
		if err != nil {
			slog.Error("kucoin: parse l2 update", "topic", msg.Topic, "err", err)
			return
		}
		for i, d := range update.Deltas {
			// MsgSeqStart is only set on the first delta so the Worker fires the
			// gap check exactly once per message. MsgSeqEnd is set on all deltas
			// so the Worker can always update its lastMsgSeqEnd tracker.
			msgSeqStart := uint64(0)
			if i == 0 {
				msgSeqStart = update.MsgSeqStart
			}
			a.sendTick(exchange.Tick{
				Exchange:    "kucoin",
				Symbol:      update.Symbol,
				Seq:         d.Seq,
				TsExchange:  d.TsExchange,
				TsLocal:     tsLocal,
				Side:        sideStr(d.Side),
				Price:       d.Price,
				Size:        d.Size,
				Type:        exchange.EventTypeUpdate,
				MsgSeqStart: msgSeqStart,
				MsgSeqEnd:   update.MsgSeqEnd,
			})
		}

	case isTradesTopic(msg.Topic):
		trade, err := parseTrade(msg)
		if err != nil {
			slog.Error("kucoin: parse trade", "topic", msg.Topic, "err", err)
			return
		}
		a.sendTick(exchange.Tick{
			Exchange:   "kucoin",
			Symbol:     trade.Symbol,
			Seq:        trade.Seq,
			TsExchange: trade.TsExchange,
			TsLocal:    tsLocal,
			Side:       trade.Side,
			Price:      trade.Price,
			Size:       trade.Size,
			Type:       exchange.EventTypeTrade,
		})
	}
}

func (a *Adapter) sendTick(t exchange.Tick) {
	select {
	case a.ticks <- t:
	default:
		slog.Warn("kucoin: ticks channel full, dropping", "sym", t.Symbol)
	}
}

// heartbeatLoop sends KuCoin application-level pings at pingInterval and
// waits for a matching pong. Fires reconnectTrigger on timeout.
func (a *Adapter) heartbeatLoop(ctx context.Context, conn *transport.Conn, pingInterval, pingTimeout time.Duration) {
	ticker := time.NewTicker(pingInterval)
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			id := nextMsgID()
			pongCh := make(chan struct{}, 1)

			a.pingsMu.Lock()
			a.pendingPings[id] = pongCh
			a.pingsMu.Unlock()

			if err := conn.Write(ctx, map[string]string{"id": id, "type": "ping"}); err != nil {
				a.pingsMu.Lock()
				delete(a.pendingPings, id)
				a.pingsMu.Unlock()
				if ctx.Err() != nil {
					return
				}
				a.fireTrigger()
				return
			}

			select {
			case <-ctx.Done():
				a.pingsMu.Lock()
				delete(a.pendingPings, id)
				a.pingsMu.Unlock()
				return
			case <-pongCh:
				// Pong received — connection is healthy.
			case <-time.After(pingTimeout):
				slog.Warn("kucoin: pong timeout", "after", pingTimeout)
				a.pingsMu.Lock()
				delete(a.pendingPings, id)
				a.pingsMu.Unlock()
				a.fireTrigger()
				return
			}
		}
	}
}

// confirmWatcher waits confirmTimeout and logs ERROR for any still-unconfirmed symbols,
// then retries their subscriptions.
func (a *Adapter) confirmWatcher(ctx context.Context, conn *transport.Conn) {
	timer := time.NewTimer(a.confirmTimeout)
	defer timer.Stop()

	select {
	case <-ctx.Done():
		return
	case <-timer.C:
	}

	a.ackMu.Lock()
	var unconfirmed []string
	for _, syms := range a.pendingAcks {
		unconfirmed = append(unconfirmed, syms...)
	}
	a.ackMu.Unlock()

	if len(unconfirmed) == 0 {
		return
	}

	display := unconfirmed
	if len(display) > 5 {
		display = display[:5]
	}
	slog.Error("kucoin: symbols not confirmed within timeout, retrying",
		"count", len(unconfirmed), "sample", display)

	// Re-subscribe the unconfirmed batch.
	if err := a.sendBatch(ctx, conn, exchange.FeedTypeOrderBook, unconfirmed); err != nil {
		slog.Error("kucoin: confirm retry failed", "err", err)
	}
}

func (a *Adapter) emitNeedsSnapshot(reason string) {
	a.ackMu.Lock()
	var confirmed []string
	for sym, ok := range a.confirmed {
		if ok {
			confirmed = append(confirmed, sym)
		}
	}
	a.ackMu.Unlock()

	for _, raw := range confirmed {
		sig := exchange.Signal{
			Symbol: symbol.Normalize("kucoin", raw),
			Type:   exchange.SignalNeedsSnapshot,
			Reason: reason,
		}
		select {
		case a.signals <- sig:
		default:
			slog.Warn("kucoin: signals channel full", "sym", raw)
		}
	}
}

func (a *Adapter) resetAcks() {
	a.ackMu.Lock()
	a.pendingAcks = make(map[string][]string)
	a.confirmed = make(map[string]bool)
	a.ackMu.Unlock()
}

// sendSubscriptions sends subscription messages for all stored symbols.
func (a *Adapter) sendSubscriptions(ctx context.Context) error {
	a.subMu.Lock()
	rawSyms := make([]string, len(a.rawSyms))
	copy(rawSyms, a.rawSyms)
	feeds := make([]exchange.FeedType, len(a.feeds))
	copy(feeds, a.feeds)
	a.subMu.Unlock()

	if len(rawSyms) == 0 {
		return nil
	}

	a.connMu.Lock()
	conn := a.conn
	a.connMu.Unlock()
	if conn == nil {
		return fmt.Errorf("not connected")
	}

	for _, feed := range feeds {
		for i := 0; i < len(rawSyms); i += subBatchSize {
			end := i + subBatchSize
			if end > len(rawSyms) {
				end = len(rawSyms)
			}
			if err := a.sendBatch(ctx, conn, feed, rawSyms[i:end]); err != nil {
				return err
			}
		}
	}
	return nil
}

func (a *Adapter) sendBatch(ctx context.Context, conn *transport.Conn, feed exchange.FeedType, syms []string) error {
	topicPrefix := feedTopic(feed)
	if topicPrefix == "" {
		return nil
	}

	msgID := nextMsgID()
	topic := topicPrefix + ":" + strings.Join(syms, ",")
	msg := map[string]any{
		"id":             msgID,
		"type":           "subscribe",
		"topic":          topic,
		"privateChannel": false,
		"response":       true,
	}

	batch := make([]string, len(syms))
	copy(batch, syms)

	a.ackMu.Lock()
	a.pendingAcks[msgID] = batch
	a.ackMu.Unlock()

	if err := conn.Write(ctx, msg); err != nil {
		a.ackMu.Lock()
		delete(a.pendingAcks, msgID)
		a.ackMu.Unlock()
		return fmt.Errorf("send subscription batch: %w", err)
	}
	return nil
}

func (a *Adapter) fireTrigger() {
	select {
	case a.reconnectTrigger <- struct{}{}:
	default:
	}
}

// feedTopic maps a FeedType to the KuCoin topic prefix string.
func feedTopic(f exchange.FeedType) string {
	switch f {
	case exchange.FeedTypeOrderBook:
		return "/market/level2"
	case exchange.FeedTypeTrade:
		return "/market/match"
	default:
		return ""
	}
}

// sideStr maps orderbook.Side to the canonical "bid"/"ask" string.
func sideStr(s orderbook.Side) string {
	if s == orderbook.SideAsk {
		return "ask"
	}
	return "bid"
}

// sleepBackoff sleeps min(1s*2^attempt, 60s); returns early if ctx is cancelled.
func sleepBackoff(ctx context.Context, attempt int) {
	d := time.Duration(1<<uint(attempt)) * time.Second
	if d > 60*time.Second {
		d = 60 * time.Second
	}
	select {
	case <-time.After(d):
	case <-ctx.Done():
	}
}

// Package bybit implements the Bybit exchange adapter.
// It wraps the connection multiplexer (mux/) with application-level ping/pong,
// market data parsing, and the exchange.Exchange interface.
package bybit

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"sync"
	"time"

	"github.com/mrqdt/magnum-opus/aggregator/internal/exchange"
	"github.com/mrqdt/magnum-opus/aggregator/internal/exchange/bybit/mux"
	"github.com/mrqdt/magnum-opus/aggregator/internal/exchange/transport"
	"github.com/mrqdt/magnum-opus/aggregator/internal/orderbook"
)

// Compile-time proof that Adapter satisfies the Exchange interface.
var _ exchange.Exchange = (*Adapter)(nil)

const defaultBybitURL = "wss://stream.bybit.com/v5/public/linear"

// Clock abstracts time.Now() so the adapter can be driven deterministically in tests.
// The composition root supplies a real wall-clock implementation; tests supply a mockClock.
type Clock interface {
	Now() time.Time
}

// Adapter implements exchange.Exchange for Bybit.
// It coordinates the connection mux across all 20 connections.
type Adapter struct {
	clk      Clock
	bybitURL string

	// overrides for mux ping settings; zero means use mux defaults
	pingInterval time.Duration
	pingTimeout  time.Duration

	mu         sync.Mutex
	adapterCtx context.Context
	cancel     context.CancelFunc
	muxInst    *mux.Mux
}

// New creates a Bybit adapter. clk is injected for testability.
// Connections are NOT dialled here — call Connect then Subscribe.
func New(clk Clock) *Adapter {
	return &Adapter{
		clk:      clk,
		bybitURL: defaultBybitURL,
	}
}

// SetPingInterval overrides the mux ping interval (default 10s).
// Must be called before Subscribe. Primarily for tests.
func (a *Adapter) SetPingInterval(d time.Duration) { a.pingInterval = d }

// SetPingTimeout overrides the mux pong wait timeout (default 20s).
// Must be called before Subscribe. Primarily for tests.
func (a *Adapter) SetPingTimeout(d time.Duration) { a.pingTimeout = d }

// Name returns the canonical lowercase exchange name.
func (a *Adapter) Name() string { return "bybit" }

// Connect establishes the adapter's lifetime context.
// Subscribe must be called after Connect to create connections and subscriptions.
func (a *Adapter) Connect(ctx context.Context) error {
	adapterCtx, cancel := context.WithCancel(ctx)
	a.mu.Lock()
	a.adapterCtx = adapterCtx
	a.cancel = cancel
	a.mu.Unlock()
	return nil
}

// Subscribe creates the connection multiplexer, registers the market data
// callback, and establishes WebSocket connections for the given symbols.
// Must be called after Connect.
func (a *Adapter) Subscribe(syms []string, feeds []exchange.FeedType) error {
	a.mu.Lock()
	adapterCtx := a.adapterCtx
	a.mu.Unlock()

	if adapterCtx == nil {
		return fmt.Errorf("bybit: Connect must be called before Subscribe")
	}

	factory := func(ctx context.Context) (mux.Conn, error) {
		return transport.Dial(ctx, a.bybitURL, transport.Options{
			// Transport-level WS pings are effectively disabled; Bybit application-level
			// ping/pong ({"op":"ping"}) is handled by mux.pingLoop at the 10s interval.
			PingInterval: 4 * time.Hour,
			PongTimeout:  5 * time.Second,
		})
	}

	m := mux.New(syms, feeds, factory)
	m.WithMsgCallback(a.onMsg)
	if a.pingInterval > 0 {
		m.WithPingInterval(a.pingInterval)
	}
	if a.pingTimeout > 0 {
		m.WithPingTimeout(a.pingTimeout)
	}

	a.mu.Lock()
	a.muxInst = m
	a.mu.Unlock()

	return m.Connect(adapterCtx)
}

// Ticks returns the channel on which normalized Tick events are delivered.
func (a *Adapter) Ticks() <-chan exchange.Tick {
	a.mu.Lock()
	m := a.muxInst
	a.mu.Unlock()
	if m == nil {
		ch := make(chan exchange.Tick)
		close(ch)
		return ch
	}
	return m.Ticks()
}

// Signals returns the channel on which lifecycle signals are delivered.
func (a *Adapter) Signals() <-chan exchange.Signal {
	a.mu.Lock()
	m := a.muxInst
	a.mu.Unlock()
	if m == nil {
		ch := make(chan exchange.Signal)
		close(ch)
		return ch
	}
	return m.Signals()
}

// AddSymbols is not yet supported on the Bybit adapter. The connection
// multiplexer would need restructuring to assign new symbols to live slots.
// Add the symbol to config.yaml and restart the service.
func (a *Adapter) AddSymbols(_ []string, _ []exchange.FeedType) error {
	return fmt.Errorf("bybit: dynamic symbol addition not yet supported — add to config.yaml and restart")
}

// Close cancels the adapter context and waits for all goroutines to exit.
func (a *Adapter) Close() error {
	a.mu.Lock()
	cancel := a.cancel
	m := a.muxInst
	a.mu.Unlock()

	if cancel != nil {
		cancel()
	}
	if m != nil {
		return m.Close()
	}
	return nil
}

// onMsg is the mux.MsgCallback invoked for each incoming market data frame.
func (a *Adapter) onMsg(topic string, ts int64, msgType string, data json.RawMessage) {
	tsLocal := a.clk.Now().UnixNano()

	switch {
	case isL2Topic(topic):
		update, err := parseL2Update(topic, ts, data)
		if err != nil {
			slog.Error("bybit: parse l2 update", "topic", topic, "error", err)
			return
		}
		a.mu.Lock()
		m := a.muxInst
		a.mu.Unlock()
		if m == nil {
			return
		}
		for _, d := range update.Deltas {
			m.SendTick(exchange.Tick{
				Exchange:   "bybit",
				Symbol:     update.Symbol,
				Seq:        d.Seq,
				TsExchange: d.TsExchange,
				TsLocal:    tsLocal,
				Side:       sideStr(d.Side),
				Price:      d.Price,
				Size:       d.Size,
				Type:       exchange.EventTypeUpdate,
			})
		}

	case isTradesTopic(topic):
		trade, err := parseTrade(topic, data)
		if err != nil {
			slog.Error("bybit: parse trade", "topic", topic, "error", err)
			return
		}
		a.mu.Lock()
		m := a.muxInst
		a.mu.Unlock()
		if m == nil {
			return
		}
		m.SendTick(exchange.Tick{
			Exchange:   "bybit",
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

// sideStr maps orderbook.Side to the canonical "bid"/"ask" string.
func sideStr(s orderbook.Side) string {
	if s == orderbook.SideAsk {
		return "ask"
	}
	return "bid"
}


// Package hub manages WebSocket client connections and routes Redis pub/sub
// messages to browser clients based on symbol subscriptions.
package hub

import (
	"context"
	"log/slog"
	"strings"
	"sync"

	"github.com/coder/websocket"

	"github.com/mrqdt/magnum-opus/gateway/internal/codec"
)

const clientSendBuf = 16

// Sender is the minimal interface for delivering a message to a client.
// Using an interface lets the hub be tested without real WebSocket connections.
type Sender interface {
	Send(typ websocket.MessageType, data []byte)
}

type message struct {
	typ  websocket.MessageType
	data []byte
}

// Client wraps a live WebSocket connection.
type Client struct {
	conn *websocket.Conn
	send chan message
}

// NewClient creates a Client for the given WebSocket connection.
func NewClient(conn *websocket.Conn) *Client {
	return &Client{conn: conn, send: make(chan message, clientSendBuf)}
}

// Send implements Sender; drops silently if the outbound channel is full.
func (c *Client) Send(typ websocket.MessageType, data []byte) {
	select {
	case c.send <- message{typ: typ, data: data}:
	default:
		slog.Warn("gateway: client send buffer full — dropping message")
	}
}

// WritePump drains the client's send channel and writes to the WebSocket.
// Runs until ctx is cancelled, the channel is closed, or a write error occurs.
func (c *Client) WritePump(ctx context.Context) {
	for {
		select {
		case <-ctx.Done():
			return
		case msg, ok := <-c.send:
			if !ok {
				return
			}
			if err := c.conn.Write(ctx, msg.typ, msg.data); err != nil {
				if ctx.Err() == nil {
					slog.Warn("gateway: ws write failed", "err", err)
				}
				return
			}
		}
	}
}

// Hub routes messages from Redis pub/sub to subscribed WebSocket clients.
// All exported methods are goroutine-safe.
type Hub struct {
	mu       sync.RWMutex
	senders  map[Sender]bool
	bySymbol map[string]map[Sender]bool
	lastSnap map[string][]byte // last binary snapshot per symbol
}

// NewHub creates an empty Hub.
func NewHub() *Hub {
	return &Hub{
		senders:  make(map[Sender]bool),
		bySymbol: make(map[string]map[Sender]bool),
		lastSnap: make(map[string][]byte),
	}
}

// RegisterSender adds a sender to the hub's client set.
func (h *Hub) RegisterSender(s Sender) {
	h.mu.Lock()
	defer h.mu.Unlock()
	h.senders[s] = true
}

// UnregisterSender removes a sender and all its symbol subscriptions.
func (h *Hub) UnregisterSender(s Sender) {
	h.mu.Lock()
	defer h.mu.Unlock()
	delete(h.senders, s)
	for sym, clients := range h.bySymbol {
		delete(clients, s)
		if len(clients) == 0 {
			delete(h.bySymbol, sym)
			delete(h.lastSnap, sym)
		}
	}
}

// Subscribe registers a sender for messages on the given symbol.
// If a cached snapshot exists for the symbol, it is sent immediately.
func (h *Hub) Subscribe(s Sender, symbol string) {
	h.mu.Lock()
	if h.bySymbol[symbol] == nil {
		h.bySymbol[symbol] = make(map[Sender]bool)
	}
	h.bySymbol[symbol][s] = true
	snap := h.lastSnap[symbol]
	h.mu.Unlock()

	if snap != nil {
		s.Send(websocket.MessageBinary, snap)
	}
}

// Unsubscribe removes a sender from a symbol's subscriber set.
func (h *Hub) Unsubscribe(s Sender, symbol string) {
	h.mu.Lock()
	defer h.mu.Unlock()
	if clients, ok := h.bySymbol[symbol]; ok {
		delete(clients, s)
		if len(clients) == 0 {
			delete(h.bySymbol, symbol)
			delete(h.lastSnap, symbol)
		}
	}
}

// Route dispatches a Redis pub/sub message to subscribed clients.
//   - channel "orderbook:{exchange}:{symbol}": encode to binary, cache as lastSnap, send as binary frame
//   - channel "candles1s:{exchange}:{symbol}": inject "type" field, send as text frame
//
// Channels with unexpected formats are silently dropped.
func (h *Hub) Route(channel string, payload []byte) {
	parts := strings.SplitN(channel, ":", 3)
	if len(parts) != 3 {
		return
	}
	prefix, _, symbol := parts[0], parts[1], parts[2]

	switch prefix {
	case "orderbook":
		h.routeOrderbook(symbol, payload)
	case "candles1s":
		h.routeCandles1s(symbol, payload)
	}
}

func (h *Hub) routeOrderbook(symbol string, payload []byte) {
	encoded, err := codec.EncodeBinary(payload)
	if err != nil {
		slog.Warn("gateway: failed to encode orderbook binary", "symbol", symbol, "err", err)
		return
	}

	h.mu.Lock()
	h.lastSnap[symbol] = encoded
	subscribers := h.bySymbol[symbol]
	// Copy to avoid holding lock during sends
	targets := make([]Sender, 0, len(subscribers))
	for s := range subscribers {
		targets = append(targets, s)
	}
	h.mu.Unlock()

	for _, s := range targets {
		s.Send(websocket.MessageBinary, encoded)
	}
}

func (h *Hub) routeCandles1s(symbol string, payload []byte) {
	tagged, err := codec.InjectType(payload, "candles1s")
	if err != nil {
		slog.Warn("gateway: failed to inject type into candles1s payload", "symbol", symbol, "err", err)
		return
	}

	h.mu.RLock()
	subscribers := h.bySymbol[symbol]
	targets := make([]Sender, 0, len(subscribers))
	for s := range subscribers {
		targets = append(targets, s)
	}
	h.mu.RUnlock()

	for _, s := range targets {
		s.Send(websocket.MessageText, tagged)
	}
}

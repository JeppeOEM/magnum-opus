// Package transport wraps github.com/coder/websocket with keepalive and reconnect signaling.
// gorilla/websocket and nhooyr.io/websocket are prohibited — github.com/coder/websocket exclusively.
package transport

import (
	"context"
	"sync"
	"time"

	"github.com/coder/websocket"
	"github.com/coder/websocket/wsjson"
)

const (
	defaultPingInterval = 15 * time.Second
	defaultPongTimeout  = 5 * time.Second
	reconnectBufSize    = 1
)

// Options configures a Conn.
// Zero values use the package defaults (ping every 15s, pong timeout 5s).
type Options struct {
	PingInterval time.Duration
	PongTimeout  time.Duration
	// DialOptions is passed through to websocket.Dial; nil is valid.
	DialOptions *websocket.DialOptions
}

// Conn wraps a github.com/coder/websocket connection with a keepalive goroutine.
// When the keepalive detects no pong within the timeout window, it fires the
// Reconnect() channel so the owning exchange adapter can re-establish the feed.
//
// Not safe for concurrent reads or concurrent writes — each direction is single-owner.
type Conn struct {
	conn         *websocket.Conn
	reconnectCh  chan struct{} // buffered size 1; send is non-blocking
	pingInterval time.Duration
	pongTimeout  time.Duration
	cancel       context.CancelFunc
	wg           sync.WaitGroup
}

// Dial establishes a WebSocket connection to url and starts the keepalive goroutine.
// The keepalive runs until Close() is called or ctx is cancelled.
func Dial(ctx context.Context, url string, opts Options) (*Conn, error) {
	if opts.PingInterval == 0 {
		opts.PingInterval = defaultPingInterval
	}
	if opts.PongTimeout == 0 {
		opts.PongTimeout = defaultPongTimeout
	}

	wsConn, _, err := websocket.Dial(ctx, url, opts.DialOptions)
	if err != nil {
		return nil, err
	}

	keepCtx, cancel := context.WithCancel(ctx)
	c := &Conn{
		conn:         wsConn,
		reconnectCh:  make(chan struct{}, reconnectBufSize),
		pingInterval: opts.PingInterval,
		pongTimeout:  opts.PongTimeout,
		cancel:       cancel,
	}

	c.wg.Add(1)
	go c.keepalive(keepCtx)

	return c, nil
}

// Read reads the next JSON message from the connection into v.
func (c *Conn) Read(ctx context.Context, v any) error {
	return wsjson.Read(ctx, c.conn, v)
}

// Write sends v as a JSON message to the connection.
func (c *Conn) Write(ctx context.Context, v any) error {
	return wsjson.Write(ctx, c.conn, v)
}

// Reconnect returns the channel that fires when the keepalive detects a pong timeout.
// The channel has a buffer of 1. If a reconnect event is already pending and unread,
// the send is skipped — the existing pending event is sufficient.
func (c *Conn) Reconnect() <-chan struct{} { return c.reconnectCh }

// Close cancels the keepalive context, waits for the keepalive goroutine to exit,
// then closes the underlying connection with StatusNormalClosure.
// Teardown order: cancel ctx first, then conn.Close — never the reverse.
func (c *Conn) Close() {
	c.cancel()
	c.wg.Wait()
	c.conn.Close(websocket.StatusNormalClosure, "")
}

// keepalive sends pings at the configured interval and fires reconnectCh when no pong
// is received within pongTimeout. Exits cleanly when ctx is cancelled.
func (c *Conn) keepalive(ctx context.Context) {
	defer c.wg.Done()
	ticker := time.NewTicker(c.pingInterval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			pongCtx, cancel := context.WithTimeout(ctx, c.pongTimeout)
			err := c.conn.Ping(pongCtx)
			cancel()
			if err != nil {
				// Non-blocking send: if the channel already has a pending reconnect,
				// skip — the receiver will handle the existing event.
				select {
				case c.reconnectCh <- struct{}{}:
				default:
				}
				return
			}
		}
	}
}

//go:build l3

// Package transport_test provides L3 acceptance tests for the WebSocket transport layer.
// Tests run an in-process WebSocket server via net/http/httptest — no external dependencies.
// Build tag: l3 — run with `make test-l3`.
//
// ATDD scaffold for Story 2.1: Exchange Interface & WebSocket Transport Layer.
package transport_test

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"github.com/coder/websocket"
	"github.com/coder/websocket/wsjson"

	"github.com/mrqdt/magnum-opus/aggregator/internal/exchange/transport"
)

// wsURL converts an httptest server URL from http:// to ws://.
func wsURL(serverURL string) string {
	return "ws" + serverURL[len("http"):]
}

// echoServer returns an httptest server that accepts a WebSocket connection and echoes all messages.
func echoServer(t *testing.T) *httptest.Server {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		conn, err := websocket.Accept(w, r, nil)
		if err != nil {
			return
		}
		defer conn.Close(websocket.StatusNormalClosure, "")
		ctx := r.Context()
		for {
			var msg map[string]any
			if err := wsjson.Read(ctx, conn, &msg); err != nil {
				return
			}
			if err := wsjson.Write(ctx, conn, msg); err != nil {
				return
			}
		}
	}))
	t.Cleanup(srv.Close)
	return srv
}

// TestConn_DialAndReadWrite verifies the basic connect + read/write round-trip.
// AC: transport/conn.go uses wsjson.Read / wsjson.Write exclusively.
func TestConn_DialAndReadWrite(t *testing.T) {
	srv := echoServer(t)
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	conn, err := transport.Dial(ctx, wsURL(srv.URL), transport.Options{})
	require.NoError(t, err)
	defer conn.Close()

	type msg struct {
		Type string `json:"type"`
		Data string `json:"data"`
	}

	sent := msg{Type: "test", Data: "hello-world"}
	require.NoError(t, conn.Write(ctx, sent))

	var received msg
	require.NoError(t, conn.Read(ctx, &received))
	assert.Equal(t, sent, received)
}

// TestConn_ReconnectFiresWithin5sOnConnectionDrop verifies NFR6:
// the transport detects a dropped connection within 5 seconds.
// AC: "a reconnect event is triggered within 5 seconds"
//
// Approach: server accepts the connection, then srv.Close() drops the TCP conn
// without sending a WebSocket close frame. The keepalive's next Ping() fails,
// triggering the reconnect channel.
func TestConn_ReconnectFiresWithin5sOnConnectionDrop(t *testing.T) {
	connected := make(chan struct{})
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		conn, err := websocket.Accept(w, r, nil)
		if err != nil {
			return
		}
		defer conn.Close(websocket.StatusNormalClosure, "")
		close(connected)
		<-r.Context().Done() // block until srv.Close() kills the connection
	}))
	// Note: do NOT t.Cleanup(srv.Close) — we close it manually below.

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	defer srv.Close()

	conn, err := transport.Dial(ctx, wsURL(srv.URL), transport.Options{
		PingInterval: 500 * time.Millisecond,
		PongTimeout:  1 * time.Second,
	})
	require.NoError(t, err)
	defer conn.Close()

	// Confirm server accepted the connection.
	select {
	case <-connected:
	case <-time.After(3 * time.Second):
		t.Fatal("server did not accept connection within 3 seconds")
	}

	// Drop the TCP connection abruptly (no WebSocket close frame).
	srv.Close()

	// NFR6: reconnect must fire within 5 seconds of the drop.
	select {
	case <-conn.Reconnect():
		// success
	case <-time.After(5 * time.Second):
		t.Fatal("NFR6 violation: reconnect not triggered within 5 seconds of connection drop")
	}
}

// TestConn_ReconnectChannelIsBuffered verifies the reconnect channel has capacity >= 1.
// AC: "reconnect event propagates via a buffered channel (minimum buffer size 1)"
func TestConn_ReconnectChannelIsBuffered(t *testing.T) {
	srv := echoServer(t)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	conn, err := transport.Dial(ctx, wsURL(srv.URL), transport.Options{})
	require.NoError(t, err)
	defer conn.Close()

	assert.GreaterOrEqual(t, cap(conn.Reconnect()), 1,
		"reconnect channel must have buffer capacity >= 1 to prevent keepalive blocking")
}

// TestConn_ReconnectNonBlockingWhenFull verifies that Close() completes quickly
// even when a reconnect has fired — no keepalive goroutine stuck on a channel send.
// AC: "if the buffered channel is full, the send is skipped — not blocked"
//
// Server does NOT send a WebSocket close frame (no defer conn.Close) — this ensures
// the TCP drop is abrupt. If the server sends a close frame, the WebSocket library's
// close handshake waits ~2s for the echo, which would cause a false timeout failure.
func TestConn_ReconnectNonBlockingWhenFull(t *testing.T) {
	connected := make(chan struct{})
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, err := websocket.Accept(w, r, nil)
		if err != nil {
			return
		}
		close(connected)
		<-r.Context().Done()
		// No conn.Close() — let srv.Close() terminate the TCP connection abruptly.
	}))
	// Manual close below.
	defer srv.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	conn, err := transport.Dial(ctx, wsURL(srv.URL), transport.Options{
		PingInterval: 300 * time.Millisecond,
		PongTimeout:  300 * time.Millisecond,
	})
	require.NoError(t, err)

	<-connected
	srv.Close() // drop the connection

	// Wait for reconnect to fire (channel now holds 1 pending event).
	select {
	case <-conn.Reconnect():
	case <-time.After(4 * time.Second):
		t.Fatal("timed out waiting for reconnect event after connection drop")
	}
	// keepalive exited after firing. Close() must return quickly (no blocking send).
	done := make(chan struct{})
	go func() { conn.Close(); close(done) }()
	select {
	case <-done:
	case <-time.After(2 * time.Second):
		t.Fatal("Close() blocked — keepalive goroutine may be stuck on a channel send")
	}
}

// TestConn_CleanExitOnCtxCancel verifies ctx cancel causes clean exit without goroutine leak.
// AC: "ctx cancelled → transport exits cleanly without blocking"
// AC: "every for-select loop has case <-ctx.Done(): return as a peer case"
func TestConn_CleanExitOnCtxCancel(t *testing.T) {
	srv := echoServer(t)
	ctx, cancel := context.WithCancel(context.Background())

	conn, err := transport.Dial(ctx, wsURL(srv.URL), transport.Options{})
	require.NoError(t, err)

	cancel() // cancel the keepalive context

	done := make(chan struct{})
	go func() { conn.Close(); close(done) }()
	select {
	case <-done:
	case <-time.After(2 * time.Second):
		t.Fatal("transport did not exit cleanly after ctx cancel — possible goroutine leak")
	}
}

// TestConn_TeardownSendsStatusNormalClosure verifies that Close() sends a StatusNormalClosure frame.
// AC: "graceful teardown cancels context first, then conn.Close(StatusNormalClosure, '')"
func TestConn_TeardownSendsStatusNormalClosure(t *testing.T) {
	closeCodes := make(chan websocket.StatusCode, 1)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		c, err := websocket.Accept(w, r, nil)
		if err != nil {
			return
		}
		// Block reading — the close frame from the client arrives as a CloseError.
		_, _, err = c.Reader(r.Context())
		var ce websocket.CloseError
		if errors.As(err, &ce) {
			select {
			case closeCodes <- ce.Code:
			default:
			}
		}
	}))
	t.Cleanup(srv.Close)

	ctx := context.Background()
	conn, err := transport.Dial(ctx, wsURL(srv.URL), transport.Options{})
	require.NoError(t, err)

	conn.Close()

	select {
	case code := <-closeCodes:
		assert.Equal(t, websocket.StatusNormalClosure, code,
			"server must receive StatusNormalClosure close frame, not an abnormal closure")
	case <-time.After(2 * time.Second):
		t.Fatal("server did not receive a close frame within 2 seconds")
	}
}

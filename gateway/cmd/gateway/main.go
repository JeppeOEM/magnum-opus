package main

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"os/signal"
	"syscall"
	"time"

	"github.com/coder/websocket"
	goredis "github.com/redis/go-redis/v9"

	"github.com/mrqdt/magnum-opus/gateway/internal/config"
	"github.com/mrqdt/magnum-opus/gateway/internal/hub"
)

const (
	msgSubscribe   = 0x10
	msgUnsubscribe = 0x11
)

func main() {
	cfg := config.Load()

	rdb := goredis.NewClient(&goredis.Options{Addr: cfg.RedisAddr})
	h := hub.NewHub()

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGTERM, syscall.SIGINT)
	defer stop()

	go runSubscriber(ctx, rdb, h)

	mux := http.NewServeMux()
	mux.HandleFunc("/ws", wsHandler(ctx, h))
	mux.HandleFunc("/health", healthHandler)

	srv := &http.Server{Addr: cfg.ListenAddr, Handler: mux}

	go func() {
		<-ctx.Done()
		shutCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		_ = srv.Shutdown(shutCtx)
	}()

	slog.Info("gateway: listening", "addr", cfg.ListenAddr)
	if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		slog.Error("gateway: server error", "err", err)
	}
}

func runSubscriber(ctx context.Context, rdb *goredis.Client, h *hub.Hub) {
	for {
		if err := subscribe(ctx, rdb, h); err != nil {
			slog.Warn("gateway: pubsub subscriber error, reconnecting", "err", err)
		}
		select {
		case <-ctx.Done():
			return
		case <-time.After(time.Second):
		}
	}
}

func subscribe(ctx context.Context, rdb *goredis.Client, h *hub.Hub) error {
	ps := rdb.PSubscribe(ctx, "orderbook:*", "candles1s:*")
	defer ps.Close()
	ch := ps.Channel()
	for {
		select {
		case <-ctx.Done():
			return nil
		case msg, ok := <-ch:
			if !ok {
				return errors.New("pubsub channel closed")
			}
			h.Route(msg.Channel, []byte(msg.Payload))
		}
	}
}

func wsHandler(serverCtx context.Context, h *hub.Hub) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		conn, err := websocket.Accept(w, r, &websocket.AcceptOptions{
			InsecureSkipVerify: true,
		})
		if err != nil {
			slog.Warn("gateway: ws accept failed", "err", err)
			return
		}

		c := hub.NewClient(conn)
		h.RegisterSender(c)
		defer h.UnregisterSender(c)

		ctx, cancel := context.WithCancel(serverCtx)
		defer cancel()

		go c.WritePump(ctx)

		for {
			msgType, data, err := conn.Read(ctx)
			if err != nil {
				return
			}
			if msgType != websocket.MessageBinary {
				continue
			}
			if len(data) < 2 {
				continue
			}
			symLen := int(data[1])
			if symLen == 0 {
				continue
			}
			if len(data) < 2+symLen {
				continue
			}
			symbol := string(data[2 : 2+symLen])
			switch data[0] {
			case msgSubscribe:
				h.Subscribe(c, symbol)
			case msgUnsubscribe:
				h.Unsubscribe(c, symbol)
			default:
				slog.Warn("gateway: unknown message opcode", "opcode", data[0])
			}
		}
	}
}

func healthHandler(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write([]byte(`{"status":"ok"}`))
}

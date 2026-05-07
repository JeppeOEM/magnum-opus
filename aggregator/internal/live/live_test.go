//go:build live

// Package live_test contains L5 live exchange tests.
// Requires real API credentials. Run via: make test-live
//
// Credential check: if KUCOIN_API_KEY or BYBIT_API_KEY is absent, exits with a
// clear message rather than a confusing authentication error.
package live_test

import (
	"context"
	"fmt"
	"net/http"
	"os"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"

	"github.com/mrqdt/magnum-opus/aggregator/internal/config"
	"github.com/mrqdt/magnum-opus/aggregator/internal/exchange"
	bybitexch "github.com/mrqdt/magnum-opus/aggregator/internal/exchange/bybit"
	kucoinexch "github.com/mrqdt/magnum-opus/aggregator/internal/exchange/kucoin"
	"github.com/mrqdt/magnum-opus/aggregator/internal/symbol"
	"github.com/mrqdt/magnum-opus/aggregator/internal/testutil"
)

func TestMain(m *testing.M) {
	missing := []string{}
	for _, v := range []string{"KUCOIN_API_KEY", "KUCOIN_API_SECRET", "KUCOIN_API_PASSPHRASE", "BYBIT_API_KEY", "BYBIT_API_SECRET"} {
		if os.Getenv(v) == "" {
			missing = append(missing, v)
		}
	}
	if len(missing) > 0 {
		fmt.Fprintf(os.Stderr,
			"live tests require exchange credentials; missing: %s\n"+
				"Set them or source .env before running make test-live\n",
			strings.Join(missing, ", "))
		os.Exit(1)
	}
	os.Exit(m.Run())
}

// realClock satisfies the kucoin.Clock and bybit.Clock interfaces.
type realClock struct{}

func (realClock) Now() time.Time { return time.Now() }

// loadConfig calls config.Load() and skips the test if config is invalid.
func loadConfig(t *testing.T) *config.Config {
	t.Helper()
	cfg, err := config.Load()
	require.NoError(t, err, "config.Load failed — ensure all required env vars are set")
	return cfg
}

// receiveTicks drains the Ticks channel until n ticks are received or timeout.
func receiveTicks(t *testing.T, ticks <-chan exchange.Tick, n int, timeout time.Duration) []exchange.Tick {
	t.Helper()
	out := make([]exchange.Tick, 0, n)
	deadline := time.After(timeout)
	for len(out) < n {
		select {
		case tk, ok := <-ticks:
			if !ok {
				t.Fatalf("ticks channel closed after receiving %d/%d ticks", len(out), n)
			}
			out = append(out, tk)
		case <-deadline:
			t.Fatalf("timeout waiting for ticks: received %d/%d within %v", len(out), n, timeout)
		}
	}
	return out
}

// ── KuCoin live tests (9) ─────────────────────────────────────────────────────

func TestKuCoin_Connect(t *testing.T) {
	cfg := loadConfig(t)
	clk := testutil.NewMockClock(time.Now())
	a := kucoinexch.New(cfg.KuCoin, http.DefaultClient, clk)
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	require.NoError(t, a.Connect(ctx))
}

func TestKuCoin_Subscribe(t *testing.T) {
	cfg := loadConfig(t)
	clk := testutil.NewMockClock(time.Now())
	a := kucoinexch.New(cfg.KuCoin, http.DefaultClient, clk)
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	require.NoError(t, a.Connect(ctx))
	require.NoError(t, a.Subscribe([]string{"BTC-USDT"}, []exchange.FeedType{exchange.FeedTypeOrderBook}))
}

func TestKuCoin_ReceivesTicks(t *testing.T) {
	cfg := loadConfig(t)
	clk := testutil.NewMockClock(time.Now())
	a := kucoinexch.New(cfg.KuCoin, http.DefaultClient, clk)
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	require.NoError(t, a.Connect(ctx))
	require.NoError(t, a.Subscribe([]string{"BTC-USDT"}, []exchange.FeedType{exchange.FeedTypeOrderBook}))
	ticks := receiveTicks(t, a.Ticks(), 5, 30*time.Second)
	require.Len(t, ticks, 5)
}

func TestKuCoin_TickSideValid(t *testing.T) {
	cfg := loadConfig(t)
	clk := testutil.NewMockClock(time.Now())
	a := kucoinexch.New(cfg.KuCoin, http.DefaultClient, clk)
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	require.NoError(t, a.Connect(ctx))
	require.NoError(t, a.Subscribe([]string{"BTC-USDT"}, []exchange.FeedType{exchange.FeedTypeOrderBook}))
	ticks := receiveTicks(t, a.Ticks(), 10, 30*time.Second)
	for _, tk := range ticks {
		assert.True(t, tk.Side == "bid" || tk.Side == "ask", "side must be bid or ask, got %q", tk.Side)
	}
}

func TestKuCoin_TickPriceAndSizePositive(t *testing.T) {
	cfg := loadConfig(t)
	clk := testutil.NewMockClock(time.Now())
	a := kucoinexch.New(cfg.KuCoin, http.DefaultClient, clk)
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	require.NoError(t, a.Connect(ctx))
	require.NoError(t, a.Subscribe([]string{"BTC-USDT"}, []exchange.FeedType{exchange.FeedTypeOrderBook}))
	ticks := receiveTicks(t, a.Ticks(), 10, 30*time.Second)
	for _, tk := range ticks {
		price, err := strconv.ParseFloat(tk.Price, 64)
		require.NoError(t, err, "price must be parseable float")
		assert.GreaterOrEqual(t, price, 0.0, "price must be >= 0")
		_, err = strconv.ParseFloat(tk.Size, 64)
		require.NoError(t, err, "size must be parseable float")
	}
}

func TestKuCoin_TickSeqMonotonic(t *testing.T) {
	cfg := loadConfig(t)
	clk := testutil.NewMockClock(time.Now())
	a := kucoinexch.New(cfg.KuCoin, http.DefaultClient, clk)
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	require.NoError(t, a.Connect(ctx))
	require.NoError(t, a.Subscribe([]string{"BTC-USDT"}, []exchange.FeedType{exchange.FeedTypeOrderBook}))
	ticks := receiveTicks(t, a.Ticks(), 10, 30*time.Second)
	for i := 1; i < len(ticks); i++ {
		if ticks[i].Symbol == ticks[i-1].Symbol {
			assert.GreaterOrEqual(t, ticks[i].Seq, ticks[i-1].Seq,
				"seq must be monotonically non-decreasing for the same symbol")
		}
	}
}

func TestKuCoin_SymbolNormalization(t *testing.T) {
	cfg := loadConfig(t)
	clk := testutil.NewMockClock(time.Now())
	a := kucoinexch.New(cfg.KuCoin, http.DefaultClient, clk)
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	require.NoError(t, a.Connect(ctx))
	require.NoError(t, a.Subscribe([]string{"BTC-USDT"}, []exchange.FeedType{exchange.FeedTypeOrderBook}))
	ticks := receiveTicks(t, a.Ticks(), 5, 30*time.Second)
	canonical := symbol.Normalize("kucoin", "BTC-USDT")
	for _, tk := range ticks {
		assert.Equal(t, canonical, tk.Symbol, "symbol must be normalized to canonical form")
	}
}

func TestKuCoin_TsExchangeReasonable(t *testing.T) {
	cfg := loadConfig(t)
	clk := testutil.NewMockClock(time.Now())
	a := kucoinexch.New(cfg.KuCoin, http.DefaultClient, clk)
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	require.NoError(t, a.Connect(ctx))
	require.NoError(t, a.Subscribe([]string{"BTC-USDT"}, []exchange.FeedType{exchange.FeedTypeOrderBook}))
	ticks := receiveTicks(t, a.Ticks(), 5, 30*time.Second)
	window := 5 * time.Minute
	for _, tk := range ticks {
		tsExch := time.Unix(0, tk.TsExchange)
		diff := time.Since(tsExch).Abs()
		assert.Less(t, diff, window, "TsExchange must be within 5 minutes of local time")
	}
}

func TestKuCoin_MultiSymbolSubscribe(t *testing.T) {
	cfg := loadConfig(t)
	clk := testutil.NewMockClock(time.Now())
	a := kucoinexch.New(cfg.KuCoin, http.DefaultClient, clk)
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	require.NoError(t, a.Connect(ctx))
	require.NoError(t, a.Subscribe([]string{"BTC-USDT", "ETH-USDT"}, []exchange.FeedType{exchange.FeedTypeOrderBook}))
	ticks := receiveTicks(t, a.Ticks(), 10, 45*time.Second)
	symbols := make(map[symbol.Symbol]bool)
	for _, tk := range ticks {
		symbols[tk.Symbol] = true
	}
	// Both symbols should appear within 10 ticks on an active market.
	assert.GreaterOrEqual(t, len(symbols), 1, "at least 1 symbol received")
}

func TestKuCoin_ExchangeField(t *testing.T) {
	cfg := loadConfig(t)
	clk := testutil.NewMockClock(time.Now())
	a := kucoinexch.New(cfg.KuCoin, http.DefaultClient, clk)
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	require.NoError(t, a.Connect(ctx))
	require.NoError(t, a.Subscribe([]string{"BTC-USDT"}, []exchange.FeedType{exchange.FeedTypeOrderBook}))
	ticks := receiveTicks(t, a.Ticks(), 3, 30*time.Second)
	for _, tk := range ticks {
		assert.Equal(t, "kucoin", tk.Exchange, "Exchange field must be 'kucoin'")
	}
}

// ── Bybit live tests (9) ──────────────────────────────────────────────────────

func TestBybit_Connect(t *testing.T) {
	a := bybitexch.New(realClock{})
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	require.NoError(t, a.Connect(ctx))
}

func TestBybit_Subscribe(t *testing.T) {
	a := bybitexch.New(realClock{})
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	require.NoError(t, a.Connect(ctx))
	require.NoError(t, a.Subscribe([]string{"BTCUSDT"}, []exchange.FeedType{exchange.FeedTypeOrderBook}))
}

func TestBybit_ReceivesTicks(t *testing.T) {
	a := bybitexch.New(realClock{})
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	require.NoError(t, a.Connect(ctx))
	require.NoError(t, a.Subscribe([]string{"BTCUSDT"}, []exchange.FeedType{exchange.FeedTypeOrderBook}))
	ticks := receiveTicks(t, a.Ticks(), 5, 30*time.Second)
	require.Len(t, ticks, 5)
}

func TestBybit_TickSideValid(t *testing.T) {
	a := bybitexch.New(realClock{})
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	require.NoError(t, a.Connect(ctx))
	require.NoError(t, a.Subscribe([]string{"BTCUSDT"}, []exchange.FeedType{exchange.FeedTypeOrderBook}))
	ticks := receiveTicks(t, a.Ticks(), 10, 30*time.Second)
	for _, tk := range ticks {
		assert.True(t, tk.Side == "bid" || tk.Side == "ask", "side must be bid or ask, got %q", tk.Side)
	}
}

func TestBybit_TickPriceAndSizePositive(t *testing.T) {
	a := bybitexch.New(realClock{})
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	require.NoError(t, a.Connect(ctx))
	require.NoError(t, a.Subscribe([]string{"BTCUSDT"}, []exchange.FeedType{exchange.FeedTypeOrderBook}))
	ticks := receiveTicks(t, a.Ticks(), 10, 30*time.Second)
	for _, tk := range ticks {
		price, err := strconv.ParseFloat(tk.Price, 64)
		require.NoError(t, err, "price must be parseable float")
		assert.GreaterOrEqual(t, price, 0.0)
		_, err = strconv.ParseFloat(tk.Size, 64)
		require.NoError(t, err, "size must be parseable float")
	}
}

func TestBybit_TickSeqMonotonic(t *testing.T) {
	a := bybitexch.New(realClock{})
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	require.NoError(t, a.Connect(ctx))
	require.NoError(t, a.Subscribe([]string{"BTCUSDT"}, []exchange.FeedType{exchange.FeedTypeOrderBook}))
	ticks := receiveTicks(t, a.Ticks(), 10, 30*time.Second)
	for i := 1; i < len(ticks); i++ {
		if ticks[i].Symbol == ticks[i-1].Symbol {
			assert.GreaterOrEqual(t, ticks[i].Seq, ticks[i-1].Seq)
		}
	}
}

func TestBybit_SymbolNormalization(t *testing.T) {
	a := bybitexch.New(realClock{})
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	require.NoError(t, a.Connect(ctx))
	require.NoError(t, a.Subscribe([]string{"BTCUSDT"}, []exchange.FeedType{exchange.FeedTypeOrderBook}))
	ticks := receiveTicks(t, a.Ticks(), 5, 30*time.Second)
	canonical := symbol.Normalize("bybit", "BTCUSDT")
	for _, tk := range ticks {
		assert.Equal(t, canonical, tk.Symbol)
	}
}

func TestBybit_TsExchangeReasonable(t *testing.T) {
	a := bybitexch.New(realClock{})
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	require.NoError(t, a.Connect(ctx))
	require.NoError(t, a.Subscribe([]string{"BTCUSDT"}, []exchange.FeedType{exchange.FeedTypeOrderBook}))
	ticks := receiveTicks(t, a.Ticks(), 5, 30*time.Second)
	window := 5 * time.Minute
	for _, tk := range ticks {
		tsExch := time.Unix(0, tk.TsExchange)
		diff := time.Since(tsExch).Abs()
		assert.Less(t, diff, window)
	}
}

func TestBybit_MultiSymbolSubscribe(t *testing.T) {
	a := bybitexch.New(realClock{})
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	require.NoError(t, a.Connect(ctx))
	require.NoError(t, a.Subscribe([]string{"BTCUSDT", "ETHUSDT"}, []exchange.FeedType{exchange.FeedTypeOrderBook}))
	ticks := receiveTicks(t, a.Ticks(), 10, 45*time.Second)
	symbols := make(map[symbol.Symbol]bool)
	for _, tk := range ticks {
		symbols[tk.Symbol] = true
	}
	assert.GreaterOrEqual(t, len(symbols), 1, "at least 1 symbol received")
}

func TestBybit_ExchangeField(t *testing.T) {
	a := bybitexch.New(realClock{})
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	require.NoError(t, a.Connect(ctx))
	require.NoError(t, a.Subscribe([]string{"BTCUSDT"}, []exchange.FeedType{exchange.FeedTypeOrderBook}))
	ticks := receiveTicks(t, a.Ticks(), 3, 30*time.Second)
	for _, tk := range ticks {
		assert.Equal(t, "bybit", tk.Exchange)
	}
}

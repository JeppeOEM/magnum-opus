package symbol_test

import (
	"testing"

	"github.com/mrqdt/magnum-opus/aggregator/internal/symbol"
	"github.com/stretchr/testify/assert"
)

func TestNormalize_KuCoin(t *testing.T) {
	got := symbol.Normalize("kucoin", "BTC-USDT")
	assert.Equal(t, symbol.Symbol("BTC-USDT"), got)
}

func TestNormalize_Bybit(t *testing.T) {
	got := symbol.Normalize("bybit", "BTCUSDT")
	assert.Equal(t, symbol.Symbol("BTC-USDT"), got)
}

func TestNormalize_SamePair_BothExchanges(t *testing.T) {
	kucoin := symbol.Normalize("kucoin", "BTC-USDT")
	bybit := symbol.Normalize("bybit", "BTCUSDT")
	assert.Equal(t, kucoin, bybit, "KuCoin and Bybit must produce identical canonical symbols")
}

func TestNormalize_Table(t *testing.T) {
	cases := []struct {
		exchange string
		raw      string
		want     symbol.Symbol
	}{
		{"kucoin", "ETH-USDT", "ETH-USDT"},
		{"kucoin", "SOL-USDC", "SOL-USDC"},
		{"kucoin", "btc-usdt", "BTC-USDT"}, // lowercase input
		{"bybit", "ETHUSDT", "ETH-USDT"},
		{"bybit", "SOLUSDC", "SOL-USDC"},
		{"bybit", "ethusdt", "ETH-USDT"}, // lowercase input
		{"bybit", "BTCETH", "BTC-ETH"},
		{"bybit", "ETHBTC", "ETH-BTC"},
	}

	for _, tc := range cases {
		t.Run(tc.exchange+"/"+tc.raw, func(t *testing.T) {
			got := symbol.Normalize(tc.exchange, tc.raw)
			assert.Equal(t, tc.want, got)
		})
	}
}

func TestNormalize_UnknownExchange(t *testing.T) {
	got := symbol.Normalize("binance", "BTCUSDT")
	// Falls back to uppercased raw — no crash.
	assert.Equal(t, symbol.Symbol("BTCUSDT"), got)
}

func TestSymbol_String(t *testing.T) {
	s := symbol.Symbol("BTC-USDT")
	assert.Equal(t, "BTC-USDT", s.String())
}

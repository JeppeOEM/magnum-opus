package symbol_test

import (
	"testing"

	"github.com/mrqdt/magnum-opus/candle-service/internal/symbol"
	"github.com/stretchr/testify/assert"
)

func TestNew_Fields(t *testing.T) {
	s := symbol.New("kucoin", "BTC-USDT")
	assert.Equal(t, "kucoin", s.Exchange)
	assert.Equal(t, "BTC-USDT", s.Raw)
}

func TestSymbol_String(t *testing.T) {
	s := symbol.New("bybit", "ETHUSDT")
	assert.Equal(t, "bybit:ETHUSDT", s.String())
}

func TestSymbol_StringAsMapKey(t *testing.T) {
	m := map[string]int{}
	s1 := symbol.New("kucoin", "BTC-USDT")
	s2 := symbol.New("kucoin", "BTC-USDT")
	m[s1.String()]++
	m[s2.String()]++
	assert.Equal(t, 2, m["kucoin:BTC-USDT"])
}

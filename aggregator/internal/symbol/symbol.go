// Package symbol provides a canonical Symbol type that normalizes
// exchange-specific formats to a single representation.
// Zero imports from any other internal package.
package symbol

import "strings"

// Symbol is the canonical normalized representation of a trading pair.
// Example: both KuCoin "BTC-USDT" and Bybit "BTCUSDT" normalize to "BTC-USDT".
type Symbol string

// Normalize converts an exchange-specific raw symbol string to a canonical Symbol.
// KuCoin uses "BASE-QUOTE"; Bybit uses "BASEQUOTE". Both produce "BASE-QUOTE".
func Normalize(exchange, raw string) Symbol {
	switch strings.ToLower(exchange) {
	case "kucoin":
		// KuCoin already uses dash-separated format: "BTC-USDT"
		return Symbol(strings.ToUpper(raw))
	case "bybit":
		// Bybit uses concatenated format: "BTCUSDT" → "BTC-USDT"
		return fromBybit(raw)
	default:
		return Symbol(strings.ToUpper(raw))
	}
}

// knownQuotes lists quote currencies ordered longest-first to avoid
// ambiguous prefix matches (e.g. "USDT" before "USD").
var knownQuotes = []string{
	"USDT", "USDC", "BUSD", "TUSD", "USDP",
	"BTC", "ETH", "BNB",
	"USD", "EUR", "GBP",
}

// fromBybit splits a Bybit concatenated symbol into "BASE-QUOTE".
// Falls back to the raw value (uppercased) if no known quote matches.
func fromBybit(raw string) Symbol {
	upper := strings.ToUpper(raw)
	for _, q := range knownQuotes {
		if strings.HasSuffix(upper, q) {
			base := upper[:len(upper)-len(q)]
			if base != "" {
				return Symbol(base + "-" + q)
			}
		}
	}
	return Symbol(upper)
}

// String returns the canonical string representation.
func (s Symbol) String() string { return string(s) }

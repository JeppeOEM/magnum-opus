package config

import (
	"fmt"
	"log/slog"
	"os"
	"strconv"
	"strings"
)

// Credential is a sealed type for API keys, secrets, and passphrases.
// It deliberately has no fmt.Stringer or error implementation so the raw
// value cannot escape into logs or error messages.
type Credential struct {
	v string
}

func newCredential(raw string) Credential { return Credential{v: raw} }

// Value returns the raw credential string. Call sites are limited to the
// exchange adapters at connection time — nowhere else.
func (c Credential) Value() string { return c.v }

// Format implements fmt.Formatter, intercepting ALL fmt verbs (%v, %+v, %#v, %s, %q, etc.)
// so that no format call can expose the raw credential value.
func (c Credential) Format(f fmt.State, verb rune) {
	fmt.Fprint(f, "[REDACTED]")
}

// GoString overrides %#v formatting to prevent accidental exposure.
func (c Credential) GoString() string { return "Credential{REDACTED}" }

// MarshalText prevents JSON/text marshaling from leaking the value.
func (c Credential) MarshalText() ([]byte, error) { return []byte("[REDACTED]"), nil }

// LogValue implements slog.LogValuer so the credential is always redacted in slog output.
func (c Credential) LogValue() slog.Value { return slog.StringValue("[REDACTED]") }

// Config holds all service parameters loaded from environment variables.
type Config struct {
	KuCoin  KuCoinConfig
	Bybit   BybitConfig
	Redis   RedisConfig
	QuestDB QuestDBConfig
	Symbols SymbolConfig
	Service ServiceConfig
}

type KuCoinConfig struct {
	APIKey      Credential
	APISecret   Credential
	Passphrase  Credential
}

type BybitConfig struct {
	APIKey    Credential
	APISecret Credential
}

type RedisConfig struct {
	Addr       string
	Password   Credential
	StreamMaxLen int64
}

type QuestDBConfig struct {
	ILPAddr  string
	HTTPAddr string
}

type SymbolConfig struct {
	KuCoin []string
	Bybit  []string
}

type ServiceConfig struct {
	LogLevel          string
	HTTPAddr          string
	StartupTimeoutSec int
}

// Load reads all configuration from environment variables.
// Returns an error listing all missing required variables.
func Load() (*Config, error) {
	missing := []string{}

	req := func(key string) Credential {
		v := os.Getenv(key)
		if v == "" {
			missing = append(missing, key)
		}
		return newCredential(v)
	}

	reqStr := func(key string) string {
		v := os.Getenv(key)
		if v == "" {
			missing = append(missing, key)
		}
		return v
	}

	optStr := func(key, def string) string {
		if v := os.Getenv(key); v != "" {
			return v
		}
		return def
	}

	optPosInt := func(key string, def int) int {
		v := os.Getenv(key)
		if v == "" {
			return def
		}
		n, err := strconv.Atoi(v)
		if err != nil || n <= 0 {
			missing = append(missing, key+" (must be positive integer)")
			return def
		}
		return n
	}

	optPosInt64 := func(key string, def int64) int64 {
		v := os.Getenv(key)
		if v == "" {
			return def
		}
		n, err := strconv.ParseInt(v, 10, 64)
		if err != nil || n <= 0 {
			missing = append(missing, key+" (must be positive integer)")
			return def
		}
		return n
	}

	splitSymbols := func(key string) []string {
		v := os.Getenv(key)
		if v == "" {
			return nil
		}
		parts := strings.Split(v, ",")
		out := make([]string, 0, len(parts))
		for _, p := range parts {
			if s := strings.TrimSpace(p); s != "" {
				out = append(out, s)
			}
		}
		return out
	}

	cfg := &Config{
		KuCoin: KuCoinConfig{
			APIKey:     req("KUCOIN_API_KEY"),
			APISecret:  req("KUCOIN_API_SECRET"),
			Passphrase: req("KUCOIN_API_PASSPHRASE"),
		},
		Bybit: BybitConfig{
			APIKey:    req("BYBIT_API_KEY"),
			APISecret: req("BYBIT_API_SECRET"),
		},
		Redis: RedisConfig{
			Addr:         reqStr("REDIS_ADDR"),
			Password:     newCredential(optStr("REDIS_PASSWORD", "")),
			StreamMaxLen: optPosInt64("REDIS_STREAM_MAXLEN", 50000),
		},
		QuestDB: QuestDBConfig{
			ILPAddr:  reqStr("QUESTDB_ILP_ADDR"),
			HTTPAddr: reqStr("QUESTDB_HTTP_ADDR"),
		},
		Symbols: SymbolConfig{
			KuCoin: splitSymbols("KUCOIN_SYMBOLS"),
			Bybit:  splitSymbols("BYBIT_SYMBOLS"),
		},
		Service: ServiceConfig{
			LogLevel:          optStr("LOG_LEVEL", "info"),
			HTTPAddr:          optStr("HTTP_ADDR", ":8080"),
			StartupTimeoutSec: optPosInt("STARTUP_TIMEOUT_SEC", 90),
		},
	}

	if len(missing) > 0 {
		return nil, fmt.Errorf("missing required environment variables: %s", strings.Join(missing, ", "))
	}

	return cfg, nil
}

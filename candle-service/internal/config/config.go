package config

import (
	"os"
	"strconv"
	"strings"
	"time"
)

// Config holds all runtime configuration for the candle service.
// Only this package reads os.Getenv.
type Config struct {
	Slot            string
	ServicePort     string
	ShutdownTimeout time.Duration
	LogLevel        string
	QuestDBHTTPAddr string
	// Redis
	RedisURL      string
	ConsumerGroup string
	// Symbols
	SymbolsKuCoin []string
	SymbolsByBit  []string
	// QuestDB ILP
	QuestDBILPAddr     string
	QuestDBILPFlushMs  int
	WALProbeIntervalS  int
	WALBufferSize      int
	// Consumer tuning
	ColdStartBufferSize int
	// Block trade classification
	BlockTradeWindow    int
	BlockTradeMinSample int
	// Redis stream max lengths
	CandleStreamMaxLen      int // CANDLE_STREAM_MAXLEN, default 10000
	CandleCloseStreamMaxLen int // CANDLE_CLOSE_STREAM_MAXLEN, default 500
	// Partial publish cadence
	CandlePartialPublishMs int // CANDLE_PARTIAL_PUBLISH_MS, default 250
}

// Load reads configuration from environment variables.
// Safe defaults are applied for every field.
func Load() Config {
	return Config{
		Slot:                getEnv("CANDLE_SLOT", "blue"),
		ServicePort:         getEnv("CANDLE_SERVICE_PORT", "8081"),
		ShutdownTimeout:     getDurationSeconds("SHUTDOWN_TIMEOUT_S", 10),
		LogLevel:            getEnv("LOG_LEVEL", "info"),
		QuestDBHTTPAddr:     getEnv("QUESTDB_HTTP_ADDR", "localhost:9000"),
		RedisURL:            getEnv("REDIS_URL", "redis://localhost:6379"),
		ConsumerGroup:       getEnv("CANDLE_CONSUMER_GROUP", "candle-service"),
		SymbolsKuCoin:       parseSymbolList(os.Getenv("SYMBOLS_KUCOIN")),
		SymbolsByBit:        parseSymbolList(os.Getenv("SYMBOLS_BYBIT")),
		QuestDBILPAddr:      getEnv("QUESTDB_ILP_ADDR", "localhost:9009"),
		QuestDBILPFlushMs:   getEnvInt("QUESTDB_ILP_FLUSH_MS", 500),
		WALProbeIntervalS:   getEnvInt("WAL_PROBE_INTERVAL_S", 5),
		WALBufferSize:       getEnvInt("WAL_BUFFER_SIZE", 10000),
		ColdStartBufferSize: getEnvInt("COLD_START_BUFFER_SIZE", 10000),
		BlockTradeWindow:        getEnvInt("BLOCK_TRADE_WINDOW", 1000),
		BlockTradeMinSample:     getEnvInt("BLOCK_TRADE_MIN_SAMPLE", 100),
		CandleStreamMaxLen:      getEnvInt("CANDLE_STREAM_MAXLEN", 10000),
		CandleCloseStreamMaxLen: getEnvInt("CANDLE_CLOSE_STREAM_MAXLEN", 500),
		CandlePartialPublishMs:  getEnvInt("CANDLE_PARTIAL_PUBLISH_MS", 250),
	}
}

func getEnv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func getDurationSeconds(key string, fallbackSecs int) time.Duration {
	if v := os.Getenv(key); v != "" {
		if n, err := strconv.Atoi(v); err == nil && n > 0 {
			return time.Duration(n) * time.Second
		}
	}
	return time.Duration(fallbackSecs) * time.Second
}

func getEnvInt(key string, fallback int) int {
	if v := os.Getenv(key); v != "" {
		if n, err := strconv.Atoi(v); err == nil && n > 0 {
			return n
		}
	}
	return fallback
}

// parseSymbolList splits a comma-separated symbol string, trimming spaces and
// dropping empty tokens.
func parseSymbolList(raw string) []string {
	if raw == "" {
		return nil
	}
	parts := strings.Split(raw, ",")
	out := make([]string, 0, len(parts))
	for _, p := range parts {
		if s := strings.TrimSpace(p); s != "" {
			out = append(out, s)
		}
	}
	return out
}

package config_test

import (
	"testing"
	"time"

	"github.com/mrqdt/magnum-opus/candle-service/internal/config"
	"github.com/stretchr/testify/assert"
)

func TestLoad_Defaults(t *testing.T) {
	t.Setenv("CANDLE_SLOT", "")
	t.Setenv("CANDLE_SERVICE_PORT", "")
	t.Setenv("SHUTDOWN_TIMEOUT_S", "")
	t.Setenv("LOG_LEVEL", "")
	t.Setenv("QUESTDB_HTTP_ADDR", "")
	t.Setenv("REDIS_URL", "")
	t.Setenv("CANDLE_CONSUMER_GROUP", "")
	t.Setenv("SYMBOLS_KUCOIN", "")
	t.Setenv("SYMBOLS_BYBIT", "")
	t.Setenv("QUESTDB_ILP_ADDR", "")
	t.Setenv("COLD_START_BUFFER_SIZE", "")
	t.Setenv("CANDLE_STREAM_MAXLEN", "")
	t.Setenv("CANDLE_CLOSE_STREAM_MAXLEN", "")
	t.Setenv("CANDLE_PARTIAL_PUBLISH_MS", "")

	cfg := config.Load()

	assert.Equal(t, "blue", cfg.Slot)
	assert.Equal(t, "8081", cfg.ServicePort)
	assert.Equal(t, 10*time.Second, cfg.ShutdownTimeout)
	assert.Equal(t, "info", cfg.LogLevel)
	assert.Equal(t, "localhost:9000", cfg.QuestDBHTTPAddr)
	assert.Equal(t, "redis://localhost:6379", cfg.RedisURL)
	assert.Equal(t, "candle-service", cfg.ConsumerGroup)
	assert.Nil(t, cfg.SymbolsKuCoin)
	assert.Nil(t, cfg.SymbolsByBit)
	assert.Equal(t, "localhost:9009", cfg.QuestDBILPAddr)
	assert.Equal(t, 10000, cfg.ColdStartBufferSize)
	assert.Equal(t, 10000, cfg.CandleStreamMaxLen)
	assert.Equal(t, 500, cfg.CandleCloseStreamMaxLen)
	assert.Equal(t, 250, cfg.CandlePartialPublishMs)
}

func TestLoad_EnvOverrides(t *testing.T) {
	t.Setenv("CANDLE_SLOT", "green")
	t.Setenv("CANDLE_SERVICE_PORT", "8082")
	t.Setenv("SHUTDOWN_TIMEOUT_S", "30")
	t.Setenv("LOG_LEVEL", "debug")
	t.Setenv("QUESTDB_HTTP_ADDR", "questdb:9000")
	t.Setenv("REDIS_URL", "redis://redis:6379")
	t.Setenv("CANDLE_CONSUMER_GROUP", "my-group")
	t.Setenv("SYMBOLS_KUCOIN", "BTC-USDT, ETH-USDT")
	t.Setenv("SYMBOLS_BYBIT", "BTCUSDT")
	t.Setenv("QUESTDB_ILP_ADDR", "questdb:9009")
	t.Setenv("COLD_START_BUFFER_SIZE", "500")

	cfg := config.Load()

	assert.Equal(t, "green", cfg.Slot)
	assert.Equal(t, "8082", cfg.ServicePort)
	assert.Equal(t, 30*time.Second, cfg.ShutdownTimeout)
	assert.Equal(t, "debug", cfg.LogLevel)
	assert.Equal(t, "questdb:9000", cfg.QuestDBHTTPAddr)
	assert.Equal(t, "redis://redis:6379", cfg.RedisURL)
	assert.Equal(t, "my-group", cfg.ConsumerGroup)
	assert.Equal(t, []string{"BTC-USDT", "ETH-USDT"}, cfg.SymbolsKuCoin)
	assert.Equal(t, []string{"BTCUSDT"}, cfg.SymbolsByBit)
	assert.Equal(t, "questdb:9009", cfg.QuestDBILPAddr)
	assert.Equal(t, 500, cfg.ColdStartBufferSize)
}

func TestLoad_SymbolList_IgnoresEmpty(t *testing.T) {
	t.Setenv("SYMBOLS_KUCOIN", "BTC-USDT,,ETH-USDT,")
	cfg := config.Load()
	assert.Equal(t, []string{"BTC-USDT", "ETH-USDT"}, cfg.SymbolsKuCoin)
}

func TestLoad_InvalidShutdownTimeout_UsesDefault(t *testing.T) {
	t.Setenv("SHUTDOWN_TIMEOUT_S", "notanumber")

	cfg := config.Load()

	assert.Equal(t, 10*time.Second, cfg.ShutdownTimeout)
}

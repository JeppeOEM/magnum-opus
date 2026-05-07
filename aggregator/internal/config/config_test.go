package config_test

import (
	"fmt"
	"log/slog"
	"os"
	"strings"
	"testing"

	"github.com/mrqdt/magnum-opus/aggregator/internal/config"
	"github.com/mrqdt/magnum-opus/aggregator/internal/testutil"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// TestMain registers the credential-redacting slog handler for all tests in this package.
// This ensures no credential value leaks through log output during test runs,
// even before Story 4.3 wires the production handler in main.go.
func TestMain(m *testing.M) {
	slog.SetDefault(slog.New(testutil.NewRedactingHandler(
		slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelDebug}),
	)))
	os.Exit(m.Run())
}

func setEnv(t *testing.T, pairs map[string]string) {
	t.Helper()
	for k, v := range pairs {
		t.Setenv(k, v)
	}
}

// writeConfigFile writes a temporary config.yaml and points CONFIG_FILE at it.
func writeConfigFile(t *testing.T, content string) {
	t.Helper()
	f, err := os.CreateTemp(t.TempDir(), "config-*.yaml")
	require.NoError(t, err)
	_, err = f.WriteString(content)
	require.NoError(t, err)
	require.NoError(t, f.Close())
	t.Setenv("CONFIG_FILE", f.Name())
}

const defaultTestConfig = `
exchanges:
  kucoin:
    symbols:
      - BTC-USDT
      - ETH-USDT
  bybit:
    symbols:
      - BTCUSDT
      - ETHUSDT
`

func fullEnv() map[string]string {
	return map[string]string{
		"KUCOIN_API_KEY":        "kc-key-abc123",
		"KUCOIN_API_SECRET":     "kc-secret-xyz",
		"KUCOIN_API_PASSPHRASE": "kc-passphrase",
		"BYBIT_API_KEY":         "bb-key-abc123",
		"BYBIT_API_SECRET":      "bb-secret-xyz",
		"REDIS_ADDR":            "localhost:6379",
		"REDIS_STREAM_MAXLEN":   "50000",
		"QUESTDB_ILP_ADDR":      "localhost:9009",
		"QUESTDB_HTTP_ADDR":     "localhost:9000",
	}
}

func TestLoad_Success(t *testing.T) {
	writeConfigFile(t, defaultTestConfig)
	setEnv(t, fullEnv())

	cfg, err := config.Load()
	require.NoError(t, err)
	require.NotNil(t, cfg)

	assert.Equal(t, "localhost:6379", cfg.Redis.Addr)
	assert.Equal(t, int64(50000), cfg.Redis.StreamMaxLen)
	assert.Equal(t, []string{"BTC-USDT", "ETH-USDT"}, cfg.Symbols.KuCoin)  // from config.yaml
	assert.Equal(t, []string{"BTCUSDT", "ETHUSDT"}, cfg.Symbols.Bybit)      // from config.yaml
	assert.Equal(t, "info", cfg.Service.LogLevel)
	assert.Equal(t, ":8080", cfg.Service.HTTPAddr)
	assert.Equal(t, 90, cfg.Service.StartupTimeoutSec)
}

func TestLoad_MissingRequired(t *testing.T) {
	writeConfigFile(t, defaultTestConfig)
	// No env vars set — only infrastructure vars are required.
	cfg, err := config.Load()
	assert.Nil(t, cfg)
	require.Error(t, err)

	for _, key := range []string{
		"REDIS_ADDR", "QUESTDB_ILP_ADDR", "QUESTDB_HTTP_ADDR",
	} {
		assert.Contains(t, err.Error(), key, "error should mention missing var %s", key)
	}
	// Exchange credentials are optional — must not appear in the error.
	for _, key := range []string{
		"KUCOIN_API_KEY", "KUCOIN_API_SECRET", "KUCOIN_API_PASSPHRASE",
		"BYBIT_API_KEY", "BYBIT_API_SECRET",
	} {
		assert.NotContains(t, err.Error(), key, "optional var %s must not appear in error", key)
	}
}

func TestLoad_KuCoinPublicMode(t *testing.T) {
	writeConfigFile(t, defaultTestConfig)
	// No KuCoin credentials → Public flag should be true.
	env := fullEnv()
	delete(env, "KUCOIN_API_KEY")
	delete(env, "KUCOIN_API_SECRET")
	delete(env, "KUCOIN_API_PASSPHRASE")
	setEnv(t, env)

	cfg, err := config.Load()
	require.NoError(t, err)
	assert.True(t, cfg.KuCoin.Public, "KuCoin should be in public mode when no credentials are set")
}

func TestLoad_KuCoinPrivateMode(t *testing.T) {
	writeConfigFile(t, defaultTestConfig)
	// All KuCoin credentials present → Public flag should be false.
	setEnv(t, fullEnv())

	cfg, err := config.Load()
	require.NoError(t, err)
	assert.False(t, cfg.KuCoin.Public, "KuCoin should be in private mode when credentials are set")
}

// TestCredential_CannotLeakViaFmt verifies that fmt.Sprintf and slog cannot
// expose the raw credential value — the sealed type must produce no output
// containing the raw string.
func TestCredential_CannotLeakViaFmt(t *testing.T) {
	writeConfigFile(t, defaultTestConfig)
	setEnv(t, fullEnv())

	cfg, err := config.Load()
	require.NoError(t, err)

	// The raw credential values we're watching for.
	sensitiveValues := []string{
		"kc-key-abc123",
		"kc-secret-xyz",
		"kc-passphrase",
		"bb-key-abc123",
		"bb-secret-xyz",
	}

	// Format the entire config struct every way Go allows.
	formatted := []string{
		fmt.Sprintf("%v", cfg.KuCoin),
		fmt.Sprintf("%+v", cfg.KuCoin),
		fmt.Sprintf("%#v", cfg.KuCoin),
		fmt.Sprintf("%s", cfg.KuCoin.APIKey),
		fmt.Sprintf("%v", cfg.KuCoin.APIKey),
		fmt.Sprintf("%q", cfg.KuCoin.APIKey),
	}

	for _, output := range formatted {
		for _, secret := range sensitiveValues {
			assert.NotContains(t, output, secret,
				"credential leaked via fmt formatting: %q", output)
		}
	}
}

// TestCredential_CannotLeakViaSlog verifies the slog path does not expose credentials.
func TestCredential_CannotLeakViaSlog(t *testing.T) {
	writeConfigFile(t, defaultTestConfig)
	setEnv(t, fullEnv())

	cfg, err := config.Load()
	require.NoError(t, err)

	var buf strings.Builder
	logger := slog.New(slog.NewTextHandler(&buf, &slog.HandlerOptions{Level: slog.LevelDebug}))

	// Attempt to log the credential directly.
	logger.Debug("config loaded",
		"kucoin_key", cfg.KuCoin.APIKey,
		"bybit_key", cfg.Bybit.APIKey,
	)
	logger.Debug("full kucoin config", "cfg", cfg.KuCoin)

	logOutput := buf.String()

	for _, secret := range []string{"kc-key-abc123", "kc-secret-xyz", "kc-passphrase", "bb-key-abc123", "bb-secret-xyz"} {
		assert.NotContains(t, logOutput, secret,
			"credential leaked via slog: output=%q", logOutput)
	}
}

func TestLoad_OptionalPassword_Empty(t *testing.T) {
	writeConfigFile(t, defaultTestConfig)
	env := fullEnv()
	// No REDIS_PASSWORD set — should succeed with empty credential.
	setEnv(t, env)

	cfg, err := config.Load()
	require.NoError(t, err)
	// Empty password is valid (no auth Redis).
	assert.Equal(t, "", cfg.Redis.Password.Value())
}

func TestLoad_Defaults(t *testing.T) {
	writeConfigFile(t, defaultTestConfig)
	setEnv(t, fullEnv())
	// Do not set optional vars — verify defaults apply.

	cfg, err := config.Load()
	require.NoError(t, err)
	assert.Equal(t, "info", cfg.Service.LogLevel)
	assert.Equal(t, ":8080", cfg.Service.HTTPAddr)
	assert.Equal(t, 90, cfg.Service.StartupTimeoutSec)
}

func TestLoad_EnvOverridesDefaults(t *testing.T) {
	writeConfigFile(t, defaultTestConfig)
	env := fullEnv()
	env["LOG_LEVEL"] = "debug"
	env["HTTP_ADDR"] = ":9090"
	env["STARTUP_TIMEOUT_SEC"] = "120"
	setEnv(t, env)

	cfg, err := config.Load()
	require.NoError(t, err)
	assert.Equal(t, "debug", cfg.Service.LogLevel)
	assert.Equal(t, ":9090", cfg.Service.HTTPAddr)
	assert.Equal(t, 120, cfg.Service.StartupTimeoutSec)
}

// Verify .env is listed in .gitignore (filesystem check, not a unit test per se).
func TestDotEnvInGitignore(t *testing.T) {
	data, err := os.ReadFile("../../.gitignore")
	require.NoError(t, err, ".gitignore must exist at aggregator root")
	assert.Contains(t, string(data), ".env", ".env must be listed in .gitignore")
}

// Package config loads gateway configuration from environment variables.
package config

import "os"

// Config holds all gateway runtime settings.
type Config struct {
	RedisAddr   string // Redis address for pub/sub subscription
	ListenAddr  string // HTTP/WebSocket listen address
}

// Load reads configuration from environment variables with sensible defaults.
func Load() Config {
	return Config{
		RedisAddr:  getEnv("REDIS_ADDR", "localhost:6379"),
		ListenAddr: getEnv("GATEWAY_ADDR", ":8083"),
	}
}

func getEnv(key, defaultVal string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return defaultVal
}

package config

import (
	"fmt"
	"os"

	"gopkg.in/yaml.v3"
)

// fileConfig is the parsed representation of config.yaml.
type fileConfig struct {
	Exchanges map[string]exchangeEntry `yaml:"exchanges"`
}

type exchangeEntry struct {
	Symbols []string `yaml:"symbols"`
}

// loadFileConfig reads a YAML config file and returns the parsed struct.
// Path is typically supplied via the CONFIG_FILE env var.
func loadFileConfig(path string) (*fileConfig, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read %s: %w", path, err)
	}
	var fc fileConfig
	if err := yaml.Unmarshal(data, &fc); err != nil {
		return nil, fmt.Errorf("parse %s: %w", path, err)
	}
	return &fc, nil
}

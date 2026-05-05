package main

import (
	"log/slog"
	"os"
)

// version, gitSHA, buildTime are injected at build time via -ldflags.
var (
	version   = "dev"
	gitSHA    = "unknown"
	buildTime = "unknown"
)

func main() {
	slog.SetDefault(slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{
		Level: slog.LevelInfo,
	})))

	slog.Info("aggregator starting",
		"version", version,
		"git_sha", gitSHA,
		"build_time", buildTime,
	)

	// Composition root wired in Story 4.3.
	// For now exit cleanly — all logic lives in internal/ packages.
	os.Exit(0)
}

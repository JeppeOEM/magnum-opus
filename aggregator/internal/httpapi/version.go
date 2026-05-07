package httpapi

// Build-time version vars. Set at build time via:
//
//	go build -ldflags "-X github.com/mrqdt/magnum-opus/aggregator/internal/httpapi.Version=1.0 ..."
var (
	Version   = "dev"
	GitSHA    = "unknown"
	BuildTime = "unknown"
)

module github.com/mrqdt/magnum-opus/aggregator

go 1.24

// Direct dependencies — run `make check-deps` before updating any entry.
// Re-verification command is documented in DEPS.md.
// gorilla/websocket is prohibited — use nhooyr.io/websocket (nhooyr is actively maintained).
require (
	github.com/prometheus/client_golang v1.23.2 // verified: 2026-05-05
	github.com/questdb/go-questdb-client/v3 v3.2.0 // verified: 2026-05-05
	github.com/redis/go-redis/v9 v9.19.0 // verified: 2026-05-05
	github.com/stretchr/testify v1.11.1 // verified: 2026-05-05
	nhooyr.io/websocket v1.8.17 // verified: 2026-05-05
)

require (
	github.com/cespare/xxhash/v2 v2.3.0 // indirect
	github.com/davecgh/go-spew v1.1.1 // indirect
	github.com/kr/pretty v0.3.1 // indirect
	github.com/pmezard/go-difflib v1.0.0 // indirect
	github.com/rogpeppe/go-internal v1.10.0 // indirect
	go.uber.org/atomic v1.11.0 // indirect
	gopkg.in/check.v1 v1.0.0-20201130134442-10cb98267c6c // indirect
	gopkg.in/yaml.v3 v3.0.1 // indirect
)

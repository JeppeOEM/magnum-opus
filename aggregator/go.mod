module github.com/mrqdt/magnum-opus/aggregator

go 1.24

// Direct dependencies — run `make check-deps` before updating any entry.
// Re-verification command and prohibited alternatives are documented in DEPS.md.
require (
	github.com/coder/websocket v1.8.14 // verified: 2026-05-06; replaces deprecated nhooyr.io/websocket
	github.com/questdb/go-questdb-client/v3 v3.2.0 // verified: 2026-05-06
	github.com/redis/go-redis/v9 v9.19.0 // verified: 2026-05-06
	github.com/stretchr/testify v1.11.1 // verified: 2026-05-05
)

require (
	github.com/prometheus/client_golang v1.23.2
	github.com/prometheus/client_model v0.6.2
)

require (
	github.com/beorn7/perks v1.0.1 // indirect
	github.com/cespare/xxhash/v2 v2.3.0 // indirect
	github.com/davecgh/go-spew v1.1.1 // indirect
	github.com/kylelemons/godebug v1.1.0 // indirect
	github.com/munnerz/goautoneg v0.0.0-20191010083416-a7dc8b61c822 // indirect
	github.com/pmezard/go-difflib v1.0.0 // indirect
	github.com/prometheus/common v0.66.1 // indirect
	github.com/prometheus/procfs v0.16.1 // indirect
	go.uber.org/atomic v1.11.0 // indirect
	go.yaml.in/yaml/v2 v2.4.2 // indirect
	golang.org/x/sys v0.35.0 // indirect
	google.golang.org/protobuf v1.36.8 // indirect
	gopkg.in/yaml.v3 v3.0.1 // indirect
)

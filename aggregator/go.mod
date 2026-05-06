module github.com/mrqdt/magnum-opus/aggregator

go 1.24

// Direct dependencies — run `make check-deps` before updating any entry.
// Re-verification command and prohibited alternatives are documented in DEPS.md.
require (
	github.com/coder/websocket v1.8.14 // verified: 2026-05-06; replaces deprecated nhooyr.io/websocket
	github.com/stretchr/testify v1.11.1 // verified: 2026-05-05
)

require (
	github.com/davecgh/go-spew v1.1.1 // indirect
	github.com/kr/pretty v0.3.1 // indirect
	github.com/pmezard/go-difflib v1.0.0 // indirect
	github.com/rogpeppe/go-internal v1.10.0 // indirect
	gopkg.in/check.v1 v1.0.0-20201130134442-10cb98267c6c // indirect
	gopkg.in/yaml.v3 v3.0.1 // indirect
)

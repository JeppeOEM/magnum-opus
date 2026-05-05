GOTEST := go test
COVER_THRESHOLD := 95

# ── L1: pure functions, no build tags, enforced coverage gate ─────────────────
.PHONY: test-l1
test-l1:
	@echo "==> L1 tests (orderbook, reconnect, gapdetector, symbol, backoff, config)"
	@$(GOTEST) -count=1 -coverprofile=coverage-l1.out \
		./internal/orderbook/... \
		./internal/reconnect/... \
		./internal/gapdetector/... \
		./internal/symbol/... \
		./internal/backoff/... \
		./internal/config/...
	@echo "==> Coverage gate ($(COVER_THRESHOLD)% branch floor for core packages)"
	@go tool cover -func=coverage-l1.out | grep -E 'orderbook|reconnect|gapdetector' \
		| awk '{print $$3}' \
		| sed 's/%//' \
		| awk 'BEGIN{fail=0} {if($$1+0 < $(COVER_THRESHOLD)) {print "FAIL: coverage " $$1 "% below $(COVER_THRESHOLD)%"; fail=1}} END{exit fail}'
	@echo "==> time.Now() / time.Sleep() ban (outside cmd/)"
	@! grep -rn --include="*.go" \
		-e 'time\.Now()' \
		-e 'time\.Sleep(' \
		./internal/ \
		| grep -v "_test.go" \
		| grep -v ':[[:space:]]*//' \
		| grep -v '/tools/'

# ── L2: mock/fake infrastructure, requires build tag ──────────────────────────
.PHONY: test-l2
test-l2:
	@echo "==> L2 tests (mock infrastructure)"
	$(GOTEST) -count=1 -tags l2 ./...

# ── L3: mock WebSocket server, exchange protocol tests ────────────────────────
.PHONY: test-l3
test-l3:
	@echo "==> L3 tests (mock WebSocket)"
	$(GOTEST) -count=1 -tags l3 ./...

# ── L4: fault injection via Toxiproxy ─────────────────────────────────────────
.PHONY: test-l4
test-l4:
	@echo "==> L4 tests (fault injection — requires running Toxiproxy)"
	$(GOTEST) -count=1 -tags l4 -timeout 5m ./...

# ── L5: live exchange (manual, requires credentials) ──────────────────────────
.PHONY: test-live
test-live:
	@echo "==> L5 live exchange tests (requires .env with valid credentials)"
	$(GOTEST) -count=1 -tags live -timeout 10m ./...

# ── All local layers (L1+L2+L3) ───────────────────────────────────────────────
.PHONY: test-all
test-all: test-l1 test-l2 test-l3

# ── Dependency maintenance check (ARC11) ──────────────────────────────────────
.PHONY: check-deps
check-deps:
	@echo "==> Checking direct dependencies for recent activity (ARC11)"
	@go list -m -json all | go run ./internal/tools/checkdeps/main.go

# ── Build ─────────────────────────────────────────────────────────────────────
.PHONY: build
build:
	go build -ldflags="-X main.version=$$(git describe --tags --always) \
		-X main.gitSHA=$$(git rev-parse HEAD) \
		-X main.buildTime=$$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
		-o bin/aggregator ./cmd/aggregator/

.PHONY: clean
clean:
	rm -rf bin/ coverage*.out

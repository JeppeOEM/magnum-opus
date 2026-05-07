GOTEST := go test
COVER_THRESHOLD := 95

# ── L1: pure functions, no build tags, enforced coverage gate ─────────────────
.PHONY: test-l1
test-l1:
	@echo "==> L1 tests (core + operational packages)"
	@$(GOTEST) -count=1 -coverprofile=coverage-l1.out \
		./internal/orderbook/... \
		./internal/reconnect/... \
		./internal/gapdetector/... \
		./internal/symbol/... \
		./internal/backoff/... \
		./internal/config/... \
		./internal/metrics/... \
		./internal/gapwindow/... \
		./internal/httpapi/... \
		./internal/slogredact/...
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
	@echo "==> init() logging ban (ARC9)"
	@files=$$(find ./internal ./cmd -name '*.go' -not -name '*_test.go' \
		| xargs grep -l 'func init()' 2>/dev/null \
		| xargs grep -lE 'slog\.|log\.' 2>/dev/null); \
	if [ -n "$$files" ]; then \
		echo "FAIL: init() functions contain logging calls in: $$files"; exit 1; \
	fi

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
	@echo "==> L4 tests (fault injection — requires docker-compose.test.yml running)"
	@./scripts/wait-for-toxiproxy.sh 30
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

# ── Verify deployed version matches git SHA ───────────────────────────────────
.PHONY: verify-versions
verify-versions:
	@addr=$${HTTP_ADDR:-http://localhost:8080}; \
	sha=$$(curl -sf "$$addr/version" | python3 -c "import sys,json; print(json.load(sys.stdin)['git_sha'])"); \
	expected=$$(git rev-parse HEAD); \
	if [ "$$sha" != "$$expected" ]; then \
		echo "FAIL: /version reports sha=$$sha, expected $$expected"; exit 1; \
	fi; \
	echo "OK: deployed SHA matches $$sha"

# ── Docker image build ────────────────────────────────────────────────────────
.PHONY: docker-build
docker-build:
	docker build \
		--build-arg VERSION=$$(git describe --tags --always) \
		--build-arg GIT_SHA=$$(git rev-parse HEAD) \
		--build-arg BUILD_TIME=$$(date -u +%Y-%m-%dT%H:%M:%SZ) \
		-t aggregator:$$(git rev-parse --short HEAD) \
		.

.PHONY: clean
clean:
	rm -rf bin/ coverage*.out

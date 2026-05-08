GOTEST := go test

# ── L1: unit tests, no external dependencies ──────────────────────────────────
.PHONY: test-l1
test-l1:
	@echo "==> L1 tests (config + health + cascade)"
	@$(GOTEST) -count=1 -race \
		./internal/config/... \
		./internal/health/... \
		./internal/cascade/...
	@echo "==> init() logging ban"
	@files=$$(find ./internal ./cmd -name '*.go' -not -name '*_test.go' \
		| xargs grep -l 'func init()' 2>/dev/null \
		| xargs grep -lE 'slog\.|log\.' 2>/dev/null); \
	if [ -n "$$files" ]; then \
		echo "FAIL: init() functions contain logging calls in: $$files"; exit 1; \
	fi

# ── L2: mock/fake infrastructure ──────────────────────────────────────────────
.PHONY: test-l2
test-l2:
	@echo "==> L2 tests (mock infrastructure)"
	$(GOTEST) -count=1 -tags l2 ./...

# ── L3: integration tests ─────────────────────────────────────────────────────
.PHONY: test-l3
test-l3:
	@echo "==> L3 tests (integration — requires docker-compose running)"
	$(GOTEST) -count=1 -tags l3 -timeout 5m ./...

# ── L4: fault injection ───────────────────────────────────────────────────────
.PHONY: test-l4
test-l4:
	@echo "==> L4 tests (fault injection — requires docker-compose running)"
	$(GOTEST) -count=1 -tags l4 -timeout 10m ./...

# ── All local layers (L1+L2) ──────────────────────────────────────────────────
.PHONY: test-all
test-all: test-l1 test-l2

# ── Build ─────────────────────────────────────────────────────────────────────
.PHONY: build
build:
	go build -ldflags="-X main.version=$$(git describe --tags --always) \
		-X main.gitSHA=$$(git rev-parse HEAD) \
		-X main.buildTime=$$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
		-o bin/candle ./cmd/candle/

# ── Docker image build ────────────────────────────────────────────────────────
.PHONY: docker-build
docker-build:
	docker build \
		--build-arg VERSION=$$(git describe --tags --always) \
		--build-arg GIT_SHA=$$(git rev-parse HEAD) \
		--build-arg BUILD_TIME=$$(date -u +%Y-%m-%dT%H:%M:%SZ) \
		-t candle-service:$$(git rev-parse --short HEAD) \
		.

# ── Verify deployed version matches git SHA ───────────────────────────────────
.PHONY: verify-versions
verify-versions:
	@addr=$${HTTP_ADDR:-http://localhost:8081}; \
	sha=$$(curl -sf "$$addr/version" | python3 -c "import sys,json; print(json.load(sys.stdin)['git_sha'])"); \
	expected=$$(git rev-parse HEAD); \
	if [ "$$sha" != "$$expected" ]; then \
		echo "FAIL: /version reports sha=$$sha, expected $$expected"; exit 1; \
	fi; \
	echo "OK: deployed SHA matches $$sha"

# ── Lint ─────────────────────────────────────────────────────────────────────
.PHONY: lint
lint:
	go vet ./...

.PHONY: clean
clean:
	rm -rf bin/ coverage*.out

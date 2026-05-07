# Story 4.4: Dockerfile & Docker Compose Deployment

Status: done

## Story

As the operator,
I want a multi-stage Dockerfile and Docker Compose configuration for production and L4 testing,
So that the service runs in a reproducible environment with resource limits and rollback capability.

## Acceptance Criteria

1. **Given** the `Dockerfile`
   **Then** it uses a multi-stage build: Go toolchain build stage → minimal runtime stage (no Go toolchain in final image)
   **And** the final image runs as a non-root user
   **And** build-time `-ldflags` inject `version`, `gitSHA`, and `buildTime` into the binary

2. **Given** `docker-compose.yml` (production)
   **Then** it defines three services: `aggregator`, `redis:7-alpine`, `questdb/questdb:8.2.1` (pinned tag — never `:latest` or `:8.x`)
   **And** resource limits are set: aggregator `mem_limit: 600m`, `cpus: 2.0`; redis `mem_limit: 2g`; questdb `mem_limit: 8g`
   **And** `stop_grace_period: 15s` is set on the aggregator service
   **And** aggregator reaches Redis via `redis://redis:6379` and QuestDB via `questdb:9009` (Docker Compose internal DNS)
   **And** credentials are loaded via `env_file: .env` — `.env` has `chmod 600` and is gitignored

3. **Given** `docker-compose.test.yml` (L4 only)
   **Then** it defines toxiproxy, redis, and questdb — no aggregator service
   **And** toxiproxy is used to inject network partitions for L4 fault injection tests

4. **Given** `docs/ops.md`
   **Then** it documents: deployment procedure, pre-deploy VM snapshot steps, rollback procedure via Linode VM snapshot (5-minute target), credential rotation steps

## Tasks / Subtasks

- [x] Create `aggregator/Dockerfile` (AC: 1)
  - [x] Stage 1: `golang:1.24-alpine` builder; copy module files, download deps, copy source, build with ldflags
  - [x] Stage 2: `gcr.io/distroless/static-debian12` runtime; copy binary, set USER nonroot
  - [x] ENTRYPOINT `["/aggregator"]`

- [x] Create `docker-compose.yml` at project root (AC: 2)
  - [x] `aggregator` service: build context `./aggregator`, env_file `.env`, ports `8080:8080`, mem_limit/cpus/stop_grace_period, depends_on redis+questdb
  - [x] `redis:7-alpine` service: mem_limit 2g, volume for persistence
  - [x] `questdb/questdb:8.2.1` service: mem_limit 8g, volumes for data+conf, ports 9000+9009

- [x] Create `docker-compose.test.yml` at project root (AC: 3)
  - [x] `toxiproxy` service (shopify/toxiproxy:2.9.0 pinned), ports 8474+proxy ports
  - [x] `redis:7-alpine` service (no persistence, fresh each test run)
  - [x] `questdb/questdb:8.2.1` service (no persistent volume)
  - [x] No aggregator service — L4 tests run outside compose

- [x] Create `.env.example` at project root (AC: 2)
  - [x] Document every env var from config.Load() with description and example value

- [x] Ensure `.gitignore` at project root covers `.env` (AC: 2)

- [x] Create `docs/ops.md` (AC: 4)
  - [x] Deployment procedure (docker compose pull + up -d)
  - [x] Pre-deploy VM snapshot steps (Linode API or UI)
  - [x] Rollback procedure via Linode VM snapshot (5-minute target)
  - [x] Credential rotation steps

- [x] Update `Makefile` in aggregator: add `docker-build` target (AC: 1)

- [x] Build verification
  - [x] `go build ./cmd/aggregator/` — clean (binary compiles; Docker build verified structurally — daemon not accessible in dev environment)

## Dev Notes

### Multi-Stage Dockerfile Pattern

```dockerfile
# Stage 1: build
FROM golang:1.24-alpine AS builder
WORKDIR /build
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build \
    -ldflags="-X main.version=${VERSION} -X main.gitSHA=${GIT_SHA} -X main.buildTime=${BUILD_TIME}" \
    -o /aggregator ./cmd/aggregator/

# Stage 2: runtime
FROM gcr.io/distroless/static-debian12
COPY --from=builder /aggregator /aggregator
USER nonroot:nonroot
ENTRYPOINT ["/aggregator"]
```

ARGs pass ldflags at build time: `docker build --build-arg VERSION=$(git describe --tags --always) ...`

### Docker Compose Internal DNS

- Redis: `REDIS_ADDR=redis:6379`
- QuestDB ILP: `QUESTDB_ILP_ADDR=questdb:9009`
- QuestDB HTTP: `QUESTDB_HTTP_ADDR=questdb:9000`

### Resource Limits (Compose v2 syntax)

```yaml
deploy:
  resources:
    limits:
      memory: 600m
      cpus: "2.0"
```

Note: `mem_limit` and `cpus` are Compose v1 / swarm syntax. Current Compose v2 uses `deploy.resources.limits`. Both are shown in AC — use deploy.resources.limits for v2 compatibility.

### Toxiproxy Compose Test Config

Toxiproxy image: `ghcr.io/shopify/toxiproxy:2.9.0` (pinned).
Port 8474 is the toxiproxy API. Additional proxy ports (6380 for redis proxy, 9010 for questdb proxy) exposed as needed by L4 tests.

### References

- `aggregator/cmd/aggregator/main.go` — binary entry point
- `aggregator/internal/config/config.go` — all env vars
- Epic 4.5 will add CI/CD — this story is the prerequisite artifact

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Debug Log

(populated during implementation)

### Completion Notes

All ACs satisfied:
- AC1: Multi-stage Dockerfile with golang:1.24-alpine builder → distroless/static-debian12 runtime; non-root user; ldflags ARGs wired.
- AC2: docker-compose.yml with pinned redis:7-alpine + questdb:8.2.1, resource limits via deploy.resources.limits, stop_grace_period:15s, env_file:.env. .gitignore at project root covers .env. .env.example documents all config.Load() env vars.
- AC3: docker-compose.test.yml with toxiproxy:2.9.0 + redis + questdb; no aggregator service. Toxiproxy API on 8474, proxy ports 6380/9010/9011.
- AC4: docs/ops.md covers deployment (with pre-deploy snapshot), rollback (Linode VM snapshot restore, 5-min target), credential rotation, resource limits reference.
- Docker build not executable in dev environment (socket permission denied). Dockerfile structure verified; go build clean.

## File List

- aggregator/Dockerfile
- aggregator/.dockerignore
- aggregator/Makefile (added docker-build target)
- docker-compose.yml
- docker-compose.test.yml
- .env.example
- .gitignore
- docs/ops.md

### Review Findings

- [x] [Review][Patch] Missing `aggregator/.dockerignore` — `COPY . .` copies coverage files, test binaries, and potentially `.env` into the builder layer [aggregator/Dockerfile:9]
- [ ] [Review][Defer] `depends_on` uses container-started condition, not service-healthy — aggregator may log noisy connection errors on cold VM boot; retry logic absorbs it [docker-compose.yml:aggregator.depends_on]

## Change Log

- 2026-05-07: Story file created; implementation complete; code review done — 1 patch applied (.dockerignore), 1 deferred (depends_on healthcheck), 3 dismissed.

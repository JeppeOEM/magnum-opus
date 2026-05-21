---
id: 30-1
title: Gateway docker-compose service
epic: 30
status: done
---

# Story 30-1: Gateway docker-compose service

## Context

`gateway/` contains a fully implemented Go WebSocket hub that subscribes to Redis PubSub channels (`orderbook:*`, `candles1s:*`) and fans out updates to WebSocket clients that subscribe per-symbol. The binary compiles cleanly but is not wired into `docker-compose.yml` — no service definition, no Dockerfile. Clients (dashboard, bot-service, external subscribers) cannot connect to the gateway in any deployment.

## What to build

### `gateway/Dockerfile`

Multi-stage build matching the aggregator pattern. Gateway has no VERSION/GIT_SHA ldflags — omit the ARG blocks and pass no `-X` flags.

```dockerfile
FROM golang:1.24-alpine AS builder
WORKDIR /build
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -o /gateway ./cmd/gateway/

FROM gcr.io/distroless/static-debian12
COPY --from=builder /gateway /gateway
USER nonroot:nonroot
ENTRYPOINT ["/gateway"]
```

### `docker-compose.yml` — add gateway service

Add after the `bot` service block, before `dashboard`. No profile (gateway is always needed when aggregator and candle-service run). Port 8083.

```yaml
  gateway:
    build:
      context: ./gateway
    environment:
      REDIS_ADDR: redis:6379
      GATEWAY_ADDR: ":8083"
    ports:
      - "8083:8083"
    depends_on:
      redis:
        condition: service_healthy
    deploy:
      resources:
        limits:
          memory: 64m
          cpus: "0.5"
    restart: unless-stopped
```

## Acceptance Criteria

1. `docker build -f gateway/Dockerfile gateway/` succeeds with zero errors.
2. `gateway` service appears in `docker-compose config` output with port 8083 and no profile.
3. Gateway `depends_on` redis with `service_healthy` condition.
4. Resource limits: memory 64m, cpus 0.5.
5. No `VERSION`/`GIT_SHA`/`BUILD_TIME` ARG blocks in the Dockerfile (gateway has no version ldflags).

## Dev Notes

- Gateway config: `REDIS_ADDR` (default `localhost:6379`), `GATEWAY_ADDR` (default `:8083`). See `gateway/internal/config/config.go`.
- Gateway binary: `cmd/gateway/main.go`. No version variables — skip the `-ldflags` entirely.
- Pattern: match the aggregator Dockerfile exactly except remove ARG/ldflags.
- The gateway reconnects to Redis PubSub automatically on drop (1s retry loop in `runSubscriber`).

### Review Findings

- [x] [Review][Defer] Healthcheck not wired — distroless image has no wget/curl; same pattern as aggregator. Would require bundling a static binary or switching to alpine runtime [`docker-compose.yml`] — deferred, pre-existing pattern
- [x] [Review][Defer] Memory limit 64m not load-benchmarked — deferred, pre-existing concern
- [x] [Review][Defer] distroless/static-debian12 tag not pinned to digest — deferred, matches aggregator pattern
- [x] [Review][Defer] runSubscriber 1s shutdown delay — deferred, pre-existing code
- [x] [Review][Defer] WS frame symLen overflow path — deferred, pre-existing code
- [x] [Review][Defer] hub.Route() silent drop on full channel — deferred, pre-existing code

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### File List

- `gateway/Dockerfile`
- `docker-compose.yml`

# Dependency Management

## Direct Dependencies

| Package | Version | Purpose | Verified |
|---|---|---|---|
| `nhooyr.io/websocket` | v1.8.17 | WebSocket transport — context-native, actively maintained. Replaces archived `gorilla/websocket`. | 2026-05-05 |
| `github.com/redis/go-redis/v9` | v9.19.0 | Redis Streams client (XADD, XREAD, consumer groups) | 2026-05-05 |
| `github.com/questdb/go-questdb-client/v3` | v3.2.0 | QuestDB ILP write client (port 9009 TCP line protocol) | 2026-05-05 |
| `github.com/prometheus/client_golang` | v1.23.2 | Prometheus metrics registry and `/metrics` handler | 2026-05-05 |
| `github.com/stretchr/testify` | v1.11.1 | Test assertions (`assert`, `require`) | 2026-05-05 |

## Maintenance Policy (ARC11)

All direct dependencies must be actively maintained at the time of adoption **and** on every update:

- Not archived on GitHub
- At least one commit within the last 12 months
- Active maintainer (not bot-only activity)

The canonical example of this rule: `nhooyr.io/websocket` is used instead of `gorilla/websocket` because gorilla was archived in 2022 with no security patches since.

## Re-verification Command

Before any dependency update, run:

```bash
make check-deps
```

This runs `go list -m -json all` and checks each direct dependency for recent commit activity. It is also run in CI on every PR via `ci.yml`.

## Prohibited Dependencies

- `github.com/gorilla/websocket` — archived, no security patches
- Any dependency with no commit activity in the last 12 months (flag and evaluate before adding)

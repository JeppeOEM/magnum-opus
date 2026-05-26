# Story R.2: Extract Pipeline Repo (aggregator + candle-service)

Status: done

## Story

As a service operator,
I want `aggregator` and `candle-service` to live in their own independent git repo (`magnum-opus-pipeline`)
with a standalone docker-compose, Makefile, and CI pipeline,
so I can record data continuously and deploy candle-service independently without the trading/consumer codebase being involved.

## Context

Current state: monorepo at `magnum-opus/` contains aggregator, candle-service, dashboard, and future bot/ML code.

Target state:
- **`magnum-opus-pipeline`** — aggregator + candle-service + their infrastructure (Redis, QuestDB, Grafana, Prometheus). Deployable on its own. This repo runs 24/7 recording data.
- **`magnum-opus-trading`** (Story R.3) — everything that consumes data. Depends on the pipeline being up; connects via `REDIS_URL` + `QUESTDB_URL`.

The split boundary is the Redis stream interface and QuestDB tables. No code is shared between repos at the import level (already true — the Go modules are already separate).

## Acceptance Criteria

### AC 1 — New repo created with full git history

1. A new GitHub repository `magnum-opus-pipeline` is created (private).
2. The repo is populated using `git subtree split` or `git filter-repo` to extract the following paths with their full commit history:
   - `aggregator/`
   - `candle-service/`
   - `monitoring/` (Grafana + Prometheus configs)
   - `scripts/` (deploy-candle.sh)
   - `docker-compose.yml`
   - `Makefile` (root — trimmed to pipeline-relevant targets only)
   - `.env.example`
3. Git blame, log, and bisect work correctly on all extracted files.
4. The original `magnum-opus` monorepo is **not deleted** — it remains the source of truth until Story R.3 is complete.

### AC 2 — Standalone docker-compose.yml

5. `docker-compose.yml` in the pipeline repo brings up the full pipeline stack with a single command:
   ```bash
   docker compose up -d
   ```
   Services included:
   - `aggregator`
   - `candle-blue` (active slot on port 8081)
   - `candle-green` (shadow slot on port 8082, started only for deploys)
   - `redis`
   - `questdb`
   - `grafana`
   - `prometheus`
6. No dashboard, no bot, no ML service in this compose file.
7. External ports exposed: Redis `6379`, QuestDB ILP `9009`, QuestDB HTTP `9000`, Grafana `3000`. These are the ports Repo 2 consumers connect to.

### AC 3 — Standalone Makefile

8. `Makefile` at repo root has at minimum:

   | Target | Action |
   |---|---|
   | `make up` | `docker compose up -d` |
   | `make down` | `docker compose down` |
   | `make deploy` | `./scripts/deploy-candle.sh green blue` (or blue green) |
   | `make logs` | `docker compose logs -f aggregator candle-blue` |
   | `make test` | `cd aggregator && go test ./... && cd ../candle-service && go test ./...` |
   | `make build` | `docker compose build aggregator candle-blue candle-green` |

9. No frontend, dashboard, or bot targets in this Makefile.

### AC 4 — .env.example

10. `.env.example` at repo root documents all required environment variables for running the pipeline. At minimum:
    ```
    # Exchange credentials
    KUCOIN_API_KEY=
    KUCOIN_API_SECRET=
    KUCOIN_API_PASSPHRASE=
    BYBIT_API_KEY=
    BYBIT_API_SECRET=

    # Storage
    QUESTDB_ILP_ADDR=questdb:9009
    QUESTDB_HTTP_ADDR=questdb:9000
    REDIS_URL=redis://redis:6379

    # Candle service
    CANDLE_SLOT=blue
    CANDLE_SHADOW_MODE=false
    SYMBOLS_KUCOIN=BTC-USDT,ETH-USDT
    SYMBOLS_BYBIT=BTCUSDT,ETHUSDT

    # Optional: Backblaze B2 cold storage
    B2_KEY_ID=
    B2_APP_KEY=
    B2_BUCKET=
    ```

### AC 5 — GitHub Actions CI

11. `.github/workflows/ci.yml` runs on every push to `main` and every pull request:
    ```yaml
    jobs:
      test:
        runs-on: ubuntu-latest
        steps:
          - uses: actions/checkout@v4
          - uses: actions/setup-go@v5
            with: { go-version: '1.23' }
          - run: cd aggregator && go test ./...
          - run: cd candle-service && go test ./...
          - run: cd aggregator && go build ./...
          - run: cd candle-service && go build ./...
    ```
12. CI does NOT run integration tests (those require Redis + QuestDB via Docker) — unit tests only in CI.
13. CI badge added to `README.md`.

### AC 6 — Independent versioning

14. A `VERSION` file at repo root contains the current semantic version (start at `1.0.0`).
15. Git tags follow `pipeline-v{MAJOR}.{MINOR}.{PATCH}` (e.g. `pipeline-v1.0.0`).
16. `make release VERSION=1.1.0` target: updates `VERSION` file, commits, and creates the git tag.
17. Docker images are tagged `aggregator:{version}` and `candle-service:{version}` on release. (Local build only — no registry push required in this story.)

### AC 7 — README

18. `README.md` updated for the pipeline repo context:
    - Describes what the repo is (data pipeline only)
    - Quick start: `cp .env.example .env && make up`
    - Section: "What this exposes" — Redis port, QuestDB port, stream keys, table names
    - Section: "Deploying candle-service" — `make deploy`
    - Section: "Consumer repos" — "Connect your bots/dashboard to `REDIS_URL=localhost:6379` and `QUESTDB_URL=localhost:9000`"

### AC 8 — Monorepo not broken

19. The original `magnum-opus` monorepo continues to build and test cleanly after this story (`go test ./...` passes in both `aggregator/` and `candle-service/`).
20. No files are deleted from the monorepo in this story. Story R.3 handles the consumer-side extraction.

## Dev Notes

### git subtree split approach (recommended)

```bash
# From the monorepo root — creates a branch containing only aggregator/ history
git subtree split --prefix=aggregator -b split-aggregator

# Push to new repo
cd /tmp
git clone --bare /path/to/magnum-opus magnum-opus-pipeline
cd magnum-opus-pipeline
git filter-branch --subdirectory-filter aggregator -- --all
# Repeat for candle-service, scripts, monitoring, docker-compose.yml

# Alternative: git filter-repo (faster, cleaner)
pip install git-filter-repo
git clone magnum-opus magnum-opus-pipeline
cd magnum-opus-pipeline
git filter-repo --path aggregator/ --path candle-service/ --path monitoring/ \
    --path scripts/ --path docker-compose.yml --path Makefile
```

`git filter-repo` is preferred — it handles multiple paths in one pass and is faster than `filter-branch`.

### What stays in magnum-opus (monorepo) for now

- `dashboard/` — stays until R.3
- `docs/` — stays (shared reference)
- `_bmad-output/` — stays (planning artifacts)
- `.claude/` — stays (Claude context)

### Port exposure contract

These ports are the public interface that consumer repos depend on:

| Port | Service | Protocol |
|---|---|---|
| 6379 | Redis | Redis protocol |
| 9000 | QuestDB | HTTP (SQL queries) |
| 9009 | QuestDB | ILP (write-only, pipeline internal) |
| 3000 | Grafana | HTTP |

Consumer repos should only need 6379 and 9000.

## Tasks / Subtasks

- [ ] Create `magnum-opus-pipeline` GitHub repo (private)
- [ ] Use `git filter-repo` to extract aggregator, candle-service, monitoring, scripts, docker-compose.yml with full history
- [ ] Push extracted history to new repo
- [ ] Audit and trim `docker-compose.yml` to pipeline-only services (AC 2)
- [ ] Write pipeline-specific `Makefile` (AC 3)
- [ ] Write `.env.example` (AC 4)
- [ ] Add `.github/workflows/ci.yml` (AC 5)
- [ ] Add `VERSION` file + `make release` target (AC 6)
- [ ] Update `README.md` for pipeline repo (AC 7)
- [ ] Verify `go test ./...` still passes in original monorepo (AC 8)

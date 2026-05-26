# Story R.3: Create Consumer Repo Skeleton (magnum-opus-trading)

Status: done

## Story

As a developer building bots and ML models,
I want a separate `magnum-opus-trading` repo that connects to the pipeline via env vars
and has its own docker-compose, CI, and versioning,
so that I can iterate on trading logic without touching or restarting the data pipeline.

## Context

Depends on: Story R.2 (pipeline repo must exist and be running before this repo is useful).

This repo is the home for:
- Trading bots (bot manager, kill switch)
- ML service
- Dashboard (Dash/Plotly — currently in `dashboard/`)
- Any future consumer of Redis streams or QuestDB tables

It connects to `magnum-opus-pipeline` only through:
- `REDIS_URL` — reads tick and candle streams
- `QUESTDB_URL` — queries historical data for backtesting and ML

No code imports between repos. No shared Docker networks at the container level — consumers connect to pipeline's exposed ports on `localhost` (or a remote host if different machines).

## Acceptance Criteria

### AC 1 — New repo created

1. A new GitHub repository `magnum-opus-trading` is created (private).
2. Initial commit includes the skeleton structure below — no code extracted from monorepo yet (that happens incrementally as features are built).
3. The dashboard code from `magnum-opus/dashboard/` is moved to this repo in this story (it is the most mature consumer).

### AC 2 — Directory structure

4. Initial repo layout:
   ```
   magnum-opus-trading/
   ├── .env.example
   ├── .github/workflows/ci.yml
   ├── docker-compose.yml
   ├── Makefile
   ├── README.md
   ├── VERSION
   └── dashboard/          ← moved from monorepo
       ├── Dockerfile
       ├── app.py
       ├── callbacks_*.py
       └── requirements.txt
   ```
5. Placeholder directories (empty with `.gitkeep`):
   - `bots/` — future bot manager
   - `ml/` — future ML service

### AC 3 — .env.example

6. `.env.example` documents the pipeline connection config:
   ```
   # Pipeline connection (magnum-opus-pipeline must be running)
   REDIS_URL=redis://localhost:6379
   QUESTDB_URL=http://localhost:9000

   # Dashboard
   DASHBOARD_PORT=8050
   ```
7. No exchange API keys in this repo — those belong to the pipeline repo.
8. The `.env` file (populated from `.env.example`) is in `.gitignore`.

### AC 4 — docker-compose.yml

9. `docker-compose.yml` brings up only consumer services:
   ```yaml
   services:
     dashboard:
       build: ./dashboard
       ports:
         - "${DASHBOARD_PORT:-8050}:8050"
       environment:
         - REDIS_URL=${REDIS_URL:-redis://localhost:6379}
         - QUESTDB_URL=${QUESTDB_URL:-http://localhost:9000}
       network_mode: host   # simplest on a single machine — connects to pipeline's localhost ports
   ```
10. `network_mode: host` is used because both repos run on the same machine. Consumer services connect to `localhost:6379` (Redis) and `localhost:9000` (QuestDB) which the pipeline exposes.
11. No Redis, QuestDB, or aggregator services in this compose file — those are owned by `magnum-opus-pipeline`.

### AC 5 — Makefile

12. `Makefile` at repo root:

    | Target | Action |
    |---|---|
    | `make up` | `docker compose up -d` |
    | `make down` | `docker compose down` |
    | `make logs` | `docker compose logs -f dashboard` |
    | `make dashboard` | `docker compose up -d dashboard` |
    | `make test` | `cd dashboard && python -m pytest tests/ -q` (if tests exist) |

### AC 6 — Dashboard moved from monorepo

13. `dashboard/` directory is moved from `magnum-opus/dashboard/` to `magnum-opus-trading/dashboard/` with full git history preserved (use `git mv` in the monorepo, then extract).
14. Dashboard `Dockerfile` updated if needed to work standalone (no monorepo path assumptions).
15. Dashboard `REDIS_URL` and `QUESTDB_URL` read from environment variables — no hardcoded `localhost` strings.
16. `make up && make dashboard` brings up the dashboard and it connects to a running pipeline.

### AC 7 — GitHub Actions CI

17. `.github/workflows/ci.yml`:
    ```yaml
    jobs:
      lint-dashboard:
        runs-on: ubuntu-latest
        steps:
          - uses: actions/checkout@v4
          - uses: actions/setup-python@v5
            with: { python-version: '3.11' }
          - run: pip install -r dashboard/requirements.txt
          - run: python -m py_compile dashboard/app.py dashboard/callbacks_*.py
    ```
18. CI validates that dashboard Python files parse cleanly. No runtime tests (those require a running pipeline).

### AC 8 — README

19. `README.md` documents:
    - What this repo is (trading consumers)
    - Prerequisite: `magnum-opus-pipeline` must be running (`REDIS_URL`, `QUESTDB_URL`)
    - Quick start: `cp .env.example .env && make up`
    - Section: "What this consumes" — Redis stream keys and QuestDB tables it reads
    - Section: "Adding a new consumer" — drop it in a subdirectory, add to docker-compose.yml

### AC 9 — Monorepo dashboard removed (optional, do last)

20. After the dashboard is confirmed working in `magnum-opus-trading`, remove `dashboard/` from the `magnum-opus` monorepo in a follow-up commit.
21. This AC is last — don't remove from monorepo until the trading repo dashboard is verified working.

## Dev Notes

### Why network_mode: host

On a single-developer machine running both repos, `network_mode: host` is the simplest way for consumer containers to reach pipeline services on `localhost:6379` and `localhost:9000`. No Docker network bridge configuration needed.

If in future the pipeline moves to a different machine, change `REDIS_URL` and `QUESTDB_URL` in `.env` to point at the remote host. No code changes required.

### Dashboard env vars to check

The current dashboard at `magnum-opus/dashboard/` may have hardcoded connection strings. Before moving, audit:
```bash
grep -r "localhost\|6379\|9000\|questdb\|redis" dashboard/
```
Any hardcoded connection strings must be replaced with `os.environ.get('REDIS_URL', 'redis://localhost:6379')` equivalents.

### Version independence

`magnum-opus-trading` has its own `VERSION` file starting at `1.0.0` with tags `trading-v1.0.0`. Completely independent of `pipeline-v*` tags.

## Tasks / Subtasks

- [ ] Create `magnum-opus-trading` GitHub repo (private)
- [ ] Initialize repo with skeleton structure (AC 2)
- [ ] Write `.env.example` (AC 3)
- [ ] Write `docker-compose.yml` with `network_mode: host` (AC 4)
- [ ] Write `Makefile` (AC 5)
- [ ] Audit dashboard for hardcoded connection strings (AC 6)
- [ ] Move `dashboard/` from monorepo to trading repo with history (AC 6)
- [ ] Add `.github/workflows/ci.yml` (AC 7)
- [ ] Write `README.md` (AC 8)
- [ ] Verify `make up && make dashboard` connects to running pipeline (AC 6)
- [ ] Remove `dashboard/` from monorepo after verification (AC 9)

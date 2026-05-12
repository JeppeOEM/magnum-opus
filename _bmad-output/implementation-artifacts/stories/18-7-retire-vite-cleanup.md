# Story 18.7: Retire Vite Frontend

Status: done

## Story

As a developer,
I want the old Vite/TypeScript frontend removed from the repository and Makefile,
So that there is a single, unambiguous frontend entry point at port 8050.

**Pre-conditions:** Story 18.6 complete and signed off. Dashboard verified working end-to-end.

## Acceptance Criteria

**AC 1 — frontend/ directory deleted**
- `frontend/` directory no longer exists in the repository root

**AC 2 — Makefile cleaned**
- `dev-frontend` target removed from Makefile
- References to `frontend`, port 5173, and Vite removed from the `run` target and `help` output
- `dev-frontend` removed from the `.PHONY` list

**AC 3 — README updated**
- Frontend section references port 8050 and the Dash dashboard
- No mention of Vite, port 5173, TypeScript frontend, or `npm run dev` in README
- Node.js prerequisite removed

**AC 4 — Dashboard still works**
- `curl localhost:8050` returns 200 after changes

## Tasks / Subtasks

- [x] Task 1: Delete `frontend/` directory
  - [x] 1.1 Remove `frontend/` from git (`git rm -r frontend/`)

- [x] Task 2: Update Makefile
  - [x] 2.1 Remove `dev-frontend` target and its comment
  - [x] 2.2 Remove `dev-frontend` from `.PHONY` line
  - [x] 2.3 Remove frontend printf line from `run` target
  - [x] 2.4 Remove `( cd frontend && npm run dev ) & FRONTEND=$$!;` and `$$FRONTEND` references from `run` target

- [x] Task 3: Update README.md
  - [x] 3.1 Replace Frontend section content with Dash dashboard reference (port 8050)
  - [x] 3.2 Remove Node.js prerequisite from requirements
  - [x] 3.3 Update TOC entry from Frontend to Dashboard

- [x] Task 4: Verify
  - [x] 4.1 Container returns 200 on port 8050
  - [x] 4.2 `frontend/` directory gone from repo root

## Dev Notes

### docker-compose.yml — no change needed
`docker-compose.yml` has no Vite/frontend service — it was never added to compose. Only Makefile `run` target ran frontend.

### Makefile targets to remove/update

The `run` target (line ~181) has:
- `@printf   "  %-14s %s\n"  "frontend"       "http://localhost:5173   heatmap: /heatmap.html"` — remove
- `( cd frontend && npm run dev ) & FRONTEND=$$!;` — remove
- `$$FRONTEND` in trap and wait — remove

The `dev-frontend` target (line ~170) and its block comment — remove entirely.

The `.PHONY` line (line ~10) includes `dev-frontend` — remove from list.

### README changes
- Remove the `## Frontend` section or replace its content with dashboard reference
- Remove Node.js from prerequisites
- Add dashboard service mention in running section if not already present

### Files to touch
- `frontend/` — delete entire directory
- `Makefile` — remove frontend references
- `README.md` — update Frontend section

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Debug Log References

### Completion Notes List

- frontend/ removed via git rm -r; node_modules/dist (untracked) removed via rm -rf
- Makefile: dev-frontend target removed, PHONY updated, run target cleaned (frontend printf + FRONTEND var + trap/wait entries removed)
- README.md: Frontend section replaced with Dashboard section (port 8050, Dash env vars); TOC updated; Node.js prerequisite removed
- docker-compose.yml: no change needed — Vite service was never added
- curl localhost:8050 returns 200; frontend/ confirmed gone

### File List

- Makefile
- README.md
- frontend/ (deleted)

### Review Findings

- [x] [Review][Defer] `make run` comment says "Start everything" but dashboard not launched or printed in service table — pre-existing; dashboard was never in `run` (only old Vite frontend was) [Makefile]

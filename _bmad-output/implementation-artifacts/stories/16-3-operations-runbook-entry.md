# Story 16.3: Operations Runbook Entry

Status: done

## Story

As mrqdt,
I want a runbook section for the bot service covering deploy, rollback, credential rotation, and alert playbooks,
so that I can operate the service without relying on memory or reverse-engineering logs.

## Acceptance Criteria

- **AC1:** Given `docs/ops.md`, when a new section `## Bot Service` is added, then it covers: Deploy, Rollback, Credential Rotation, and Alert Playbooks (bot_heartbeat_timeout_total, bot_strategy_restart_total, bot_orphaned_order_total) with concrete commands.

- **AC2:** Given `bot-service/DESIGN.md`, when inspected, then it contains a section documenting the intentional design decision about cross-strategy position concentration (two strategies can hold the same symbol with no portfolio-level cap).

## Tasks / Subtasks

- [x] T1: Add `## Bot Service` section to `docs/ops.md` (AC1)
  - [x] T1.1: Add Deploy subsection with pre-deploy checklist and compose command
  - [x] T1.2: Add Rollback subsection with tag-based rollback and /health verification
  - [x] T1.3: Add Credential Rotation subsection for all 5 exchange credentials
  - [x] T1.4: Add Alert Playbooks subsection for all three alert types
- [x] T2: Verify `bot-service/DESIGN.md` has cross-strategy concentration section (AC2)
  - [x] T2.1: DESIGN.md already exists and has the section — no change needed

## Dev Notes

### docs/ops.md location

The file lives at `docs/ops.md` relative to the repo root. Add the Bot Service section at the end.

### bot-service/DESIGN.md

Already created during Epic 13 implementation. Contains the cross-strategy position concentration decision. No modifications needed.

## Senior Developer Review (AI)

**Outcome:** Changes Requested  
**Date:** 2026-05-10  
**Patches applied:** 1

### Action Items

- [x] **[Med]** Tag-based rollback command `docker compose up -d bot --image $BOT_IMAGE` is not valid syntax for Docker Compose v2 — `--image` is not a recognized flag for `docker compose up`. Fix: describe editing `image:` in docker-compose.yml + `docker compose pull bot && docker compose up -d bot`.
- [ ] **[Low — Defer]** Alert playbook for `bot_heartbeat_timeout_total` references `bot_consumer_lag` metric to diagnose Redis lag, but no Grafana panel exists for consumer_lag yet. Add a panel in a future monitoring story.

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Completion Notes List

- Added `## Bot Service` section to `docs/ops.md` covering Deploy (pre-deploy checklist, compose command), Rollback (tag-based + git-based), Credential Rotation (5 credentials, no-downtime procedure), and 3 Alert Playbooks (heartbeat_timeout, strategy_restart, orphaned_order)
- Added bot service row to Resource Limits Reference table (2 GB)
- `bot-service/DESIGN.md` already had the Cross-Strategy Position Concentration section from Epic 13 — no changes needed
- No automated tests (documentation story)
- **Review patch 3:** Fixed invalid `docker compose up -d bot --image $BOT_IMAGE` rollback command — replaced with edit docker-compose.yml `image:` field + `docker compose pull bot && docker compose up -d bot`

### File List

- docs/ops.md (modified — added Bot Service section; review patch: fixed invalid rollback command)
- _bmad-output/implementation-artifacts/stories/16-3-operations-runbook-entry.md (updated)
- _bmad-output/implementation-artifacts/sprint-status.yaml (updated)

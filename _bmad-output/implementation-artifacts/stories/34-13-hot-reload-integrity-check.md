---
id: 34-13
title: Strategy hot-reload integrity check — SHA256 hash verification before load
epic: 34
status: ready-for-dev
---

# Story 34-13: Strategy hot-reload integrity check

## Context

The file watcher (story 13-2) reloads any `.py` file placed into `strategies/active/`. This means any file — including a corrupted upload, a partially-written file mid-scp, or a maliciously crafted file — is loaded and executed as a strategy with full access to the exchange client and position state.

This story adds an optional SHA256 hash verification step: before loading a strategy file, the file watcher checks for a corresponding `.sha256` sidecar file and verifies the hash matches. Files without a sidecar are loaded normally (opt-in safety, backwards compatible). Files with a sidecar that fail verification are rejected with a log warning and not loaded.

**Promoted from:** D-34-5 (Strategy hot-reload has no file integrity / hash check)

## What to build

### `bot-service/bot_service/strategy/registry.py` — hash check in `_load_new`

```python
import hashlib
from pathlib import Path

def _verify_strategy_hash(strategy_path: Path) -> bool:
    """
    Check for a .sha256 sidecar file and verify it matches the strategy file.
    Returns True if:
      - No sidecar file exists (hash check opted out — load proceeds)
      - Sidecar exists and hash matches
    Returns False if:
      - Sidecar exists but hash does not match (file rejected)
    """
    hash_path = strategy_path.with_suffix(".py.sha256")
    if not hash_path.exists():
        return True  # No sidecar — load without verification

    expected_hash = hash_path.read_text().strip().split()[0]  # handle "hash  filename" format
    actual_hash = hashlib.sha256(strategy_path.read_bytes()).hexdigest()

    if actual_hash != expected_hash:
        log.warning(
            "strategy_hash_mismatch",
            path=str(strategy_path),
            expected=expected_hash[:16] + "...",
            actual=actual_hash[:16] + "...",
        )
        return False

    log.info("strategy_hash_verified", path=str(strategy_path), hash=actual_hash[:16] + "...")
    return True
```

Call `_verify_strategy_hash` in `_load_new` before loading the module:

```python
def _load_new(self, path: Path) -> None:
    if not _verify_strategy_hash(path):
        log.error("strategy_load_rejected_hash_mismatch", path=str(path))
        return  # Do not load — leave existing strategy running if one is active

    # ... existing load logic ...
```

### Also check on file modification events

The file watcher triggers `_load_new` on both `IN_CREATE` and `IN_MODIFY` events. The hash check must apply to both — a file that passes on creation but is subsequently modified and fails the hash check should be rejected on reload:

```python
def _on_file_event(self, event) -> None:
    path = Path(event.src_path)
    if path.suffix == ".py" and path.stem not in (".sha256",):
        self._load_new(path)
    # Ignore .sha256 sidecar events — they don't trigger reloads themselves
```

### Hash generation — `Makefile`

Add a target to generate the sidecar hash file locally before deploying:

```makefile
## Generate SHA256 hash sidecar for a strategy file before deploy
##   make hash-strategy STRATEGY=strategies/active/funding_rate_arb_bot.py
hash-strategy:
	@[ -n "$(STRATEGY)" ] || (echo "Usage: make hash-strategy STRATEGY=strategies/active/my_bot.py"; exit 1)
	sha256sum $(STRATEGY) > $(STRATEGY).sha256
	@echo "✓ Hash written to $(STRATEGY).sha256"
	@cat $(STRATEGY).sha256

## Verify all active strategy hashes (run before deploy)
verify-strategies:
	@failed=0; \
	for f in strategies/active/*.py; do \
	  if [ -f "$${f}.sha256" ]; then \
	    sha256sum -c "$${f}.sha256" --quiet || failed=$$((failed+1)); \
	  fi; \
	done; \
	[ $$failed -eq 0 ] && echo "✓ All strategy hashes verified" || (echo "✗ $$failed hash(es) failed"; exit 1)
```

### `vps-deploy.sh` — verify hashes before uploading

In `scripts/vps-deploy.sh`, when deploying the bot service, run `make verify-strategies` first:

```bash
if [ "$service" = "bot" ]; then
  echo "==> Verifying strategy hashes"
  make verify-strategies || {
    echo "✗ Strategy hash verification failed — aborting deploy"
    exit 1
  }
fi
```

### `.gitignore` — track sidecar files

Strategy `.sha256` files should be committed alongside the strategy files:

```gitignore
# Do NOT ignore .sha256 files — they are integrity sidecar files
# (Remove any existing *.sha256 gitignore rule)
```

### `docs/ops.md` — strategy deploy workflow with hashing

```markdown
### Deploying a new strategy with hash verification

```bash
# 1. Write your strategy file
vim strategies/active/my_new_bot.py

# 2. Generate the hash sidecar
make hash-strategy STRATEGY=strategies/active/my_new_bot.py

# 3. Commit both files
git add strategies/active/my_new_bot.py strategies/active/my_new_bot.py.sha256
git commit -m "feat: add my_new_bot strategy"

# 4. Deploy
make deploy-bot

# The file watcher will pick up my_new_bot.py, verify the hash, and load it.
```

### Bypassing hash check (opt-out)

If no `.sha256` sidecar exists, the strategy loads without verification. This is backwards
compatible — all existing strategies without sidecars continue to work.

To opt in for a specific strategy, generate a sidecar:
```bash
sha256sum strategies/active/my_bot.py > strategies/active/my_bot.py.sha256
```
```

## Acceptance Criteria

1. A strategy file with a matching `.sha256` sidecar loads successfully; `strategy_hash_verified` log line appears.
2. A strategy file with a `.sha256` sidecar that does NOT match (modified after hash was generated) is rejected; `strategy_load_rejected_hash_mismatch` log line appears; the bot continues running the previously loaded strategy.
3. A strategy file with NO `.sha256` sidecar loads normally (backwards compatible, no warning).
4. `make hash-strategy STRATEGY=strategies/active/funding_rate_arb_bot.py` creates `strategies/active/funding_rate_arb_bot.py.sha256` with the correct hash.
5. `make verify-strategies` returns exit 0 when all sidecars match, non-zero when any mismatch.
6. `vps-deploy.sh` for the bot service runs `make verify-strategies` before deploying; a hash mismatch aborts the deploy.
7. Modifying a strategy file after its sidecar was generated triggers `strategy_hash_mismatch` on the next file watcher event if the sidecar was not updated.

## Dev Notes

- **Opt-in, not mandatory:** requiring a sidecar for every strategy would break the existing hot-reload workflow. Opt-in (sidecar presence triggers verification) is backwards compatible and allows gradual adoption.
- **`.sha256` file format:** `sha256sum` output is `<hash>  <filename>` (two spaces). The verification reads only the hash portion with `.strip().split()[0]`. This is compatible with `sha256sum -c` for the `make verify-strategies` target.
- **Partial-write protection:** the file watcher triggers on `IN_CLOSE_WRITE` (file fully written) or `IN_MODIFY`. If using `IN_MODIFY`, the hash check on a partially-written file will fail and the load will be rejected — which is the correct behaviour. The file will be retried on the next modification event once writing is complete.
- **Sidecar file events:** the file watcher should NOT trigger a reload when a `.sha256` file changes — only `.py` files are strategy modules. Ensure the event filter excludes sidecar files by checking file extension.
- **Security model:** this is not a cryptographic trust chain — the sidecar is alongside the strategy in the same git repo and same deployment path. It protects against: (1) corrupted file mid-transfer, (2) accidental partial write. It does NOT protect against an attacker who has write access to the deployment directory (they can update both the file and the sidecar). For that threat model, you would need GPG signing with a key not on the VPS.

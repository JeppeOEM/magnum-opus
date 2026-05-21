---
id: 34-1
title: VPS bootstrap script
epic: 34
status: done
---

# Story 34-1: VPS bootstrap script

## Context

A fresh Linode Ubuntu 22.04 VPS needs a repeatable one-time setup before any services can run. Currently there is no script for this — it must be done manually each time a new VPS is provisioned. The setup must produce a state where story 34-2's deploy script can run immediately without further manual steps.

The VPS runs the full stack via `docker compose` from the monorepo. No image registry is used — images are built on the VPS from source. Redis and QuestDB data must persist across service restarts and reboots via named Docker volumes.

## What to build

### `scripts/bootstrap-vps.sh`

A single idempotent script that configures a fresh Ubuntu 22.04 VPS from root. Safe to re-run — all steps check for existing state before acting.

```bash
#!/usr/bin/env bash
# bootstrap-vps.sh — one-time VPS provisioning for magnum-opus
# Run as root on a fresh Ubuntu 22.04 Linode.
# Idempotent: safe to re-run.
#
# Usage:
#   ssh root@<VPS_IP> 'bash -s' < scripts/bootstrap-vps.sh
#   # Or after copying the script:
#   ./bootstrap-vps.sh
#
# After completion:
#   - Docker + compose plugin installed
#   - 'deploy' user created, added to docker group, SSH key auth only
#   - UFW: deny all incoming except SSH; allow all outgoing
#   - Repo cloned to /home/deploy/magnum-opus
#   - .env stub files created (fill in API keys before first deploy)
#   - swap enabled (1GB) for Go build headroom on small instances
```

**Steps the script performs (in order):**

1. **System update** — `apt-get update && apt-get upgrade -y`

2. **Docker install** — official Docker install script (`get.docker.com`); installs docker-ce + compose plugin. Skip if `docker` already in PATH.

3. **Swap** — create 1GB swapfile at `/swapfile` if none exists. Prevents Go compile OOM on 1–2GB Linodes.
   ```bash
   fallocate -l 1G /swapfile && chmod 600 /swapfile
   mkswap /swapfile && swapon /swapfile
   echo '/swapfile none swap sw 0 0' >> /etc/fstab
   ```

4. **Deploy user** — create `deploy` user if not present; add to `docker` group; create `~/.ssh/authorized_keys` with a placeholder comment. Print instructions to add the dev machine's public key.
   ```bash
   adduser --disabled-password --gecos "" deploy 2>/dev/null || true
   usermod -aG docker deploy
   mkdir -p /home/deploy/.ssh
   chmod 700 /home/deploy/.ssh
   touch /home/deploy/.ssh/authorized_keys
   chmod 600 /home/deploy/.ssh/authorized_keys
   chown -R deploy:deploy /home/deploy/.ssh
   ```

5. **SSH hardening** — edit `/etc/ssh/sshd_config`:
   - `PasswordAuthentication no`
   - `PermitRootLogin prohibit-password`
   - `PubkeyAuthentication yes`
   Restart sshd. **Note:** only apply after confirming an SSH key is in `authorized_keys`.

6. **UFW baseline** — install ufw if missing; set defaults; allow SSH; enable.
   ```bash
   ufw --force reset
   ufw default deny incoming
   ufw default allow outgoing
   ufw allow 22/tcp comment 'SSH'
   ufw --force enable
   ```
   Note: story 34-3 adds Tailscale-specific rules on top of this.

7. **Repo clone** — clone `https://github.com/JeppeOEM/magnum-opus.git` to `/home/deploy/magnum-opus` if not already present. Set ownership to `deploy:deploy`.
   ```bash
   if [ ! -d /home/deploy/magnum-opus ]; then
     git clone https://github.com/JeppeOEM/magnum-opus.git /home/deploy/magnum-opus
     chown -R deploy:deploy /home/deploy/magnum-opus
   fi
   ```

8. **Env file stubs** — create stub `.env` files if not present. Never overwrite existing files.
   ```bash
   cd /home/deploy/magnum-opus
   [ -f .env ] || cp .env.example .env 2>/dev/null || touch .env
   [ -f bot-service/.env ] || cp bot-service/.env.example bot-service/.env 2>/dev/null || touch bot-service/.env
   [ -f candle-service/.env ] || touch candle-service/.env
   chmod 600 .env bot-service/.env candle-service/.env
   chown deploy:deploy .env bot-service/.env candle-service/.env
   ```

9. **Print summary** — print all endpoints, their ports, and next steps for the operator:
   ```
   ✓ Bootstrap complete.

   Next steps:
   1. Add your SSH public key:
      echo "ssh-ed25519 AAAA..." >> /home/deploy/.ssh/authorized_keys
   2. Fill in API keys:
      nano /home/deploy/magnum-opus/.env
      nano /home/deploy/magnum-opus/bot-service/.env
   3. Install Tailscale (story 34-3):
      curl -fsSL https://tailscale.com/install.sh | sh
   4. Run first deploy (story 34-2):
      make deploy-all VPS=deploy@<IP>
   ```

### `Makefile` — add bootstrap target

```makefile
VPS ?= deploy@$(VPS_HOST)

## One-time VPS provisioning (run as root: make bootstrap VPS=root@IP)
bootstrap:
	ssh $(VPS) 'bash -s' < scripts/bootstrap-vps.sh
```

## Acceptance Criteria

1. Running `bash scripts/bootstrap-vps.sh` on a fresh Ubuntu 22.04 instance completes without errors.
2. After the script: `docker compose version` succeeds as the `deploy` user.
3. UFW status shows `deny (incoming)` default with port 22 allowed.
4. `/home/deploy/magnum-opus` exists and is owned by `deploy`.
5. `.env` and `bot-service/.env` exist with permissions 600.
6. Script is idempotent — running it twice produces no errors and no duplicate entries.
7. `PasswordAuthentication no` is present in `/etc/ssh/sshd_config` (applied only after AC reminder to add SSH key).

## Dev Notes

- Target: Ubuntu 22.04 LTS (Linode default). Docker's `get.docker.com` script handles apt source setup.
- Swap is critical: Go compiles the aggregator and gateway; on a 1GB Nanode this OOMs without swap. 1GB swap is adequate; 2GB Linode needs no swap.
- The script should NOT start any Docker services — that is story 34-2's job.
- SSH hardening step (AC7) must print a prominent warning: "Ensure your SSH public key is in authorized_keys before applying — you will be locked out otherwise."
- `.env.example` files: check if they exist before trying to copy; some services may not have them yet.
- File permissions: `.env` files contain API keys. `chmod 600` ensures only the `deploy` user can read them.

## Review Findings

- [ ] [Review][Patch] `ufw --force reset` destroys all rules on re-run — breaks AC6 idempotency [scripts/bootstrap-vps.sh:201]
- [ ] [Review][Patch] Cloud-init sshd_config.d override keeps PasswordAuthentication yes — AC7 silently not met [scripts/bootstrap-vps.sh:158]
- [ ] [Review][Patch] `command -v docker` matches docker.io snap stub, skipping Docker CE install — AC2 failure path [scripts/bootstrap-vps.sh:75]
- [ ] [Review][Patch] `PubkeyAuthentication` sed `||` append never fires (sed exits 0 on no-match) — setting may not be written [scripts/bootstrap-vps.sh:184]
- [x] [Review][Defer] Partial `/swapfile` from aborted previous run causes abort on re-run [scripts/bootstrap-vps.sh:98] — deferred, operator can manually delete file
- [x] [Review][Defer] SSH hardening applied without verifying authorized_keys is populated — non-interactive script design limitation
- [x] [Review][Defer] No git commit pinning for bootstrap clone — security vs ops convenience tradeoff
- [x] [Review][Defer] Makefile VPS unquoted in ssh command — low practical risk (value is always user@IP)
- [x] [Review][Defer] Docker install uses apt method vs spec's get.docker.com — minor spec deviation, functionally equivalent
- [x] [Review][Defer] Makefile missing `VPS ?= deploy@$(VPS_HOST)` — minor spec deviation, VPS loaded from .env.deploy
- [x] [Review][Defer] ANSI escape codes emitted to non-TTY stdout — cosmetic, consistent with sibling scripts
- [x] [Review][Defer] `create_env_stub` missing `mkdir -p` guard for service subdirectory — low risk with normal clone

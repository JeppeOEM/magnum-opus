# Verification Layer

Documents that prove the system does what it claims.

| File | Purpose |
|------|---------|
| [NOMICON.md](NOMICON.md) | Dark corners — things that look ordinary but are catastrophically dangerous if misunderstood. 10 sections with proof sketches. |
| [VERIFICATION-MATRIX.md](VERIFICATION-MATRIX.md) | Every testable system claim × verification method × status (AUTOMATED / MANUAL / UNTESTED). 64 claims tracked. |

## Running the automated audit

```bash
bash scripts/audit-docs.sh
```

All 14 checks should pass on a clean repo. See the script for what each check verifies.

# Linux Subsystem Layer

Documentation about internal structure, ownership, and rules — inspired by Linux kernel documentation style.

| File | Purpose |
|------|---------|
| [MAINTAINERS.md](MAINTAINERS.md) | Every package with its owner goroutine/thread, maintained invariants, and cross-references to NOMICON. |
| [LOCKING.md](LOCKING.md) | Every mutex, atomic, and channel in the codebase — what it protects, who holds it, how long. |
| [DANGEROUS.md](DANGEROUS.md) | Functions with surprising contracts, hidden preconditions, or side effects that outlive the call. |
| [CODING-STANDARDS.md](CODING-STANDARDS.md) | Rules followed by all code, with rationale and counterexamples. |

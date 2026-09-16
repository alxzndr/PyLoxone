# Documentation index

User-facing documentation is in the top-level [`README.md`](../README.md);
release notes are in [`CHANGELOG.md`](../CHANGELOG.md); how to work on the
code is in [`CONTRIBUTING.md`](../CONTRIBUTING.md). This folder holds the
longer material.

| File | What it is | Status |
|---|---|---|
| [`fork-vs-upstream.md`](fork-vs-upstream.md) | What this fork changes compared to JoDehli/PyLoxone, the one-way config-entry migration, and how to install it | Maintained |
| [`review/2026-09-findings.md`](review/2026-09-findings.md) | The findings catalogue of the September 2026 code review. Finding ids (`API-xx`, `CORE-xx`, `PS-xx`, `PC-xx`, `TOOL-xx`) are cited in commits, tests and the changelog | Historical reference; line numbers refer to upstream commit `7561247` |
| [`review/2026-09-remediation-plan.md`](review/2026-09-remediation-plan.md) | The work packages (WP-0.1 to WP-6.10) that turned the findings into 0.10.0 | Complete except the "Ventilation fan model rework" follow-up |
| [`review/LIVE-MINISERVER-CHECKS.md`](review/LIVE-MINISERVER-CHECKS.md) | Protocol assumptions that need confirmation on real hardware, with the results so far | Living document |
| [`incidents/2026-09-02-401-during-miniserver-reboot.md`](incidents/2026-09-02-401-during-miniserver-reboot.md) | Incident report: a transient 401 during a Miniserver reboot killed the integration on upstream 0.9.23 | Fixed in 0.10.0 (CORE-09) |

The per-package agent prompts and PR bodies that drove the remediation were
removed once every package had landed; they are in the git history under
`docs/review/prompts/` before the cleanup commit.

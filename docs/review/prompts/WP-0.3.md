# Agent prompt — WP-0.3: Packaging, metadata, translations, docs baseline

You are implementing work package **WP-0.3** of the PyLoxone remediation plan.
Repository: `/Users/alexandergeeraerts/github/PyLoxone` (Home Assistant custom integration for Loxone Miniservers).
Work autonomously. Do not ask questions unless genuinely blocked; state assumptions in the PR body instead.

Source documents (read them; they are authoritative):
- `docs/review/2026-09-remediation-plan.md` — the full plan (this package is one section of it)
- `docs/review/2026-09-findings.md` — the findings catalogue (the entries you need are inlined below)

## Ordering

- Phase: Phase 0
- Prerequisite packages that must be merged first: WP-0.1
- Parallel-safe with WP-0.2.
- Before starting, run `git log --oneline -20` and check whether prerequisite packages have landed. If a prerequisite is missing, stop and report; do not re-implement it.

## Rules (from the plan)

1. **Branch per WP** from `master`: `fix/wp-<id>-<slug>`. Do not mix WPs in one branch.
2. **Read the finding entries** listed under the WP, then the code they point at. Line numbers are
   for commit `7561247`; re-locate if the file has moved on.
3. **Stay inside the WP's file list.** If a fix needs a change elsewhere, note it in the PR body as a
   follow-up rather than expanding scope. Two WPs marked *parallel-safe* never touch the same file.
4. **Tests are part of the WP.** Every behavioural fix ships with a regression test named in the
   package's acceptance criteria. Pure-function tests go in `tests/`; HA-harness tests use the
   fixtures from WP-0.2.
5. **Before finishing** run, from the repo root, and paste the results into the PR body:
   ```
   ruff check . --select F,E9,PLE,B,T20,S307,ASYNC,RUF006
   ruff format --check .
   pytest -q
   ```
   A WP is not done while any of these fail.
6. **Commit messages** reference the finding IDs (`fix(cover): send stop on Window stop_cover (PC-07)`)
   and the upstream issue where one exists (`Fixes JoDehli/PyLoxone#413`). Add a line to
   `CHANGELOG.md` (created in WP-0.3) under *Unreleased*.
7. **Items marked VERIFY** in the catalogue must not change behaviour on assumption. Implement the
   fix behind a clearly named helper, write the test for the *intended* semantics, and flag in the
   PR that a live-Miniserver check is required before merge.
8. **Do not bump `manifest.json` version** inside a WP; releases are cut separately (WP-0.3 sets up
   the mechanism).
9. **No new `# noqa` to make a check pass.** Silencing a lint rule to turn CI green is forbidden
   for every package. The single exception is the set of markers **WP-0.1** lays down — 6×`S307` →
   WP-1.2, 6×`RUF006` → WP-2.2, 2×`RUF006` → WP-3.1 — each written as
   `# noqa: <RULE>  # TODO(WP-x.y)` with the owning package id. No other `# noqa` may be added.
   For the owning package, clearing is part of its definition of done: removing the marker
   *and* the code that caused it, such that
   `grep -rn "TODO(WP-<your id>)" custom_components/` returns nothing when it finishes. If a rule
   genuinely cannot be satisfied, say so in the PR body instead of silencing it.

## Decisions already taken (do not re-litigate)

| Topic | Decision | Rationale |
|---|---|---|
| Minimum HA version | Raise `hacs.json` floor to **2026.7.0**; README says the same | `sensor.py` already needs `UnitOfRatio` (CORE-25); a shim buys little since requirements pin 2026.8.1 |
| Reconnect architecture | In-place reconnect inside `LoxoneConnection` with a connection-state callback; entities stay alive and flip `available` | Reload-everything is the root of #475/#491 (API-09, CORE-05) |
| State fan-out | Coordinator holds one bus listener and dispatches per uuid via `async_dispatcher_send`; the public `loxone_event` bus event keeps firing for user automations | CORE-27; keeps README's recorder advice valid |
| Outbound commands | Entities call their coordinator directly; the `loxone_send`/`loxone_send_secured` bus listeners stay but filter on "uuid belongs to this Miniserver" | Multi-instance correctness (#491) without breaking external users |
| Credentials location | Move host/port/user/password/verify_ssl to `ConfigEntry.data` in entry version 5; options keep only preferences | HA convention; needed for reauth and diagnostics redaction |
| Auto-groups | Keep, but fix (CORE-15, PS-14) and gate behind an option defaulting to **off** for new installs | Existing users rely on them; groups are a legacy idiom |
| YAML `loxone:` block | Delete `async_setup` import attempt and `CONFIG_SCHEMA`; register a repair issue if the key is present | Import step never existed (CORE-19); YAML *sensors* (`platform: loxone`) stay |
| `has_entity_name` | Adopt in Phase 5 only, with a release note, because entity IDs will change | User-visible rename; keep it out of bug-fix releases |
| Dead `pyloxone_api` files | Delete `api.py`, `helper.py`, `__main__.py` CLI stays but fixed | Nothing imports them (API-23) |

## Your work package (verbatim from the plan)

### WP-0.3 Packaging, metadata, translations, docs baseline
- **Findings**: TOOL-02, TOOL-03/CORE-25, TOOL-13 (logger name + version line), TOOL-14 (CHANGELOG, CONTRIBUTING, feature-request template), CORE-23, CORE-24, TOOL-15.
- **Files**: `custom_components/loxone/manifest.json`, `hacs.json`, `README.md`, `CHANGELOG.md` (new), `CONTRIBUTING.md` (new), `.github/ISSUE_TEMPLATE/*`, `.github/workflows/release.yaml` (new), `custom_components/loxone/translations/{en,de,cs}.json`, `custom_components/loxone/services.yaml`, `custom_components/loxone/__init__.py` (delete `REQUIREMENTS` list only).
- **Steps**: manifest → `iot_class: local_push`, drop `httpx`, `websockets>=14,<16`, `pycryptodome>=3.20`, `dependencies: ["group"]`, `integration_type: hub`, `loggers: ["custom_components.loxone"]`, `version: 0.9.23`; `hacs.json` → `homeassistant: "2026.7.0"`; README → version line, logger snippet `custom_components.loxone.pyloxone_api`; translations → add `config.error`/`options.error` keys (`invalid_username_encoding`, `invalid_password_encoding`) to all three, remove `single_instance_allowed`, remove `re\-`; `release.yaml` → on tag push, verify (or write) manifest version = tag and create the GitHub release with generated notes; `CHANGELOG.md` seeded with an *Unreleased* section.
- **Interaction with WP-0.2**: the two are parallel-safe (no shared files), but WP-0.2's contract tests assert the *current, broken* state with `xfail(strict=True)` markers. If WP-0.2 has already landed when you finish, several of those become XPASS and fail the suite. Flipping them is part of this package: remove the `xfail` from at least the manifest-requirements test (you drop `httpx`) and the manifest-semver/version test, and re-run `pytest -q`. If WP-0.2 has *not* landed, note in the PR body which xfails WP-0.2 must not add.
- **Acceptance**: hassfest and HACS jobs pass; the translations job stays green (the `services.sync_areas` keys were backfilled in WP-0.1; you add the `error` keys); `manifest-version` job passes on a test tag; `pytest -q` green with no XPASS.

## Findings you are fixing (verbatim from the catalogue)

### TOOL-02 [critical] Release 0.9.23 ships `manifest.json` version `0.9.22`
- Upstream tag `0.9.23` = commit `7561247`; `manifest.json:14` says `0.9.22`. Version bumps are manual commits and were skipped. Needs a release workflow or a tag-vs-manifest guard.

### TOOL-03 [critical] Minimum HA version — see CORE-25 (canonical entry).


### CORE-25 [critical] Declared minimum HA version is wrong everywhere and the code cannot import on the versions HACS advertises
- `hacs.json:6` says `2025.2.4`, `README.md:18` says `2024.1.0`, `requirements.txt` pins `2026.8.1`. `sensor.py:24` imports `UnitOfRatio` at module scope, which first exists in HA **2026.7.0** (verified by probing HA tags); `AlarmControlPanelState` (used in `alarm_control_panel.py`) does not exist in 2024.1. A user on 2025.2–2026.6 installs successfully via HACS and the integration dies with `ImportError`.
- Fix: set `hacs.json` to `2026.7.0` and the README to match (or add an import shim and CI-test the real floor).
- Effort: S

### TOOL-13 [medium] README gaps
- No documentation of `verify_ssl`, `generate_scenes`, `generate_scenes_delay` (min 3, unexplained), `generate_lightcontroller_subcontrols`; only 1 of 7 services documented; logger snippet names `custom_components.loxone.api`, which does not exist (the package is `pyloxone_api`); minimum-version claim wrong (CORE-25).

### TOOL-14 [low] No dependabot, release workflow, CHANGELOG, CONTRIBUTING; issue template has no feature-request form although README invites them; free-text version fields.


### CORE-23 [medium] Translation drift and stray escapes
- `de.json` lacks `services.sync_areas.*` (4 keys); `cs.json` has only the `entity` block (51 keys missing); no `error` sections anywhere; `services.yaml:71` and `en.json:74` contain a literal `re\-synchronized`; `config.abort.single_instance_allowed` is unused.
- Fix: backfill de, add error keys, remove the backslash and the dead key, add a CI key-parity check.
- Effort: S

### CORE-24 [medium] `manifest.json` / legacy declarations
- `iot_class` is `local_polling` (integration is websocket push); `httpx` required but never imported (HTTP layer is `aiohttp`); `pycryptodome` unpinned, `websockets>=14` unbounded; `dependencies: []` while `homeassistant.components.group` is imported at module scope; missing `integration_type`, `loggers`; `__init__.py:51` `REQUIREMENTS = [..., "numpy"]` is an HA 0.x relic that modern HA ignores.
- Fix: `local_push`; drop `httpx`; pin; `dependencies: ["group"]` (if groups stay); `loggers: ["custom_components.loxone"]`; delete `REQUIREMENTS`.
- Effort: S

### TOOL-15 [low] `manifest.json` lacks `loggers`/`integration_type` — see CORE-24.



## Definition of done

1. Every acceptance criterion above is met and backed by a test that fails before your change and passes after it.
2. From the repo root, all of these pass and their output is pasted into the PR/commit body:
   ```
   ruff check . --select F,E9,PLE,B,T20,S307,ASYNC,RUF006
   ruff format --check .
   pytest -q
   ```
3. The commit message(s) cite the finding IDs and upstream issue numbers listed above.
4. Items marked **VERIFY** are implemented behind a clearly named helper with tests for the intended semantics, and the PR body lists them as requiring a live-Miniserver check before merge.
5. A line is added under *Unreleased* in `CHANGELOG.md` (if the file does not exist yet because WP-0.3 has not landed, add the note to the PR body instead).
6. Anything you discovered outside this package's scope is listed under "Follow-ups" in the PR body, not fixed.
7. Finish with a short report: what changed (by file), test results, VERIFY items, follow-ups.

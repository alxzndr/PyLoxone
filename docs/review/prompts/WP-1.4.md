# Agent prompt — WP-1.4: Secrets and privacy

You are implementing work package **WP-1.4** of the PyLoxone remediation plan.
Repository: `/Users/alexandergeeraerts/github/PyLoxone` (Home Assistant custom integration for Loxone Miniservers).
Work autonomously. Do not ask questions unless genuinely blocked; state assumptions in the PR body instead.

Source documents (read them; they are authoritative):
- `docs/review/2026-09-remediation-plan.md` — the full plan (this package is one section of it)
- `docs/review/2026-09-findings.md` — the findings catalogue (the entries you need are inlined below)

## Ordering

- Phase: Phase 1
- Prerequisite packages that must be merged first: WP-0.2
- Parallel-safe with WP-1.1, WP-1.2, WP-1.3, WP-1.5.
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

### WP-1.4 Secrets and privacy
- **Findings**: CORE-08, CORE-21, API-05.
- **Files**: `diagnostics.py`, `system_health.py`, `pyloxone_api/connection.py` (callback filter at 744-749 and the ERROR log at 1421-1426), `pyloxone_api/websocket_protocol.py:47`, `__init__.py:368-371` (debug log only), `tests/test_diagnostics.py` (new).
- **Steps**: diagnostics for the *requested* entry with `async_redact_data` over `{serialNr, localUrl, remoteUrl, projectName, mac, token, password, username, key, salt}` and redacted entry options; system health aggregates all entries with `.get()`; protocol/auth text responses never reach the external callback; token dicts redacted in logs; `Sent:` debug line truncated to the command name.
- **Acceptance**: diagnostics test asserts no fixture serial/URL appears in the output and the right entry is returned when two are loaded; a connection-layer test feeds a fake `gettoken` response and asserts the callback is not invoked.

## Findings you are fixing (verbatim from the catalogue)

### CORE-08 [high] Diagnostics dump the whole `LoxAPP3.json` unredacted, for the wrong entry, and may return `None`
- Where: `diagnostics.py:13-21` (iterates `hass.data[DOMAIN]` and returns the first; ignores `config_entry`; no `async_redact_data`). Structure file contains `serialNr`, `localUrl`, `remoteUrl`, `projectName`, users, rooms.
- Fix: use `hass.data[DOMAIN][config_entry.entry_id]`; `async_redact_data` with `{serialNr, localUrl, remoteUrl, projectName, mac, token, password, username, key}`; also redact entry options.
- Effort: S · Category: security

### CORE-21 [medium] `system_health` reports only the first entry and has unguarded lookups
- Where: `system_health.py:21-42` (`return` inside the loop; `hass.data[DOMAIN]` `KeyError` if never set up; `hasattr(v, "miniserver")` always true because the coordinator initialises it to `None`; `["msInfo"][...]` unguarded; exposes `remoteUrl`).
- Fix: aggregate per entry; `.get()`; guard `miniserver is None`.
- Effort: S

### API-05 [high] Bearer token, user salt/key and visual-password salt are fired on the HA event bus and logged
- Where: `connection.py:744-749` (`MessageType.TEXT` is in `callback_types`, so `gettoken`/`getjwt`/`getkey2`/`getvisusalt` responses reach the callback), `__init__.py:368-371` (`hass.bus.async_fire(EVENT, message)` + `_LOGGER.debug(f"{message}")`), `connection.py:1421-1426` (full token dict logged at ERROR), `websocket_protocol.py:47` (every sent command incl. auth hashes at DEBUG).
- Fix: do not forward protocol/auth text responses to the external callback (filter on `control` ∈ {getkey, getkey2, gettoken, getjwt, refreshjwt, refreshtoken, getvisusalt, keyexchange, authwithtoken}); redact tokens in logs; drop/truncate the `Sent:` debug line.
- Effort: M · Category: security


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

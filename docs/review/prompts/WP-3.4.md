# Agent prompt — WP-3.4: Config flow rewrite with reauth and unique id

You are implementing work package **WP-3.4** of the PyLoxone remediation plan.
Repository: **your current working directory**. It is a Home Assistant custom integration for
Loxone Miniservers. Every path in this document is relative to that directory.
Do not `cd` outside it. Do not search the filesystem for another copy of this project: other
checkouts exist, they belong to other people, and writing to one destroys their work.
Use `.venv/bin/python` for Python; it is present in your working directory.
Work autonomously. Do not ask questions unless genuinely blocked; state assumptions in the PR body instead.

Source documents (read them; they are authoritative):
- `docs/review/2026-09-remediation-plan.md` — the full plan (this package is one section of it)
- `docs/review/2026-09-findings.md` — the findings catalogue (the entries you need are inlined below)

## Ordering

- Phase: Phase 3
- Prerequisite packages that must be merged first: WP-3.3
- Sequential after WP-3.3.
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

### WP-3.4 Config flow rewrite with reauth and unique id
- **Findings**: CORE-19, CORE-18, CORE-09 (reauth escalation only: replace WP-1.5's "keep raising NotReady after 5 attempts / 5 min" branch with `ConfigEntryAuthFailed`; a 401 inside the transient window must still retry), API-13 consumer side, CORE-30 (YAML repair issue).
- **Files**: `config_flow.py`, `__init__.py` (`async_migrate_entry` v5, `ConfigEntryAuthFailed`, delete `async_setup` import + `CONFIG_SCHEMA`), `coordinator.py` (read from `entry.data`), `translations/*`, `tests/test_config_flow.py` (new), `tests/test_config_entry_migration.py` (extend).
- **Design**: hand-written `ConfigFlow` v5: `user` step (host/port/user/password/verify_ssl) → test connection with `LoxoneConnection.open` → read `msInfo.serialNr` → `async_set_unique_id(serial)` + `_abort_if_unique_id_configured()` → create entry with data; errors `cannot_connect`, `invalid_auth`, `invalid_username_encoding`, `invalid_password_encoding`; `reauth`/`reauth_confirm` steps; optional `zeroconf` step using `discover.py` if the Miniserver advertises via mDNS (**VERIFY**; skip if not). `OptionsFlow` with only `generate_scenes`, `generate_scenes_delay`, `generate_lightcontroller_subcontrols`, `generate_groups`. Migration v4→v5 moves connection keys from options to data and sets `unique_id` from the stored serial if available (else leave `None` and set it on next successful setup).
- **Acceptance**: flow tests for success, cannot-connect, invalid-auth, duplicate serial abort, reauth success; migration test v1→v5; `ConfigEntryAuthFailed` from setup starts a reauth flow; an entry created under v4 still loads after migration.

## Findings you are fixing (verbatim from the catalogue)

### CORE-19 [medium] Config flow gaps: credentials in `options`, no unique id, no connection test, no reauth, no discovery, broken YAML import
- Where: `config_flow.py:108-133` (`SchemaConfigFlowHandler` stores everything in `options`; plaintext password round-trips to the frontend as a suggested value); no `async_set_unique_id` (same Miniserver can be added twice; `single_instance_allowed` translation key is dead); `validate_loxone_setup` never connects; `pyloxone_api/discover.py` exists but no `zeroconf`/`dhcp` step; `__init__.py:142-150` fires an `import` flow but no `async_step_import` exists → `UnknownStep` traceback at every startup for YAML users.
- Fix: hand-written `ConfigFlow` (user step with connection test → `unique_id = serial`, reauth, optional zeroconf) + `OptionsFlow` for preferences only; migrate creds to `data` (entry version 5); implement or delete the YAML import.
- Effort: L

### CORE-18 [medium] `SchemaFlowError` raised with free text where a translation key is expected
- Where: `config_flow.py:36-39, 45-47`. No `config.error`/`options.error` section exists in any translation file.
- Fix: `SchemaFlowError("invalid_username_encoding")` etc. plus matching `error` keys in en/de/cs.
- Effort: S

### CORE-09 [critical] A transient 401 during setup permanently kills the integration (`return False`, no retry, no reauth); connection leaked on two error branches
- Where: `__init__.py:257-269`. `LoxoneUnauthorisedError` is the *only* setup failure that neither raises `ConfigEntryNotReady` nor closes the API; it does `return False`, which parks the entry in `setup_error` until a human reloads it. `LoxoneServiceUnAvailableError` also skips `await coordinator.api.close()`. No `async_step_reauth` exists (CORE-19).
- Incident: documented in `2026-09-02-pyloxone-401-setup-error.md` (repo root). A Miniserver firmware update (17.0.3.31) rebooted the device; its HTTP server came back before auth was initialised and answered `401` to `GET /data/LoxAPP3.json` at 21:56:11. The reconnect path (CORE-05) re-ran `async_setup_entry`, hit this branch, and the integration stayed dead for 10.5 hours with valid credentials (verified with `curl` and a second client during the outage). The 503/timeout branches retried correctly minutes earlier — only 401 is treated as fatal, and a booting Miniserver emits exactly that.
- Fix: never `return False`. Treat 401 during setup as retryable: raise `ConfigEntryNotReady` and count consecutive auth failures per entry (e.g. in `hass.data`/runtime data with a timestamp); only after N attempts spanning at least several minutes (suggest 5 attempts / 5 min) escalate to `ConfigEntryAuthFailed` (once WP-3.4 provides reauth) and a repair issue. Close the API in every failure branch. Same rule must apply inside the in-place reconnect loop (API-09): a 401 immediately after a reconnect is a boot artefact, not a credential change.
- Effort: S (retry + close) / L (reauth, see CORE-19)

### API-13 [medium] LL response codes other than `authwithtoken` 401 are ignored → bad credentials hang silently
- Where: `connection.py:1341-1364` (`gettoken`/`getjwt` handler never checks `mess_obj.code`; empty token → `ValueError` swallowed at the `except Exception` three lines later).
- Fix: check `code` in every auth handler; on 401/4003/1001 signal a typed `LoxoneUnauthorisedError`/`LoxoneTokenError` through `_reconnect_event`/a stored exception (the handler runs in a detached task, see API-14).
- Effort: M

### CORE-30 [low] No repairs / issue-registry usage
- Candidates: invalid credentials, unsupported firmware, YAML config present, repeated reconnect failure. Upstream PR #515 adds message-center repairs.
- Effort: M


## Definition of done

1. Every acceptance criterion above is met and backed by a test that fails before your change and passes after it.
2. From your working directory, all of these pass and their output is pasted into the PR/commit body:
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

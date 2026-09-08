# Agent prompt — WP-2.2: Clean-close detection, auth failure handling, logging hygiene (#514)

You are implementing work package **WP-2.2** of the PyLoxone remediation plan.
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

- Phase: Phase 2
- Prerequisite packages that must be merged first: WP-2.1
- Sequential after WP-2.1.
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

### WP-2.2 Clean-close detection, auth failure handling, logging hygiene (#514)
- **Findings**: API-02, API-27, API-14, API-16, API-13, API-06, API-08, API-17.
- **Files**: `pyloxone_api/connection.py`, `pyloxone_api/exceptions.py` (add `LoxoneReconnectRequested`), `pyloxone_api/const.py`, `coordinator.py` (token-changed callback → `async_update_entry` with `**data`), `tests/test_connection_lifecycle.py` (new).
- **Steps**: detect the normal end of the `async for` and raise `LoxoneConnectionClosedOk`; `asyncio.wait(FIRST_COMPLETED)`; typed handlers: expected closes → INFO once, control-flow reconnect → DEBUG, real errors → WARNING with the first traceback at DEBUG; `_process_message` awaits each send; tracked task set with done-callback; `check_refresh_token` waits for an authenticated event, awaits the refresh, escalates after N failures; check `code` on every auth response and raise `LoxoneUnauthorisedError` for 401/4003; `ping_interval=None`; retry loop → 3 tries with backoff, all three GETs; token persisted via callback whenever it changes (incl. `unsecure_password`); `killtoken` on entry removal (`async_remove_entry` hook).
- **Acceptance**: with a stub connection whose `__aiter__` ends cleanly, `start_listening` raises `LoxoneConnectionClosedOk` within 1s (not 30s) and the log contains no ERROR records (`caplog`); wrong-password stub → `LoxoneUnauthorisedError` within the open timeout; token change invokes the persistence callback; `grep -rn "TODO(WP-2.2)" custom_components/` returns nothing (the tracked-task set replaces WP-0.1's `RUF006` markers in `pyloxone_api/`).

## Findings you are fixing (verbatim from the catalogue)

### API-02 [critical] A clean close (code 1000) is invisible to the listen loop and surfaces 30s later as an ERROR
- Where: `connection.py:759-760` (`async for message in connection` — `websockets` swallows `ConnectionClosedOK` and the iterator simply ends), `connection.py:629-631` (`asyncio.wait(..., FIRST_EXCEPTION)` does not wake for a normal return), `connection.py:655-659` (`LoxoneConnectionClosedOk` is a custom class not matched by any specific handler → generic `except Exception` logs ERROR with traceback).
- Problem: token expiry, firmware restart and session-limit closes all take this path. The integration sits on a dead socket until `keep_alive` fails.
- Fix: after the `async for` ends, check `connection.close_code` and raise `LoxoneConnectionClosedOk`; use `return_when=FIRST_COMPLETED`; add `except LoxoneConnectionClosedOk` logging at INFO before the generic handler.
- Effort: M · Upstream: #514

### API-27 [medium] Expected reconnect control flow is logged at ERROR
- Where: `connection.py:589-616` (`raise LoxoneTokenError` bare, as control flow), `637-660` (`ERROR: Token error`, `ERROR: Connection closed with error`, `ERROR: Miniserver out of service` for events the code then recovers from).
- Fix: dedicated `LoxoneReconnectRequested` exception at DEBUG; WARNING on first outage, DEBUG on repeats; one "lost/restored" pair per outage.
- Effort: M · Upstream: #514

### API-14 [medium] Fire-and-forget tasks swallow errors; `task_done()` fires before the send completes
- Where: `connection.py:784` (`_websocket_event` detached), `687-695` (each outbound command detached, `task_done()` immediately), `534-536`, `557` (`_refresh_token` detached).
- Fix: `await self._send_text_command(...)` inside `_process_message`; keep a `self._tasks: set` with a done-callback that logs/propagates for the rest.
- Effort: M · Upstream: #514

### API-16 [medium] `check_refresh_token` spins at 1 Hz while no token exists; refresh failures are invisible
- Where: `connection.py:498-576` (`seconds_to_expire()` negative → clamp to 1s); `if self._key == old_key: continue` skips refresh entirely.
- Fix: wait on an "authenticated" event; `await` the refresh; escalate repeated failures to `LoxoneTokenError`.
- Effort: M · Upstream: #514

### API-13 [medium] LL response codes other than `authwithtoken` 401 are ignored → bad credentials hang silently
- Where: `connection.py:1341-1364` (`gettoken`/`getjwt` handler never checks `mess_obj.code`; empty token → `ValueError` swallowed at the `except Exception` three lines later).
- Fix: check `code` in every auth handler; on 401/4003/1001 signal a typed `LoxoneUnauthorisedError`/`LoxoneTokenError` through `_reconnect_event`/a stored exception (the handler runs in a detached task, see API-14).
- Effort: M

### API-06 [high] `websockets` protocol-level ping defaults (20s ping / 20s timeout) are not overridden
- Where: `connection.py:1028-1040` — `websocket_options` lacks `ping_interval`/`ping_timeout`.
- Problem: redundant with the Loxone `keepalive` (30s); a late pong kills a healthy connection with a 1011 and no diagnostics. **VERIFY** with `websockets` DEBUG logging against a real Miniserver.
- Fix: `ping_interval=None` (rely on Loxone keepalive) and explicit `close_timeout`.
- Effort: S · Upstream: #486 #457

### API-08 [high] `open()` retry loop (100 × 5s) blocks config-entry setup instead of letting `ConfigEntryNotReady` retry
- Where: `connection.py:819-836`, `const.py:12-13` (`RECONNECT_DELAY=5`, `RECONNECT_TRIES=100`). Only the first GET is retried; `LOXAPPPATH` (908) and `getPublicKey` (935) have none.
- Fix: ≤3 tries with exponential backoff, or no loop at all; apply uniformly.
- Effort: S · Upstream: #486

### API-17 [medium] Token persisted only at HA shutdown, never killed on the Miniserver; `unsecure_password` dropped
- Where: `__init__.py:578-589` (`stop_event` is the sole caller of `get_token_dict()`), no `killtoken` anywhere; `__init__.py:332-339` replaces the whole `data` dict (see CORE-10).
- Fix: persist on every token change via a coordinator callback, include `unsecure_password`, send `jdev/sys/killtoken/...` on entry removal.
- Effort: M · Upstream: #486


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

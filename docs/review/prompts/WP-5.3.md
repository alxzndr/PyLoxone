# Agent prompt — WP-5.3: Lint ratchet and dead-code sweep

You are implementing work package **WP-5.3** of the PyLoxone remediation plan.
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

- Phase: Phase 5
- Prerequisite packages that must be merged first: WP-4.5
- After Phase 4; one rule group per PR.
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

### WP-5.3 Lint ratchet and dead-code sweep
- **Findings**: TOOL-07 (ratchet), API-23, PC-40, PC-41, PS-25, CORE-32 remainder, API-26.
- **Steps**: enable rule groups one PR at a time (`F401`, `G004`, `ERA001`, `B006`, `BLE001`, `TRY400`, `ARG`), each PR mechanical; translate German comments; fix module docstrings; uncomment `ruff check` in `scripts/lint`; add `.pre-commit-config.yaml` mirroring CI.
- **Acceptance**: advisory ruff count trends down and the blocking set grows; no behaviour change (test suite unchanged and green).

## Findings you are fixing (verbatim from the catalogue)

### TOOL-07 [high] `ruff check` is commented out of `scripts/lint`; 2161 violations; 33 of 48 files unformatted
- `ruff.toml` selects `ALL` with a 7-entry ignore list; `scripts/lint:8-9` disables the check. Genuine defects buried in the noise: `F821` ×2 (PS-01), `F811` (helper.py HMAC), `PLE1205` (API-03), `E722` (message.py:233), `S307` ×6 (PC-01), `T201` ×2.
- Fix: two-tier — blocking defect ruleset in CI (`F, E9, PLE, B, T20, S307, ASYNC, RUF006`), advisory full run; `per-file-ignores` for tests; one `ruff format` commit with `.git-blame-ignore-revs`.

### API-23 [low] Dead code and stale declarations across the package
- `api.py` (8-line stub), `helper.py` (unused `hash_token`/`generate_hmac`; stdlib `HMAC` shadowed by `Crypto.Hash.HMAC`), `websocket_protocol.py:51-103` (`recv_message`), `message.py:30-55` (timers), `loxone_token.py` (`Salt`, unused imports), `const.py` (`THROTTLE_CHECK_TOKEN_STILL_VALID`, `TOKEN_REFRESH_*`, `CMD_ENCRYPT_CMD`, `DEFAULT_TOKEN_PERSIST_NAME`, `LOX_CONFIG`), `exceptions.py:27-97` (eight unused classes, some copy-pasted from a Samsung TV library), `connection.py:52-59` (`httpx` warnings filter — `httpx` is not imported anywhere), `__main__.py:5` (docstring argument order wrong).
- Fix: delete; drop `httpx` from `manifest.json` (see CORE-24).
- Effort: S

### PC-40 [low] Dead code in the complex platforms
- `helpers.py:42-47` (`lox2lox_mapped`), `68-83` (mired converters — confirmed unused; the lights are fully Kelvin-based), `cover.py:73-75, 118-121, 369-372`, `alarm_control_panel.py:158-175, 241-246` (`hidden`, `icon`, sync stubs, `_validate_code`), `alarm_control_panel.py:30-37`/`climate.py:75-79` (`PLATFORM_SCHEMA` for no-op YAML), `colorpickers.py:128`, `dimmer.py:129-142` (duplicate device-info block), `fan.py:109-122, 256-258, 273-277`, `lightcontroller.py:23,28,75-77,167-169`, `media_player.py:146-149` (`async_media_stop` without the `STOP` feature), unused imports (`DeviceInfo`/`DOMAIN` in `lights/*`, `DOMAIN` in alarm).
- Effort: M

### PC-41 [low] Docstrings, comments, formatting
- `fan.py:1`/`alarm_control_panel.py:1` say "Interfaces with Alarm.com alarm control panels"; f-string logging (`light.py:108,130,150`, `climate.py:751`, `media_player.py:83,90`); `climate.py:408-409` duplicate assignment; `cover.py:527` `shade_postion_as_text`; `climate.py:567` nested same-quote f-string (3.12+) with a suspicious `//`; `-> None` functions returning `True` (`climate.py:95`, `cover.py:43`, `light.py:48`, `alarm_control_panel.py:47,66`, `media_player.py:47`).
- Effort: S

### PS-25 [low] Dead code and small defects
- `binary_sensor.py:8-23` unused imports (`cv`, `vol`, `CONF_*`, `DOMAIN`, `SENDDOMAIN`); `switch.py:64-65` `_` used as a real variable; `sensor.py:588` magic `14`; `const.py:31 ERROR_VALUE` unused; `sensor.py:454` `if precision:` treats `0` as "none".
- Effort: S

### CORE-32 [low] Code hygiene in the core files
- f-string logging on the hot path (`__init__.py:370` runs for every message), German comments (`126, 340, 346, 352, 361`), unused imports (`EVENT_COMPONENT_LOADED`, `Platform`, `ATTR_COMMAND`, `DOMAIN_DEVICES`, `ERROR_VALUE`, `MiniServer`, `LoxoneConnection`, `LoxoneException`, `get_miniserver_type`), `_UNDEF` (77), mutable default `data={}` (398), `LoxoneEntity._clean_unit` duplicates `helpers.clean_unit` with a different `%%` fix, commented-out numpy helpers (`helpers.py:58-65`), `map_range` divides by zero when `in_min == in_max`, `get_all` crashes on missing `controls`/`type` (both reported by the Uni Ulm fuzzing PR #292).
- Effort: S

### API-26 [low] Hot-path inefficiencies
- `message.py:375-380` (`BaseMessage.__subclasses__()` per message), `connection.py:773,784,788` (up to two `create_task` per message, unbounded during a state burst), `websocket_protocol.py:37-49` (f-string debug logs evaluated regardless of level).
- Fix: type→class dict at import; lazy `%s` logging; run the callback inline.
- Effort: S · Upstream: #475 (burst handling)


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

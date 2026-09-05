# Agent prompt — WP-0.1: CI, lint and test harness baseline

You are implementing work package **WP-0.1** of the PyLoxone remediation plan.
Repository: `/Users/alexandergeeraerts/github/PyLoxone` (Home Assistant custom integration for Loxone Miniservers).
Work autonomously. Do not ask questions unless genuinely blocked; state assumptions in the PR body instead.

Source documents (read them; they are authoritative):
- `docs/review/2026-09-remediation-plan.md` — the full plan (this package is one section of it)
- `docs/review/2026-09-findings.md` — the findings catalogue (the entries you need are inlined below)

## Ordering

- Phase: Phase 0
- Prerequisite packages that must be merged first: none
- First package; nothing depends on it being parallel-safe.
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

### WP-0.1 CI, lint and test harness baseline
- **Findings**: TOOL-01, TOOL-04, TOOL-05, TOOL-06, TOOL-07, TOOL-09, TOOL-10, TOOL-11, TOOL-12, PS-01, API-03, CORE-12 (print only), PC-34.
- **Files**: `.github/workflows/ci.yaml` (new), `.github/workflows/hassfest.yaml`, `validate.yaml`, `stale.yaml`, `.github/dependabot.yml` (new), `.gitignore`, `ruff.toml`, `pytest.ini`, `requirements.txt`, `requirements-dev.txt` (new), `.pre-commit-config.yaml` (new), `.devcontainer.json`, `.vscode/settings.json`, `scripts/lint`, `tests/test_python_compatibilty.py` (delete), `custom_components/loxone/pyloxone_api/tests/test_run_alone.py` (delete), `switch.py` (import only), `fan.py` (imports only), `connection.py:282` (one line), `coordinator.py:86` (one line).
- **Steps**:
  1. Fix `.gitignore` first (TOOL-05) or the new YAML files will not stage.
  2. `requirements-dev.txt`: `pytest-homeassistant-custom-component==0.13.355`, `ruff==0.15.20`, `pre-commit`, `debugpy`, `colorlog`. `requirements.txt` mirrors `manifest.json` runtime deps only.
  3. `pytest.ini`: `testpaths = tests custom_components/loxone/pyloxone_api/tests`, `addopts = -m "not online" --cov=custom_components/loxone --cov-report=term-missing --cov-fail-under=15`, `asyncio_mode = auto`, `markers = online: requires a reachable Miniserver`.
  4. `ruff.toml`: keep `select = ["ALL"]`; add `[lint.per-file-ignores] "tests/**" = ["S101","ANN","D","PLR2004"]`. CI blocks on the defect subset only (see step 6).
  5. Fix the four one-line defects (PS-01 `from typing import Any`; PC-34 voluptuous annotations; API-03 logging placeholder; delete the `print`).
  5b. **Backfill the 4 missing `services.sync_areas.*` keys in `de.json`** (CORE-23, partial). The `translations` job added in step 6 is blocking, and de.json fails it today, so the gate would ship red. Add only those four keys and preserve the file's existing formatting (2-space indent, non-ASCII characters unescaped); the rest of CORE-23 (error keys, `single_instance_allowed`, the `re\-` escape, cs.json) stays in WP-0.3.
  6. `ci.yaml` jobs: `lint` (blocking `ruff check` over the **blocking set** defined below; full `ruff check --statistics` advisory with `continue-on-error`; `ruff format --check`), `test` (Python 3.14 only — HA 2026.8.1 refuses < 3.14.2), `hassfest`, `hacs` (`hacs/action@22.5.0`), `translations` (de.json key parity vs en.json), `manifest-version` (on tag push: manifest version == tag). Pin `actions/checkout@v7`, `actions/setup-python@v7`, `actions/stale@v11`. Re-enable the stale cron or delete the workflow.
  7. **Blocking set and the ratchet.** The blocking set is `F,E9,PLE,B,T20,S307,ASYNC,RUF006`. Getting it to zero is a WP-0.1 deliverable, but WP-0.1 must not implement behavioural fixes owned by later packages. Resolve the two rules that collide with other packages by *marking*, not fixing:
     - `S307` (6 `eval()` sites, PC-01, owned by **WP-1.2**) and `RUF006` (8 unstored `asyncio.create_task` calls, CORE-03/API-14, owned by **WP-2.2** and **WP-3.1**): add `# noqa: <RULE>  # TODO(WP-x.y)` at each site naming the owning package. Do not restructure the code.
     - Everything else in the blocking set is mechanical and behaviour-preserving: fix it repo-wide. Take `F401` from `ruff check --select F401 --fix` output only; never hand-delete an import you have not proved unused, and re-run `pytest -q` afterwards (HA re-exports such as `PLATFORM_SCHEMA` are assigned, so they are not flagged).
     Touching ~18 files for mechanical lint is expected and acceptable: WP-0.1 lands before any Phase 1 package starts, so the conflicts are with unwritten work.
  8. Do **not** widen the blocking set beyond those eight rules. `ruff.toml` keeps `select = ["ALL"]` for the advisory job; WP-5.3 ratchets additional rule groups in later.
  7. One separate commit `style: ruff format` and add its SHA to `.git-blame-ignore-revs`.
  8. Devcontainer image `python:3.14`; fix both `extraPaths`.
- **Acceptance**: CI green on the branch; `pytest -q` collects both test trees with `test_discover` deselected; `ruff check . --select F,E9,PLE,B,T20,S307,ASYNC,RUF006` reports 0 (with the 14 marked sites carrying a `TODO(WP-x.y)` noqa); `ruff format --check` clean; `grep -rn "TODO(WP-" custom_components/` lists exactly the 6 S307 and 8 RUF006 sites and nothing else.

## Findings you are fixing (verbatim from the catalogue)

### TOOL-01 [critical] No CI job runs pytest or ruff
- `.github/workflows/` has only hassfest, HACS validation and a disabled stale bot. 69 tests pass locally (`pytest -q tests` → `69 passed`) but nothing runs them; the two `pyloxone_api` tests have been broken for an unknown time.

### TOOL-04 [high] `pyloxone_api/tests/` is unrunnable and excluded
- `pytest.ini` `testpaths = tests`; `test_run_alone.py:17` calls a fixture directly (collection error), imports `dotenv` (not installed), body is `pass`; `test_discover.py` needs a live Miniserver and `pytest.mark.online` is unregistered so it fails instead of skipping.
- Fix: delete `test_run_alone.py`; register the marker with `-m "not online"` default; add `python-dotenv`/`pytest-asyncio` to dev requirements if kept.

### TOOL-05 [high] `.gitignore` swallows every `*.yaml`/`*.yml` outside two directories
- `git check-ignore` confirms `.pre-commit-config.yaml` and `.github/dependabot.yml` would be ignored; `services.yaml` and `.vscode/*` are tracked only because they predate the rule. `pre-commit` is in `requirements.txt` with no config file.
- Fix: targeted ignores or negations.

### TOOL-06 [high] Dev container cannot install the project's own requirements
- `.devcontainer.json:3` uses `python:3.13`; `homeassistant==2026.8.1` requires Python ≥ 3.14.2 (verified resolution failure). `.devcontainer.json:33` and `.vscode/settings.json:5` hard-code `python3.13/site-packages`.

### TOOL-07 [high] `ruff check` is commented out of `scripts/lint`; 2161 violations; 33 of 48 files unformatted
- `ruff.toml` selects `ALL` with a 7-entry ignore list; `scripts/lint:8-9` disables the check. Genuine defects buried in the noise: `F821` ×2 (PS-01), `F811` (helper.py HMAC), `PLE1205` (API-03), `E722` (message.py:233), `S307` ×6 (PC-01), `T201` ×2.
- Fix: two-tier — blocking defect ruleset in CI (`F, E9, PLE, B, T20, S307, ASYNC, RUF006`), advisory full run; `per-file-ignores` for tests; one `ruff format` commit with `.git-blame-ignore-revs`.

### TOOL-09 [medium] `test_python_compatibilty.py` is a tautology
- Asserts `ruff.toml` contains `py314` and scans for Python-2 `except` syntax. Filename misspelled.

### TOOL-10 [medium] `requirements.txt` mixes runtime/dev/test deps; test deps missing
- `pytest` unpinned; `pytest-asyncio`, `python-dotenv` missing; `httpx` unused; `pip`/`debugpy` are devcontainer concerns. Use `pytest-homeassistant-custom-component==0.13.355` (pins HA 2026.8.1) in a `requirements-dev.txt`.

### TOOL-11 [medium] `pytest.ini` registers no markers, no `asyncio_mode`, no coverage floor.


### TOOL-12 [medium] Outdated/unpinned actions; stale bot disabled
- `actions/checkout@v3`, `actions/stale@v5`, `hacs/action@main` (floating); `stale.yaml` has only `workflow_dispatch` with write permissions.

### PS-01 [critical] `Any` used in annotations but never imported in `switch.py`
- Where: `switch.py:375, 379` (`**kwargs: Any`); no `typing` import and no `from __future__ import annotations`. On Python ≤ 3.13 the module fails to import (whole switch platform gone); on 3.14 it is latent until something resolves annotations.
- Fix: `from typing import Any` (and `from __future__ import annotations`).
- Effort: S

### API-03 [high] `_LOGGER.error("Error while sending...", e)` has no placeholder → `--- Logging error ---` traceback on every send failure
- Where: `connection.py:281-283`.
- Fix: `_LOGGER.error("Error while sending command: %s", e)`.
- Effort: S · Upstream: #514

### CORE-12 [high] `DataUpdateCoordinator` misused
- Where: `coordinator.py:20-45` (`config_entry` not passed to `super().__init__` → HA deprecation report; `async_config_entry_first_refresh` overridden without calling super, so `data`/`last_update_success` never set), `86` (`print("_async_update_data")`), `99-100` (`async_cleanup` calls `self.api.close()` on `None` if unload precedes first refresh), `29-32` (`options[...]` with `[]` → `KeyError` reported as a connection error).
- Fix: pass `config_entry=`; drop the print; guard `api is None`; either use `_async_setup()` or stop subclassing `DataUpdateCoordinator`.
- Effort: M

### PC-34 [medium] `voluptuous.Any`/`Optional` used as type annotations
- Where: `fan.py:13, 225, 264`. Survives only via `from __future__ import annotations`; breaks `get_type_hints`.
- Fix: `int | None`, `typing.Any`.
- Effort: S

### CORE-23 [medium] Translation drift and stray escapes
- `de.json` lacks `services.sync_areas.*` (4 keys); `cs.json` has only the `entity` block (51 keys missing); no `error` sections anywhere; `services.yaml:71` and `en.json:74` contain a literal `re\-synchronized`; `config.abort.single_instance_allowed` is unused.
- Fix: backfill de, add error keys, remove the backslash and the dead key, add a CI key-parity check.
- Effort: S

### PC-01 [critical] `eval()` on strings received from the Miniserver websocket
- Where: `lights/lightcontroller.py:198, 212, 216` (`activeMoods`, `moodList` after `replace("true","True")`, `additionalMoods`); `lights/colorpickers.py:103, 247, 254` (`hsv(...)`/`temp(...)` strings). Arbitrary code execution from a spoofed/compromised Miniserver (plain HTTP on 8080 is the default; TLS verification is user-disableable).
- Fix: `json.loads` for the mood payloads; regex parser `(hsv|temp)\(([\d.]+),([\d.]+)(?:,([\d.]+))?\)` for colours; try/except and skip.
- Effort: S · Category: security

### CORE-03 [critical] The websocket listening task is never stored, never registered with HA, and can be garbage-collected
- Where: `__init__.py:568-576` (`listening_task` is a local), `102-112` (unload reads `coordinator._listening_task`, which nothing ever sets — dead code). Same class of bug at `602`, `616` (`_ = asyncio.create_task(...)`).
- Fix: `coordinator.listening_task = config_entry.async_create_background_task(hass, ..., name="loxone-listener")`; cancel on unload.
- Effort: M

### API-14 [medium] Fire-and-forget tasks swallow errors; `task_done()` fires before the send completes
- Where: `connection.py:784` (`_websocket_event` detached), `687-695` (each outbound command detached, `task_done()` immediately), `534-536`, `557` (`_refresh_token` detached).
- Fix: `await self._send_text_command(...)` inside `_process_message`; keep a `self._tasks: set` with a done-callback that logs/propagates for the rest.
- Effort: M · Upstream: #514


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

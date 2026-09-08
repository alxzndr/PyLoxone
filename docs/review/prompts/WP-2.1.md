# Agent prompt — WP-2.1: Connection correctness quick wins

You are implementing work package **WP-2.1** of the PyLoxone remediation plan.
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
- Prerequisite packages that must be merged first: WP-0.2
- Sequential: one agent owns pyloxone_api/ through WP-2.3.
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

### WP-2.1 Connection correctness quick wins
- **Findings**: API-01, API-04, API-07, API-10, API-11, API-12, API-15, API-18, API-19, API-20, API-21, API-22, API-23, API-24, API-25 (comment), API-26.
- **Files**: `pyloxone_api/connection.py`, `message.py`, `websocket_protocol.py`, `loxone_token.py`, `const.py`, `exceptions.py`, `helper.py` (delete), `api.py` (delete), `__main__.py`, `coordinator.py` (only the `open()` call site), `tests/test_connection_unit.py` (new).
- **Steps** (in this order): store `self.connection`/`self._session` in `open()` and make `start_listening` reuse them; `send(command)` not `send([command])`; delete line 1319; guard-and-raise in `_send_text_command`; scheme/host/port from the redirected URL; `urllib.parse.quote` the username (keep a **VERIFY** note on UTF-8 vs latin-1); `_secured_queue` → `deque` of parameter dataclasses, cleared in `close()`; `MessageHeader` always sets `payload_length`; strict header→body reads (port the logic from the dead `recv_message`, then delete it); `json.loads` off-loop via an injectable `loads` callable (coordinator passes `hass.async_add_executor_job`); `"<d"`; simplify decoding; delete dead files/constants/exceptions; rename `send_secured__websocket_command` → `send_secured_websocket_command` with a deprecated alias; initialise `_session_key`.
- **Acceptance**: unit tests per the catalogue's API test-gap list — URL-building table incl. the Cloud-DNS `http` redirect case, encryption round-trip asserting `send` receives a `str`, `_hash_token` returns `None` on a non-hex key and the `getvisusalt` handler leaves `_key` untouched, `close()` idempotent; HA-harness test asserts exactly one `wslib.connect` call per setup.

## Findings you are fixing (verbatim from the catalogue)

### API-01 [critical] `open()` never stores the connection; every setup opens two websockets and leaks one
- Where: `coordinator.py:66` (`await self.api.open(session)` return value discarded), `connection.py:463-465` (`start_listening` calls `open()` again because `self.connection` is `None`), `connection.py:1037-1057` (`open()` only *returns* the connection).
- Problem: the first socket (with HA's shared aiohttp session) is orphaned but stays connected with library pings; the second is opened with `session=None`. `LoxAPP3.json` is downloaded and parsed twice. Each reload adds another orphan; the Miniserver has a connection cap (error 901).
- Fix: in `open()` set `self.connection = connection` and keep `self._session`; make `start_listening()` reuse the open connection instead of re-running `open()`.
- Effort: S · Upstream: #486 #457 #487

### API-04 [high] `_send_text_command` warns that the connection is closed, then sends anyway
- Where: `connection.py:272-283` — guard has no `return`/`raise`; `self.connection.send` on `None` raises `AttributeError`.
- Fix: raise `LoxoneConnectionClosedOk` (or return for keep-alives) when not connected.
- Effort: S · Upstream: #514

### API-07 [high] `getvisusalt` handler overwrites the auth key `self._key` with a stringified dict
- Where: `connection.py:1319` (`self._key = value_dict.get("value", "")`).
- Problem: after any secured command, `_hash_token()` does `bytes.fromhex(self._key)` → `ValueError` → token refresh fails silently → token expires → clean close cascade.
- Fix: delete the line; the visual key already lives in `self._visual_hash`.
- Effort: S · Upstream: #514

### API-10 [high] Commands are sent as fragmented frames
- Where: `connection.py:276` (`await self.connection.send([command])` — a list triggers fragmentation) vs `connection.py:582-584` (key exchange sends a plain `str`, correctly).
- Fix: `await self.connection.send(command)`.
- Effort: S

### API-11 [medium] Loxone Cloud DNS redirect updates the host but not the scheme
- Where: `connection.py:895-904` (`self.url` rewritten from the redirect target), `connection.py:1020-1025` (`ws://`/`wss://` chosen from `self.scheme`, set once at `__init__` line 122). `_websocket_ssl_context` keys on the same stale scheme.
- Fix: derive `scheme`, `host`, `port` from `api_resp.url` after the redirect; strip trailing `/`.
- Effort: S

### API-12 [medium] Username is never URL-encoded in protocol commands
- Where: `connection.py:1166, 1243, 1272-1274, 1284-1286` (compare the encrypted path at line 270 which does `urllib.parse.quote`).
- Fix: `urllib.parse.quote(self.username, safe="")` once and reuse. **VERIFY** UTF-8 vs latin-1 percent-encoding expected by the Miniserver.
- Effort: S · Upstream: #506

### API-15 [medium] `_secured_queue` stores un-awaited coroutines in a `maxsize=1` queue
- Where: `connection.py:203, 1171-1176, 1326-1333`; `_send_secure` dereferences `self._visual_hash.salt` without a `None` guard (386).
- Fix: queue parameters (dataclass), unbounded `deque`, clear it in `close()`, guard `_visual_hash is None`.
- Effort: S

### API-18 [medium] Binary framing inferred from message length; `MessageHeader` for non-`0x03` leaves `payload_length` unset
- Where: `connection.py:766-790`, `message.py:200-212`. Any 8-byte payload is parsed as a header; a following `last_header.payload_length` access raises `AttributeError` and kills the listener. `websocket_protocol.py:51-103 recv_message()` implements the correct header→body sequencing but is dead code.
- Fix: always set `payload_length=0`/`estimated=False` in the UNKNOWN branch; better, adopt strict header→body reads.
- Effort: M

### API-19 [medium] `LoxAPP3.json` (often several MB) is `json.loads`-ed in the event loop
- Where: `connection.py:918-925`.
- Fix: parse in an executor (accept an optional `loads` callable or return raw bytes and let the coordinator parse via `hass.async_add_executor_job`).
- Effort: S

### API-20 [medium] Unreachable `except` blocks, possible `NameError` on `data`, unreleased aiohttp response
- Where: `connection.py:837-854` (`TimeoutError`/`ConnectionError` already caught as `OSError`), `data` only bound under `if api_resp:`, non-200 `LoxAPP3.json` response raised without `release()` (913-916).
- Fix: delete dead handlers, initialise `data = None`, use `async with` for responses.
- Effort: S

### API-21 [low] `struct.unpack("d", ...)` uses native byte order; truncated trailing records silently dropped
- Where: `message.py:272-287`.
- Fix: `"<d"`; `divmod(len, 24)` and log/raise on remainder.
- Effort: S

### API-22 [low] `check_and_decode_if_needed` is mostly unreachable (`latin-1` never fails); `detect_encoding` and the cache are dead
- Where: `message.py:24-27, 58-136`.
- Fix: `utf-8` then `latin-1` fallback; delete the rest.
- Effort: S

### API-23 [low] Dead code and stale declarations across the package
- `api.py` (8-line stub), `helper.py` (unused `hash_token`/`generate_hmac`; stdlib `HMAC` shadowed by `Crypto.Hash.HMAC`), `websocket_protocol.py:51-103` (`recv_message`), `message.py:30-55` (timers), `loxone_token.py` (`Salt`, unused imports), `const.py` (`THROTTLE_CHECK_TOKEN_STILL_VALID`, `TOKEN_REFRESH_*`, `CMD_ENCRYPT_CMD`, `DEFAULT_TOKEN_PERSIST_NAME`, `LOX_CONFIG`), `exceptions.py:27-97` (eight unused classes, some copy-pasted from a Samsung TV library), `connection.py:52-59` (`httpx` warnings filter — `httpx` is not imported anywhere), `__main__.py:5` (docstring argument order wrong).
- Fix: delete; drop `httpx` from `manifest.json` (see CORE-24).
- Effort: S

### API-24 [low] Misleading type hints and names
- `_send_text_command(...) -> TextMessage` returns `None` (244-246); `send_secured__websocket_command` double underscore (1155; callers at `__init__.py:396, 617`); `self._session_key: bytes` annotated but never initialised (149) → `start_listening:579` raises `AttributeError` not the intended `RuntimeError`.
- Effort: S

### API-25 [low] Crypto choices are protocol-mandated but undocumented
- Fixed AES-CBC IV per session (142-144, 266-269) is required by the Loxone key exchange; salt rotation (`SALT_MAX_USE_COUNT`/`SALT_MAX_AGE_SECONDS`) is the mitigation. `hashAlg` fallback to SHA1 (1260, `loxone_token.py:35`) is correct and handles #498.
- Fix: comment only; add a regression test for the missing-`hashAlg` case.
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

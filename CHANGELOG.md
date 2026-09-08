# Changelog

All notable changes to the pyloxone integration. Format based on
[Keep a Changelog](https://keepachangelog.com).

Version numbers and release dates live in `manifest.json` and are cut
by the `release` GitHub Action on every tag push — no manual `1.0.x →
1.0.x+1` commits.

## Unreleased

### Added

- CI, lint and the offline test harness (WP-0.1/0.2)
- `CHANGELOG.md`, `CONTRIBUTING.md`, `release.yaml`, `ISSUE_TEMPLATE` forms

### Fixed

- `__init__.py` — a 401 during setup (e.g. a Miniserver inside its boot
  window after a firmware update, incident 2026-09-02) no longer ends the
  entry in `setup_error`: `return False` is gone, the branch now raises
  `ConfigEntryNotReady`, and after 5 consecutive auth failures spanning at
  least 5 minutes an ERROR points at the stored credentials (a WARNING is
  logged on every earlier attempt). `ConfigEntryAuthFailed`/re-auth lands
  in WP-3.4; every setup failure now closes the API handle, including the
  503 branch (CORE-09)

- `pyloxone_api` connection layer: `open()` now stores the websocket, so
  each setup opens exactly one instead of two (API-01, JoDehli/PyLoxone#486)
- `pyloxone_api` connection layer: commands are sent as a single str frame,
  not a fragmented list (API-10); sending on a closed socket raises instead
  of crashing (API-04, #514); the `getvisusalt` handler no longer clobbers
  the token key (API-07, #514); secured commands queue parameter dataclasses
  in a deque cleared on `close()` (API-15); Cloud-DNS redirects keep the
  websocket scheme in sync (API-11); usernames are percent-encoded in
  commands (API-12, #506, VERIFY: UTF-8 vs latin-1); strict header-to-body
  frame sequencing (API-18); `LoxAPP3.json` parsed off the event loop
  (API-19); HTTP responses always released (API-20)
- `pyloxone_api`: dead files and code removed (`api.py`, `helper.py`,
  `recv_message`, unused constants/exceptions, `httpx` warning filter)
  (API-23); `send_secured__websocket_command` renamed with a deprecated
  alias (API-24); `struct` endianness pinned to little-endian (API-21);
  text decoding simplified to utf-8 with latin-1 fallback (API-22)

- `manifest.json` — `iot_class` corrected to `local_push`; dead `httpx`
  requirement removed; `websockets` ceiling to `<16`; `dependencies` pin
  to `group`; log list; version aligned to the `0.9.23` tag (TOOL-02,
  CORE-04)
- `hacs.json` - minimum HA version raised to `2026.7.0` to match the real
  first version where the integration can *import* (see CORE-25)
- `README.md` - version line fixed to `2026.7.0`; logger snippet swapped
  to `custom_components.loxone.pyloxone_api`; `custom_components.loxone.api`
  did not exist
- `services.yaml` and `en.json` - literal `re\-synchronized` escape removed
  (CORE-03)
- `config.abort.single_instance_allowed` and its lineage removed from
  `de.json`/`en.json` (dead stock HA key; the integration uses
  `data_exceeds_unique_id`-style aborts once WP-3.4 lands)
- `config.error` + `options.error` translation keys added (separate file)
  matching the real `SchemaFlowError` messages raised by
  `config_flow.validate_loxone_setup`
- `__init__.py` - `REQUIREMENTS` list deleted (a HA 0.x relic, HA>=2021
  reads the `requirements` key on the `manifest`)
- `__init__.py` — a transient 401 during setup (e.g. a Miniserver inside its
  boot window after a firmware update, incident 2026-09-02) no longer parks
  the entry in `setup_error` via `return False`: it now raises
  `ConfigEntryNotReady` and retries, escalating to an ERROR with
  credential-check guidance only after 5 consecutive auth failures spanning
  at least 5 minutes (`ConfigEntryAuthFailed`/reauth lands in WP-3.4);
  every setup failure now closes the API handle, fixing the connection
  leak on the 401/503 branches (CORE-09)

- `pyloxone_api` connection layer (WP-2.2, #514 #486 #457): a clean close
  (code 1000) now surfaces as `LoxoneConnectionClosedOk` within ~1s
  instead of 30s later as an ERROR (API-02); expected reconnect control
  flow is logged at DEBUG with one lost/restored WARNING+INFO pair per
  outage, and real failures as WARNING with the traceback at DEBUG (API-27);
  outbound commands are awaited per send and remaining background tasks are
  tracked in a set with a done-callback so a failure is no longer swallowed
  (API-14); the token-refresh loop waits for an authenticated token instead
  of spinning at 1 Hz and escalates after 3 consecutive failures
  (API-16); 401/4003 on *every* auth response raises
  `LoxoneUnauthorisedError` instead of hanging on bad credentials (API-13);
  the websocket uses `ping_interval=None` with an explicit `close_timeout`
  (API-06, VERIFY: ping off vs live Miniserver); `open()` GETs use 3 tries
  with exponential backoff instead of 100×5s, applied to all three GETs
  (API-08); the token is persisted on every change (incl. `unsecurePass`)
  via a coordinator callback, and `jdev/sys/killtoken` is sent before close
  on entry unload (API-17, VERIFY: killtoken form for JWTs)
- `fan.py` (Ventilation) — `TURN_ON`/`TURN_OFF` advertised (PC-08);
  `set_preset_mode` actually sends `setMode/<id>` instead of a no-op
  (PC-09, VERIFY command name); `set_percentage` sends the raw integer
  profile id instead of the profile name/`None` and refuses to guess when
  no mode is known yet (PC-29, VERIFY); `percentage` is a clamped int in
  0..100 instead of the raw server float (PC-30); dead code removed
  (PC-40/PC-41)
- `alarm_control_panel.py` — `code_arm_required`/`code_format` are
  setup-time attributes from `isSecured` (NUMBER when secured, `None`
  otherwise) instead of a side-effects-laden TEXT default (PC-06,
  JoDehli/PyLoxone#413); arm-home/arm-away parameter value inverted to
  agree with the state mapping (PC-31, VERIFY on a live Miniserver);
  missing `states` entries (e.g. no `nextLevelAt`) no longer crash the
  platform (PC-16); dead YAML schema, stubs and sync shims removed
  (PC-40/PC-41)
- `media_player.py` (AudioZoneV2) — `STOP` advertised so `async_media_stop`
  is reachable (PC-43); unknown `playState` values map to `idle` instead
  of leaving the entity stateless (PC-41); missing `playState`/`volume`
  state uuids no longer crash event handling (PC-16)

## 0.9.23

&mdash; (version number; release notes back-ported after first cut)

## 0.9.22 / earlier

&mdash; (earlier tags; no changelog was maintained back then)

# Changelog

All notable changes to the PyLoxone integration. The format follows
[Keep a Changelog](https://keepachangelog.com). Version numbers are the
`version` field of `custom_components/loxone/manifest.json`, tagged as
`v<version>`; the `release` GitHub Action turns the section of the tagged
version into the GitHub release notes.

This repository is a fork of [JoDehli/PyLoxone](https://github.com/JoDehli/PyLoxone).
It starts at 0.10.0; 0.9.23 is the upstream release it was branched from.
Finding ids such as `CORE-09` or `PC-31` refer to
`docs/review/2026-09-findings.md`; `#nnn` refers to upstream issues.

## Unreleased

### Changed

- Repository cleanup: the per-package agent prompts and PR bodies of the
  2026-09 review were removed from `docs/review/` (the findings catalogue,
  the remediation plan and the live-Miniserver checklist stay), the
  fork-vs-upstream comparison moved to `docs/fork-vs-upstream.md`, the
  401 incident report moved to `docs/incidents/`, and the online discovery
  test moved out of the shipped package into `tests/`.
- README, CONTRIBUTING and this changelog were rewritten; the changelog
  now has one section per release (0.10.1 to 0.10.7 were missing).
- `requirements.txt` mirrors `manifest.json` again (`websockets>=14,<16`,
  `pycryptodome>=3.20`; the unused `httpx` is gone).
- `scripts/lint`, the pre-commit hook and the CI lint job run the same
  blocking rule set; CI calls `scripts/lint --check`.
- The release workflow reads the changelog section of the tagged version
  instead of always taking the `Unreleased` block.

## 0.10.7 - 2026-09-15

### Changed

- Entity service handlers are coroutines. Home Assistant runs a plain
  `def` handler in its executor thread, which was the root cause of the
  0.10.5 and 0.10.6 fixes. Button press, climate `set_temperature` /
  `set_hvac_mode` / `set_preset_mode`, every cover command (open, close,
  stop, set position, the tilt quartet) and the fan preset / percentage
  setters now run on the event loop. Eleven dead synchronous twins that
  Home Assistant never called were removed. Command strings are unchanged.
- New guard `tests/test_no_sync_service_handlers.py` fails when a platform
  defines a Home Assistant service handler as a plain `def`.

## 0.10.6 - 2026-09-15

### Fixed

- Setting a target temperature on an `IRoomController` or
  `IRoomControllerV2` climate failed with "Failed to perform action": the
  handler wrote entity state from the executor thread, which Home
  Assistant rejects. Superseded by the 0.10.7 refactor.

### Added

- `tests/test_service_smoke.py` calls every service on every Loxone entity
  of the fixture through the real service registry.

## 0.10.5 - 2026-09-15

### Fixed

- Every cover, climate, fan and button command failed with "Failed to
  perform action" ("loop ... is not the running loop"): the outbound task
  was created from Home Assistant's executor thread. `LoxoneEntity._send`
  now hops onto the event loop when called from a worker thread.

## 0.10.4 - 2026-09-14

### Fixed

- Token refresh on firmware older than 10.2 sent
  `jdev/sys/refreshtoken<hash>/<user>` without the `/` separator. Upstream
  carries the same defect.
- A switched-off tunable-white colour picker (`hsv(0,0,0)`) no longer
  leaves the light stale, or unavailable forever when it was the first
  report.
- RGB colour picker brightness is rounded to the integer 0-255 Home
  Assistant expects.
- The presence switch of a `LightControllerV2` turns on from the event
  loop instead of the executor.
- HTTP client: the mapped status errors (`ValueError`, `PermissionError`,
  `RuntimeError`, `ConnectionError`, `TimeoutError`) surface unwrapped
  instead of collapsing into "Unexpected error during HTTP request", and
  the connection-error handlers are ordered most specific first.

### Changed

- Coverage is measured for the whole integration (it used to cover the
  API package only) with a floor of 85 %; the suite grew to 952 tests.

## 0.10.3 - 2026-09-14

### Fixed

- The Miniserver token was never persisted: the coordinator called a
  method that does not exist on `ConfigEntry`, which surfaced as "Task
  exception was never retrieved" on every refresh, and every restart
  re-authenticated with the password. Token-only entry updates no longer
  trigger a reload, which closes the persist, reload, reconnect loop the
  naive fix would have opened.
- LL responses containing raw control characters (notification texts) no
  longer kill the websocket session: parsing is lenient (upstream #517,
  parse fix and tests from upstream PR #519), and a single unparseable
  frame is logged and skipped instead of ending the session.

## 0.10.2 - 2026-09-13

### Added

- Two diagnostic sensors on the Miniserver device, `Traffic In` and
  `Traffic Out`, report messages per minute with the cumulative count as
  the `total` attribute.

## 0.10.1 - 2026-09-13

### Fixed

- Every state message logged a "Callback error" (`'NoneType' object can't
  be awaited`) because the listen loop awaited the coordinator's
  synchronous message callback. State still propagated, but the log
  flooded.
- Alarm arm-home / arm-away sent the wrong `delayedon` parameter. Verified
  on real hardware: `delayedon/0` suppresses movement (home) and
  `delayedon/1` keeps it (away). This reverts the inverted 0.10.0 change
  (PC-31) to what upstream sent; night and vacation arming follow the
  corrected mapping.

### Documentation

- A live test of a Room Ventilation Controller confirmed that the fan
  model is wrong on hardware (PC-09, PC-29): `setMode/<id>` does nothing
  and `setTimer` sets a self-reverting override. Recorded as the
  "Ventilation fan model rework" follow-up in the remediation plan; no
  code change.

## 0.10.0 - 2026-09-12

First release of the fork: upstream 0.9.23 plus the remediation of the
2026-09 code review (work packages WP-0.1 to WP-6.10 in
`docs/review/2026-09-remediation-plan.md`). `docs/fork-vs-upstream.md`
summarises the differences for users.

### Breaking changes and migration notes

- **Config entries migrate from version 4 to 5** (CORE-19): host, port,
  username, password and `verify_ssl` move from the options into the
  entry data, where the reauth flow and diagnostics redaction expect
  them. Home Assistant refuses to load an entry whose version is newer
  than the integration's, so going back to upstream requires deleting and
  re-adding the integration. Take a backup before installing.
- **Minimum Home Assistant 2026.7.0** (`hacs.json`). The upstream floor of
  2025.2.4 was wrong: `sensor.py` imports `UnitOfRatio`, which only exists
  from 2026.7 (CORE-25). Python 3.14.2 follows from that.
- **Entity display names** use `has_entity_name` (WP-5.1, CORE-26/33):
  primary entities adopt the device name, sub-entities carry short names
  ("Comfort Temperature", "Total", "Presence"). Entity ids and unique ids
  are unchanged; the reviewed registry snapshot is
  `tests/snapshots/entity_names.json`.
- A leftover YAML `loxone:` block raises a repair issue instead of being
  silently ignored (CORE-19). YAML `platform: loxone` sensors still work.
- The new `generate_groups` option defaults to off for new installs;
  existing installs keep the auto-groups until the option is set (CORE-15).
- `manifest.json`: `iot_class` is `local_push`, `dependencies` pins
  `group`, `integration_type` is `hub`, `loggers` is declared,
  `websockets<16` and `pycryptodome>=3.20` are required and the unused
  `httpx` requirement is gone (TOOL-02, CORE-04).

### Security

- `eval()` on six strings received over the websocket was removed from the
  light platform (WP-1.2, S307).
- The auth token, the user salt and the visual-password salt are no longer
  fired onto the Home Assistant event bus, and the token is no longer
  logged (WP-1.4).
- Diagnostics downloads redact the serial, project name, cloud DNS address
  and credentials (CORE-08).
- `sys.exit()` was removed from the entity constructor: one malformed
  control could terminate the Home Assistant process (CORE-02).
- `NfcCodeTouch` never exposes `lastcode` or `lasttag`.

### Added

- Config flow: the connection is verified before the entry is saved, the
  entry's `unique_id` is the Miniserver serial (no duplicates), a reauth
  flow with Home Assistant's repair issue replaces "stuck in setup error",
  form errors are translated, and the setup form prefills the address of
  a Miniserver that answers the LoxLIVE broadcast on the LAN (WP-3.4,
  WP-6.9, CORE-19).
- Repair issues: `auth_failed`, `unsupported_firmware` (below 7.0.0) and
  `yaml_config_present`, plus one issue per active Message Center entry
  (WP-5.2, WP-6.2).
- The Miniserver is a device in the registry with a diagnostic version
  sensor; every entity's `device_info` is built from one helper and rooms
  become `suggested_area` (WP-3.3, CORE-16/20, #477, #490).
- Message Center: a diagnostic `Message Center` sensor (highest active
  severity, per-severity counts) and a `Notifications` sensor; active
  entries mirror as repair issues (WP-6.2, ports upstream #515 by
  @mpcaddy).
- New control types: `InfoOnlyText`, `UpDownDigital`, `Tracker` (WP-6.6);
  `EnergyManager`, `EnergyManager2`, `PowerUnit`, `Wallbox` register
  sensors (WP-6.5); `IntercomV2` (WP-6.3, #466); `NfcCodeTouch` with a
  `lastuser` sensor, diagnostic `codeDate` / `deviceState` sensors and a
  `loxone_nfc_auth` bus event; `LightsceneRGB` as an RGB light with an
  optional scene select (WP-6.10, PS-27); standalone `ColorPickerV2`
  (PC-05).
- `PresenceDetector` illuminance and noise sub-sensors (WP-6.1, #461).
- `InfoOnlyDigital` device class inferred from the control's on/off text
  and category (WP-6.4, #402).
- `AudioZoneV2`: turn on / off, mute, source selection, `source` and
  `favourites` attributes, track metadata, and `STOP` (WP-6.7, PC-43).
- Alarm: `ARM_NIGHT` and `ARM_VACATION`, `armed_delay` attributes (WP-6.8,
  #323). Gate: `SET_POSITION` when the block streams a position. Jalousie:
  a "Sun auto" select (Off / Auto / Shade). `AcControl`: `hvac_action`
  (WP-6.8, #398).
- The `text` platform is loaded (upstream shipped it but never listed it).
- `loxone.reload` accepts an optional `entry_id`; `loxone_event` carries
  the `entry_id` of the Miniserver that produced it (CORE-27).
- Tooling: CI with a blocking ruff rule set, pytest on Python 3.14,
  hassfest, HACS validation, translation parity and a docs-vs-services
  check; a release workflow that refuses a tag that does not match the
  manifest version; `CHANGELOG.md`, `CONTRIBUTING.md`, issue forms, a
  devcontainer and pre-commit hooks. The test suite went from 69 to 707
  tests on an offline `LoxAPP3.json` fixture, including contract tests.

### Changed

- Reconnects happen in place with exponential backoff; entities flip to
  unavailable and back instead of the whole integration reloading and
  recreating every entity (API-09, CORE-05; #475, #486, #491).
- Multi-instance isolation: state dispatch and commands are scoped to the
  config entry, so two Miniservers no longer receive each other's commands
  or acknowledgements (WP-3.2, #491, CORE-04/11/27, PS-13/18).
- Lifecycle: the session task is tracked and cancelled on unload, start /
  stop listeners no longer accumulate across reloads, an options change
  reloads the entry, and the coordinator lives on `entry.runtime_data`
  (CORE-03/06/10/12/13/14/29/31).
- Logging: a normal session produces no WARNING or ERROR records; a clean
  close (code 1000) is detected within about a second instead of 30 s
  later as an ERROR; one lost / restored pair per outage (API-02, API-27,
  #514).
- Connection layer: one websocket per setup instead of two (API-01, #486);
  commands are sent as a single frame (API-10); sending on a closed socket
  raises (API-04); the `getvisusalt` handler no longer clobbers the token
  key (API-07); secured commands queue in a deque cleared on close
  (API-15); cloud DNS redirects keep the scheme (API-11); usernames are
  percent-encoded (API-12, #506); strict header-to-body frame sequencing
  (API-18); `LoxAPP3.json` is parsed off the event loop (API-19); HTTP
  responses are always released (API-20); library pings are off with an
  explicit close timeout (API-06); GETs use three tries with backoff
  (API-08); the token is persisted on every change and `killtoken` is sent
  on unload (API-17); the refresh loop waits for an authenticated token and
  escalates after three failures (API-16); 401 / 4003 on any auth response
  raises (API-13); dead files removed (API-23); struct endianness pinned
  (API-21); text decoding is UTF-8 with a latin-1 fallback (API-22).
- Scenes are generated as soon as the `moodList` stream arrives; the
  3-second timer and the `hass.data["light"]` scraping are gone (PS-17).
- Lint ratchet: `F401`, `G004`, `ERA001`, `B006`, `BLE001`, `TRY400` and
  `ARG` are at zero and blocking (WP-5.3, TOOL-07).
- `except A, B:` (Python 3.14 only) rewritten to the parenthesised form so
  the files parse on 3.13; the supported floor is unchanged.
- The test suite runs in 13 s instead of 70 s.

### Fixed

- A transient 401 while the Miniserver reboots no longer kills the entry:
  setup retries and escalates to a reauth flow only after five failures
  spanning five minutes (CORE-09, `docs/incidents/`).
- Home Assistant 2026.9: `Group.async_create_group` gained a required
  `context` argument, detected at runtime so 2026.7 through 2026.9 all
  work.
- Climate: `NameError: comfort_cool` in building-protection mode (PC-02,
  #416); `AcControl` missing states return `None` (PC-10, #479, #398);
  target temperature in dual modes and the `TARGET_TEMPERATURE` vs
  `_RANGE` feature flag (PC-12); unknown modes keep the previous state
  (PC-13); fan / swing modes only when the control has them, never
  `setFan/None` (PC-24); `set_hvac_mode(OFF)` sends only `off` (PC-25);
  `setOperatingMode` spelling (PC-20); stable preset literals (PC-32);
  shared mode tables (PC-26); `hvac_action` falls back to the controller's
  own valve states (PC-28); temperature unit parsed from the format string
  (PC-23).
- Cover: Window and Gate `stop_cover` send `stop` instead of the opposite
  direction (PC-07, #501); the Jalousie services are gated on the
  advertised features and raise `ServiceValidationError` on other covers
  (PC-14); custom feature bits moved out of Home Assistant's range (PC-27);
  missing optional states no longer `KeyError` (PC-15/16, #501);
  `manualLamelle` uses a 3-decimal format (PC-19).
- Lights: brightness-only `turn_on` on an RGB picker with unknown colour
  mode sends `setBrightness` (PC-03, upstream PR #512 regression); dimmers
  and `LightControllerV2` honour their min / max in both directions, so
  brightness 1 is no longer off and the full slider no longer snaps back
  (PC-17/18); tunable white before the first state (PC-11); effect plus
  brightness sends both (PC-33); the Kelvin floor is 2700 K (PC-38/39).
- Fan: `TURN_ON` / `TURN_OFF` advertised (PC-08); percentage is a clamped
  integer (PC-30).
- Alarm: `code_format` derives from `isSecured` (PC-06, #413); a missing
  `nextLevelAt` no longer crashes the platform (PC-16).
- Media player: unknown `playState` maps to `idle` (PC-41); missing state
  uuids no longer crash event handling (PC-16).
- Sensors and simple platforms: one bad control no longer aborts a whole
  platform; `SmokeAlarm` reads `level` (PS-04); the presence switch reads
  `states.presence` (PS-06); intercom sub-controls without `active` are
  skipped (PS-05); `map_range` no longer divides by zero and `get_all`
  tolerates structure files without `controls` (CORE-32, #292); YAML
  sensors without a name get a unique id and the broken `value_template`
  is gone (PS-07); a `Meter` without a format no longer kills the sensor
  platform (PS-08); the error value publishes `unknown` and only numeric
  formats get a `state_class` (PS-09); `override_reason` values are
  translatable slugs (CORE-22); Meter registers get explicit device and
  state classes and resetting kWh counters are not `total_increasing`
  (PS-21); numbers take min / max / step / unit from the control (PS-15);
  buttons expose `last_pressed` instead of overriding the final `state`
  (PS-16); radios without outputs are skipped and a locked radio raises
  (PS-19). (#402, #481, #492)
- Entity base: listener leak (CORE-01), `should_poll` off and bookkeeping
  attributes excluded from the recorder (PS-12/22), initial-state
  correctness (WP-1.3, #475).
- Ventilation device identity (PC-04); no reference to a non-existent
  `via_device` (PS-17); the structure file is deep-copied so platform
  setup cannot poison a later reload (CORE-20); the scene mood-list
  subscription is entry-scoped (CORE-17); an empty device uuid raises
  (PC-05).
- Translations: `config.error` and `options.error` keys match the flow,
  the dead `single_instance_allowed` key is gone, the `re\-synchronized`
  escape is fixed (CORE-03), and `de.json` is a superset of `en.json`.

## 0.9.23

Last upstream release (JoDehli/PyLoxone, commit `7561247`). No changelog
was maintained before this fork.

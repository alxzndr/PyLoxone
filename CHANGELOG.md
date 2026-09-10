# Changelog

All notable changes to the pyloxone integration. Format based on
[Keep a Changelog](https://keepachangelog.com).

Version numbers and release dates live in `manifest.json` and are cut
by the `release` GitHub Action on every tag push — no manual `1.0.x →
1.0.x+1` commits.

## Unreleased

### Changed

- Config flow rewrite (WP-3.4, entry version 5): adding a Miniserver now
  verifies the connection first and stamps the config entry's `unique_id`
  with the Miniserver serial, so the same Miniserver cannot be added twice.
  Host/port/user/password/verify_ssl live in the entry **data** instead of
  options (options keep only the preferences: `generate_groups`,
  `generate_scenes`, `generate_scenes_delay`,
  `generate_lightcontroller_subcontrols`); existing entries migrate v4→v5
  automatically. Reauth: failed setup after the bounded 401 retry window
  (5 attempts / 5 min) or a rejected live session now starts a reauth flow
  (with HA's `config_entry_reauth` repair issue) instead of parking the
  entry forever; form errors use translation keys (`cannot_connect`,
  `invalid_auth`, `invalid_host`, encoding errors). A leftover YAML
  `loxone:` block registers a persistent repair issue — the import flow it
  was passed to never existed (CORE-19, CORE-18, CORE-09,
  API-13 consumer side, CORE-30).

### Added

- Meter family (WP-6.5, PS-26): `EnergyManager`, `EnergyManager2`,
  `PowerUnit` and `Wallbox` controls now create sensor sub-entities
  for their registers with the same names and classifications as the
  legacy `Meter` ("Actual" → power/measurement; "Total" / "Total Neg"
  → energy/total_increasing; "Level" → energy/measurement).  Only the
  registers a control advertises are created, and the registers of one
  control share one device.  The pattern is one generalised pure
  helper (`meter_sub_sensor_kwargs`) driving all five types.
- More control on existing controls (WP-6.8, JoDehli/PyLoxone#323, #398):
  the alarm panel now advertises `ARM_NIGHT` and `ARM_VACATION` (the
  Mushroom night/vacation icons work: night arms with delay — the
  `delayedon/1` home arm —, vacation arms without it), and the arming
  delay is surfaced as `armed_delay` / `armed_delay_total_delay`
  attribute values in whole seconds (or `None` before the server has
  streamed them); Gate cover entities with a `position` state stream
  advertise `SET_POSITION` (`cover.set_cover_position` sends
  `manualPosition/<position>`); Jalousies with sun automation get a
  "Sun auto" select entity on the same device (`Off` / `Auto` /
  `Shade` sends `NoAuto` / `auto` / `shade`) alongside the existing
  cover services; AcControl climates report `hvac_action` and their
  setter services (`set_hvac_mode`, `set_temperature`, `set_fan_mode`,
  `set_swing_mode`) now run on the event loop, so they actually reach
  the Miniserver instead of failing in the executor.
  **VERIFY**: the night/vacation mapping, the gate `manualPosition`
  orientation, and the `Shade` display pin need a live-Miniserver check
  before relying on them.  (Night and vacation passed.)

- New Loxone control types (WP-6.6, PS-26):
  `InfoOnlyText` shows up as a read-only text sensor alongside the
  writable `TextInput` (`sensor.set_value` on it is refused locally);
  `UpDownDigital` creates two buttons, one per rocker side ("Up" / "Down"),
  sending the `UpOn` / `DownOn` on command on press — the control has no
  states itself; and `Tracker` shows its `entries` JSON list as a
  comma-joined sensor (`entries` and `count` attributes).  Control
  discovery (`get_all`) now also scans `subControls` when asked to recurse,
  picking up the `Tracker` a structure file nests under an `Alarm` (its
  `sensors` state).  The `Tracker` entry-list shape and the `UpDownDigital`
  rocker-on-press semantics are assumptions about the Miniserver protocol
  and need a live-Miniserver check before this is assumed right (flagged
  in the PR).  Existing entities are unchanged.

- Message Center sync (WP-6.2): a diagnostic `Message Center` sensor per
  top-level message-center control of the structure file (state:
  highest active severity class, `status` attribute: per-severity
  counts) and a `Notifications` text sensor for the Miniserver's global
  notification stream, both on the Miniserver device.  Changes on the
  Message Center's `changed` stream re-sync via the `getEntries` command;
  active entries are mirrored as persistent repair issues (with severity
  WARNING/ERROR/CRITICAL, a `more-info` link when the affected control
  has an entity, and the official help link), and resolved (historic)
  entries delete their issue again.  Ports the approach from
  JoDehli/PyLoxone#515 by @mpcaddy.
- `InfoOnlyDigital` binary sensors get an inferred `device_class` (WP-6.4,
  #402): first the control's own on/off display text (`details.text`),
  then the control's category name.  Common EN/DE label phrases map to
  `motion`, `moving`, `opening`, `door`, `window`, `smoke`, `gas`,
  `moisture` and `vibration`; categories are substring-matched ("Doors"
  → `door`, "Energy" → `plug`).  Controls without a matching label or
  category stay classless as before, and the keyword tables are a
  heuristic that needs a live-Miniserver check (flagged in the PR).
  Entity ids, device identities and unique ids are unchanged.
- Intercoms now also match `IntercomV2` (WP-6.3, JoDehli/PyLoxone#466):
  newer firmware (~16.2+) reports the same intercom block with the V2
  suffix, and before this change the switch platform simply skipped it
  (only a light and a custom push button got imported).  Both block
  shapes now fan out their `subControls` the same way — each sub-control
  that advertises an `active` state stream becomes a switch on the
  intercom's device (e.g. a door-lock sub-control); sub-controls without
  one are skipped with a warning (PS-05 semantics, unchanged).  The
  match lives in one place (`INTERCOM_TYPES`) and the fan-out in one
  pure helper (`intercom_sub_control_kwargs`).  **VERIFY**: the V2
  sub-control modelled in the test fixture (one `active` stream, master
  and sub on the intercom's shared device) is inferred from the
  reporter's V2-suffix workaround, not from a live structure file — a
  live-Miniserver check is required before merge (item in
  `docs/review/LIVE-MINISERVER-CHECKS.md`).  Existing entities are
  unchanged.
- Device registry and identity (WP-3.3): the Miniserver itself is now a
  device in the registry (`Miniserver <serial>`, model
  `ControlVersion8.61.0` already reported by the server, the real
  software version instead of `unknown` as reported by a version sensor
  that now lands on that same device with `entity_category: diagnostic`),
  and every platform's `device_info` is built from that one helper with
  per-call payloads: no more module-level device cache (CORE-20/CORE-16,
  #477 #490). Rooms flow into `suggested_area` correctly now that room/
  category name resolution is idempotent, so later platforms no longer
  blank out the room of earlier ones; scene entity object ids pick up the
  device's area prefix in 2026.x for that reason
- CI, lint and the offline test harness (WP-0.1/0.2)
- `CHANGELOG.md`, `CONTRIBUTING.md`, `release.yaml`, `ISSUE_TEMPLATE` forms

### Documentation

- README, CONTRIBUTING and CI docs parity (WP-5.4, TOOL-13, TOOL-14 remainder, CORE-24 docs side):
  - "Configuration" and "Configuration options" sections: the connection
    fields (incl. the `verify_ssl` security note) and all five options with
    defaults and effect — `generate_scenes`, `generate_scenes_delay` (why
    the minimum is 3), `generate_lightcontroller_subcontrols`,
    `generate_groups` (new-install default off vs pre-option installs)
  - "Services" table covering **all** services of `services.yaml` (was: 1 of 7)
  - "Entities and attributes" reference per platform (domain, extra state
    attributes, entity services)
  - `loxone_event` payload documented, including the multi-instance
    `entry_id` field; logger snippet fixed (`custom_components.loxone.api`
    never existed — the client is `custom_components.loxone.pyloxone_api`)
  - minimum-version claim aligned with `hacs.json` (Home Assistant
    2026.7.0+) and the Python 3.14.2 floor
  - CONTRIBUTING: `scripts/setup`, `scripts/lint`, `pytest`, minimum
    Python 3.14.2
  - CI now has a `docs-services` job (and `tests/test_docs_readme.py`) that
    fails when a `services.yaml` service is not documented in the README
### Changed

- Entity naming (WP-5.1, CORE-26 remainder / CORE-33): every Loxone entity
  now uses `has_entity_name = True`. Primary entities no longer repeat the
  control's full name as their own name (no more "Living Room Light
  Switch Living Room Light Switch"); the entity carries no own name and adopts the device
  name, which keeps the *exact* control name. Sub-entities carry short
  names without the parent prefix instead ("Override Reason", "Comfort
  Temperature", "Total", "Presence", …); unique ids, entity ids and device
  identities are unchanged. The override-reason enum sensor's translation
  moved from the unreachable `entity.sensor.loxone.override_reason` path to
  HA core's layout `entity.sensor.override_reason` (CORE-22), and the
  shipped en/de/cs files now nest under it; the unused legacy
  `room_controller` preset block dropped from en.json. Reviewed registry
  snapshot: `tests/snapshots/entity_names.json`
  (pre-change baseline: `tests/snapshots/entity_names.pre.json`;
  regenerate with `LOXONE_ENTITY_SNAPSHOT_OUT=… pytest -q
  tests/test_entity_name_snapshot.py` and diff in the PR). The reviewed
  diff touches the stored names only — every `entity_id` and
  `unique_id` is unchanged, so upgrades re-name nothing and lose no
  entity id.

### Added

- Repairs (CORE-30, WP-5.2): the `auth_failed` repair issue is shown while
  the stored credentials are rejected and a reauthentication is pending, and
  disappears once reauth completes with working credentials; the new
  `unsupported_firmware` warning issue appears when the structure file
  reports a firmware below 7.0.0 (the floor of the JSON websocket API the
  integration relies on) and is removed again at/above the floor. Both
  carry en/de/cs translations alongside the existing `yaml_config_present`
  issue from WP-3.4. (Upstream: JoDehli/PyLoxone#486, #515 area.)
- Config entries store their coordinator on `entry.runtime_data` instead of
  `hass.data["loxone"]` (CORE-31, WP-5.2): nothing is left behind after the
  last unload, and the stale empty `hass.data["loxone"]` dict no longer
  survives (verifiable in the test suite: `tests/test_runtime_data.py`).

### Added

- Presence detectors now expose the `illuminance` and `noise` states
  they advertise as analog sub-sensors (`Illuminance`, `Noise`) on the
  sensor platform, attached to the presence binary sensor's device —
  lux readings pick up the `illuminance` device class, noise is a plain
  numeric measurement (upstream issue JoDehli/PyLoxone#461, WP-6.1).

### Changed

- The `loxone_options` option `generate_groups` (new: default `false`;
  missing key keeps groups on for pre-option installs) gates the Loxone
  auto-groups; group membership now converges on the entry's current
  entities — a removed group is recreated with its members, and the
  master group includes dimmers/climates/accontrollers instead of
  dropping them (CORE-15)
- `generate_groups` default off for new installs (config flow); options
  flow preserves stored values

### Fixed

- Ventilation device identity: the fan now carries its own name/model
  (`Ventilation 2` / `Ventilation` — the PS-14 constant, not the literal
  `unknown` the string did not match) instead of reporting under a
  presence-detection derivative; the presence/humidity/temperature sub
  sensors share the fan's device (PC-04)
- `device_info` no longer references a non-existing `via_device`;
  Miniserver device linking via `via_device` stays a follow-up until the
  remaining platforms migrate (they still build payloads through the
  compat shim) (PS-17)
- Structure file is no longer mutated in place: `get_all` hands out deep
  copies, so platform setup (room resolution, `type` rewrites, runtime
  references, Intercom sub-control name joins) can no longer poison a
  later setup in the same process — the silent "platform set up zero
  entities on the second reload" class of bug is gone (CORE-20 support)
- Scene mood-list subscription is entry-scoped (`async_on_unload`)
  instead of appending to the dead `MiniServer.listeners` list, which
  was never called back and leaked on unload (CORE-17)
- New config entries are stamped with `unique_id` = Miniserver serial at
  setup time (idempotency against reload loops)
- `get_or_create_device` raises on an empty uuid (PC-05) and the dead
  `device_class` properties on ventilation/text sensors are deleted
  (PS-10/PC-35)

- Setup/unload/reload lifecycle: the session task is tracked on the
  config entry and cancelled on unload (CORE-03); `EVENT_HOMEASSISTANT_STOP`
  / `STARTED` listeners no longer accumulate across reloads and the
  startup group hook uses `homeassistant.helpers.start.async_at_started`
  (CORE-06); an empty persisted token is no longer passed to the
  connection (CORE-10); the coordinator passes `config_entry=` and uses
  the stock first-refresh instead of overriding
  `async_config_entry_first_refresh`, and `async_cleanup` guards
  `api is None` (CORE-12); unload unloads platforms first and only
  cleans up entry resources when that succeeds (CORE-13); the redundant
  `async_load_platform` loop, six stub `async_setup_platform` functions
  and the dead `PLATFORM_SCHEMA` leftovers are deleted (CORE-14);
  changing an option now schedules a reload of that entry (CORE-29);
  the `loxone.reload` service takes an optional `entry_id` and reloads
  per entry via `async_schedule_reload` (CORE-05, tail of the interim
  fix)

- Multi-instance isolation (WP-3.2, #491): inbound and outbound traffic is
  fully separated per config entry. The coordinator dispatches received
  values only through per-uuid signals of its own entry's signal dispatcher,
  and every entity sends commands through its own entry's API instead of a
  global `loxone.send` bus event (CORE-04): with two Miniserver entries,
  service commands previously went to whichever listener fired first,
  could be addressed to the *other* entry's uuid, and one global
  `success` event answered every command-ack listener (CORE-11, PS-13).
  Room-controller demand events are a per-(entry, room-uuid) signal, so
  another entry's thermostat can no longer toggle this one (CORE-27).
  The `loxone.send`-shaped domain event is kept only by legacy partial
  send channels, with the coordinator subscribed to ignore the stray
  `done` (PS-18; removal lands in WP-3.6). Entities that HA reuses across
  a reload resolve the *current* platform's config entry, so a stale
  coordinator from the previous setup is never captured (CORE-11).
  New tests assert two entries with shifted Loxone uuids keep state and
  commands independent in both directions

- Sensor/binary/switch/select/number/button/scene platforms: one bad
  control or a missing detail key no longer aborts the entire platform
  (per-control `try/except` in every `async_setup_entry`, shared
  `iter_controls` helper); a `SmokeAlarm` binary sensor is read from its
  `level` state (on when > 0) instead of `areAlarmSignalsOff` (PS-04);
  `LoxoneLightPresenceSwitch` guard and constructor both read
  `states["presence"]` (PS-06); intercom sub-controls without an `active`
  state are skipped with a log (PS-05); `map_range` no longer divides by
  zero on a degenerate range and `get_all` tolerates structure files
  without `controls`/`type` (CORE-32, Uni Ulm fuzzing PR #292); YAML
  sensors without a `name` get a usable unique id and the broken
  `value_template` handling is removed (PS-07); a `Meter` without a match
  register format no longer kills the sensor platform (PS-08); analog
  `ERROR_VALUE`/`None` publish `unknown` and only numeric formats advertise
  a state_class (PS-09); `override_reason` sensor values are slugs that
  translate, with a single `unknown` fallback (CORE-22); Meter registers
  get explicit per-register device/state classes and resetting kWh values
  no longer claim `total_increasing` (PS-21); `LoxoneNumber` starts
  unknown, listens on the `value` state, and takes min/max/step/unit from
  the control details (PS-15); `LoxoneButton` no longer overrides the
  `@final` `ButtonEntity.state` — the press echo becomes a `last_pressed`
  attribute (PS-16); scenes are generated from the structure file as soon
  as a `moodList` stream is seen, with no 3-second timer, no
  `hass.data["light"]` scraping and a tracked listener (PS-17); Radios
  without outputs are skipped with a log and a locked Radio raises
  `HomeAssistantError` on select (PS-19)
  (JoDehli/PyLoxone#402 #481 #492)

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
- `cover.py` (Jalousie/Gate/Window) - a Window `stop_cover` now sends a
  real `stop` instead of driving the window to the opposite end when
  closing (PC-07, JoDehli/PyLoxone#501); `Gate.stop_cover` no longer
  re-sends the opposite direction but sends `stop` (VERIFY on a live
  Miniserver). The Jalousie-only services `enable_sun_automation` /
  `disable_sun_automation`/`quick_shade` only reach Jalousies that
  advertise the capability — a Gate/Window target raises
  `ServiceValidationError`, not `AttributeError` (PC-14) — and run on the
  event loop. The custom feature bits moved to `1 << 12`/`1 << 13`, far
  out of HA's cover-feature range (PC-27). Missing optional states
  (`targetPosition`, `direction`, `shadePosition`, `animation`, …) no
  longer `KeyError` (PC-15/PC-16, JoDehli/PyLoxone#501);
  `Jalousie.__init__` no longer injects empty-string keys into the shared
  structure JSON (PC-36); `manualLamelle` commands use an explicit
  3-decimal format with a documented jitter constant (PC-19); dead code
  and the `shade_postion_as_text` typo removed (PC-40/PC-41)
- `services.yaml` - the three cover services' target narrowed to
  `integration: loxone` (kept `domain: cover`)
- `light.py`/`lights/*` — brightness-only `turn_on` on an RGB colour
  picker with an unknown colour mode now emits `setBrightness/…` instead
  of nothing (PC-03, JoDehli/PyLoxone PR #512 regression); dimmers and
  LightControllerV2 honour their (master dimmer's) min/max in both
  directions — the full-brightness slider no longer snaps back — and HA
  brightness 1 no longer rounds to Loxone 0 = off (PC-17/PC-18);
  TunableWhite `turn_on` before the first state no longer raises
  (PC-11); standalone `ColorPickerV2` controls are now created, also for
  the integer `pickerType` values, with a proper string device
  identifier (PC-05/PC-43); the `masterColor` subControl filter uses
  `find() > -1` (PC-37); effect + brightness on a LightControllerV2
  sends both (PC-33); the fake `device_class` control-type marker and
  dead helpers removed (PS-10, PC-40 light lines); `async_turn_off`
  accepts `**kwargs` and the kelvin floor is firmly 2700 K (PC-38/PC-39)
- `climate.py` — `set_temperature` with only `target_temp_low` in building-
  protect mode no longer crashes the event loop with `NameError: comfort_cool`
  and compares against the frost-protect temperature (PC-02, #416);
  `AcControl` properties returning missing state entries return `None`
  instead of `KeyError` (PC-10, #479 #398); `RoomControllerV2`
  `target_temperature` no longer falls off the end to `None` in dual modes,
  and the entity advertises `TARGET_TEMPERATURE` *or* `TARGET_TEMPERATURE_RANGE`,
  never both (PC-12, #416); unknown Loxone `operatingMode`/`activeMode`
  values keep the previous state and log once (PC-13); `AcControl`
  `FAN_MODE`/`SWING_MODE` are only advertised when the control has
  `fanspeeds`/`airflows` states, those lists are parsed once, and
  `set_fan_mode`/`set_swing_mode` no longer send `setFan/None` (PC-24,
  #398); `AcControl.set_hvac_mode(OFF)` sends only `off` (PC-25);
  `setOperatingMode/0` for the schedule preset is no longer spelled
  `setOperationMode/0` (PC-20); FIXED/FIXED-DYNAMIC presets read back as
  stable literals and `preset_modes` is a constant (PC-32); the
  legacy `IRoomController` and V2 mode tables are single shared
  read/write pairs (PC-26, VERIFY against a live V1 Miniserver);
  `RoomControllerV2.hvac_action` falls back to the controller's own
  valve/prepare states when no `ClimateController` demand event exists,
  and the duplicate `hvac_modes` line is gone (PC-28); one shared
  `temperature_unit_from_format` helper for all three platforms, so a
  format string starting with `°C` no longer reads as Fahrenheit
  (PC-23, #398); all remaining direct state/details indexing uses
  `.get()` (PC-16 climate lines); f-string logging replaced with lazy
  formatting (PC-41 climate lines)
## 0.9.23

&mdash; (version number; release notes back-ported after first cut)

## 0.9.22 / earlier

&mdash; (earlier tags; no changelog was maintained back then)

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

### Fixed

- **Changed for `LightControllerV2` automations and dashboards:** the
  controller light now tells the truth about on/off. Its state followed
  the active moods with one hardcoded rule ("mood 778 means off"), so a
  controller showing mood 0, or no mood at all after the master was
  moved, claimed to be on. The state now follows the active moods (off
  moods: 0, which the structure file defines as always off, and 778, the
  static "Aus" mood of some installations), then — in manual mode, with
  no mood active — the master dimmer position, and failing that the
  controller's own circuits; it stays unknown until the Miniserver has
  reported the active moods. Upstream `e43301f` had the same idea but
  read every controller without a master dimmer as off in manual mode
  while its circuits were lit; this version falls back to the circuits
  instead. (PC-33)
- A **plain `light.turn_on`** of a light controller (no brightness, no
  effect) no longer sends the undocumented `changeTo/99` — a mood id no
  source documents, which a Miniserver would acknowledge even if it did
  not know it. It now plays the first mood of the controller's mood list
  that is not an off mood, or the spec word `plus` when the mood list
  holds none; a controller that is verifiably already on still gets
  nothing, so the call never steals a running mood. Which mood actually
  comes up is settled by live check #29 before the release.
  (Upstream `e43301f`/`466ea0e`)
- A `LightControllerV2` whose structure entry lacks one of the optional
  mood states (`moodList`, `additionalMoods`, `activeMoods`) no longer
  raises while the light platform is setting up; before, the whole
  platform lost its entities. (Upstream `64ad11d`, LightControllerV2
  part)
- The `Switch` circuit of a light controller starts as *unknown* instead
  of carrying the string `"unknown"` as its state — a truthy value that
  claimed the light was on before its first report. (Upstream `173d2ea`,
  PS-02)

## 1.0.1 - 2026-10-18

### Fixed

- The NFC Code Touch `Code Date` sensor showed a date 39 years in the
  future (2065 instead of 2026). The reader's `codeDate` and `historyDate`
  are Unix timestamps in milliseconds (structure file v16); they were read
  against the 2009 Loxone epoch. Found on the maintainer's reader right
  after installing 1.0.0; the bug dates from 0.10.0.
- **Changed for automations on `loxone_nfc_auth`:** the event now fires
  when the reader logs an access (`historyDate` changes). It used to fire
  when `codeDate` changed, which per the structure file means a code was
  *edited*, and once at every start of Home Assistant. The first value
  after a start or reconnect is now a baseline. `code_date` in the event
  is the time of the access. A reader without `historyDate` keeps the old
  trigger.
- Removing the device of a block deleted in Loxone Config logged a
  deprecation on Home Assistant 2026.10 (a device now belongs to one
  config entry); it now uses `async_remove_device`.

### Added

- NFC Code Touch readers get a `Last Access` timestamp sensor
  (`historyDate`). `Code Date` is now described as what it is: when a code
  was last changed.

## 1.0.0 - 2026-10-18

The fork continues at 1.0.0 because upstream has since released its own,
different 0.10.0 and 0.10.1; see `docs/fork-vs-upstream.md`. Most of the
connection and security fixes below are ported from upstream 0.10.0 and
0.10.1, checked against this fork's code and tests.

### Added

- A control locked in Loxone (from the visualisation or by logic) shows
  `locked`, `locked_by` and `lock_reason` attributes, and a command to it
  fails with an error that names the lock instead of being silently
  ignored by the Miniserver. Every control on the maintainer's Miniserver
  publishes this state. Gates show `prevent_open` / `prevent_close`, and
  automatic blinds `safety_active` and `movement_locked`.

- The "Status" block (`TextState`) is a read-only text sensor (#529). The
  state is the block's text, cut to Home Assistant's 255-character limit
  with the full text in the `text` attribute; from Loxone 13.1 the icon and
  colour arrive as the `loxone_icon` and `color` attributes. A missing or
  malformed icon payload leaves the attributes out. The README now also
  explains that memory flags (Merker) appear once "Use" is ticked in
  Loxone Config, as the already supported `InfoOnlyDigital`,
  `InfoOnlyAnalog` and `InfoOnlyText` controls.

- The window and door monitor (`WindowMonitor`) is supported (#524). Its
  contacts did not appear in Home Assistant at all, because the monitor is
  the only place the Miniserver publishes them. Each contact gets its own
  device in its own room, with a `window` binary sensor (tilted counts as
  open), a `State` sensor that keeps `tilted` apart from `open`, and a
  `Lock` binary sensor that appears once the contact has reported a lock
  state, since the structure file does not say which contacts have one.
  The values are decoded as the bitmask the structure file describes, so a
  lockable door's `8` and `16` read as closed, as the reporter observed. A
  contact listed by a room monitor and the central monitor appears once,
  and motorised windows and gates stay covers. The unique ids carry a
  suffix (`-window`, `-state`, `-lock`), because a contact can be a control
  that already has an entity of its own. The monitor's counters are
  sensors on the monitor's device; only `Open` and `Tilted` are enabled by
  default.

- `ValueSelector` controls (the Up-Down Buttons / push-button +/- blocks)
  become `number` entities on a device of their own, ported from upstream
  PR #522. Unlike a `Slider`, the Structure File publishes their min, max
  and step as states rather than details, so the range follows the
  Miniserver at runtime; the entity is unavailable until the first value
  arrives, and a zero step is ignored. They join the `Loxone Numbers`
  group, and the control's `increaseOnly` detail is the `increase_only`
  attribute.

- Ready for Home Assistant 2026.11, while 2026.7 stays the floor. The
  climate entities implement the renamed `native_current_temperature`,
  `native_target_temperature(_high/_low)` and `native_temperature_unit`
  members, so 2026.11 logs no deprecation for them (the old names keep
  being served on 2026.7-2026.10), and the integration leaves the new
  `temperature_unit` state attribute to Home Assistant. Device info is
  built as the release's own `DeviceInfo`, which is a dataclass from
  2026.11 and still the plain dict before; the `via_device_id` link to the
  Miniserver is unchanged. A CI job runs the suite against the newest
  Home Assistant beta and may fail without blocking.

- Entities of blocks deleted in Loxone Config are removed at setup, with
  the devices this leaves empty. They used to stay forever as "no longer
  provided". An entity counts as stale only when its unique id starts with
  a Loxone id that occurs nowhere in the structure file, and if more than
  half of the entities would go, the structure file is treated as
  incomplete and nothing is removed. A device whose block still exists can
  no longer be deleted by hand, because it came straight back at the next
  setup.

- On Home Assistant 2026.11, changing an entity id renames the entity in
  place instead of removing and re-adding it.

- `scripts/pyloxone-cli` runs the protocol client without Home Assistant
  to check the connection to a Miniserver. It asks for the password
  instead of taking it on the command line.

- Intercoms get a doorbell. Each `Intercom` and `IntercomV2` has an event
  entity `Doorbell` (device class `doorbell`, event type `ring`) that fires
  when the intercom's `bell` state goes from 0 to 1. The first value after
  Home Assistant starts, after a reload and after every reconnect is taken
  as the starting point and never fires, so a bell that is already ringing
  when the connection comes back, or a value the Miniserver merely
  repeats, does not run a doorbell automation; a ring that starts and ends
  while the connection is down is missed. An `Answer` button sends
  `answer`, which stops the bell, and an `IntercomV2` also gets a `Mute`
  switch that follows the block's `muted` state and sends `mute/1` and
  `mute/0`. All of them sit on the intercom's device. The test fixture now
  models both intercom blocks as the structure file describes them.

### Changed

- **Breaking for automations on intercom outputs:** the outputs of an
  `Intercom` or `IntercomV2` (door openers, gate releases and the like) are
  `button` entities now, not switches. The Loxone structure file (v16)
  describes them as pushbuttons; the switches sent `on`, which kept a door
  strike energised until a `turn_off` followed, and if that never came
  (an automation error, a dropped connection) the door stayed released. A
  press of the new button sends `pulse`, so the output fires once. The
  button keeps the output's name and unique id and sits on the intercom's
  device, so the entity id changes only in its domain, for example
  `switch.hall_entrance_intercom_door_lock` becomes
  `button.hall_entrance_intercom_door_lock`. On the first start the old
  switch entries are removed from the entity registry, each with a WARNING
  in the log naming the new button, and a repair issue lists them.
  Automations, scripts and dashboards that turned an old switch on or off
  must call `button.press` on the new entity instead. Outputs without an
  `active` state, which used to be skipped, are buttons now too.

- **Breaking for automations on the `Override Reason` sensor:** code 14
  now reads `low_request` ("Low Request") instead of `fixed` ("Fixed
  Setpoint"), and codes 9-20 get names of their own (`cooling_forbidden`,
  `heating_forbidden`, `manual`, `no_cooling_available`,
  `no_heating_available`, `low_request`, `pause_timer`, `different_mode`,
  `load_shedding`, `locked`, `device_offline`,
  `different_mode_than_central`) where they read `unknown` before (from
  upstream PR #530). `fixed` confused the override reason with the
  room controller's fixed-setpoint *active mode*, which also uses 14. An
  automation that compared the sensor with `fixed` should compare with
  `low_request`; the slugs for 0-8 are unchanged. The Structure File (v16)
  documents only 0-8, so the names of 9-20 still need a live check.

- Entity names no longer repeat their device's name: an output called
  "home cinema led achter" on the controller "Home Cinema" is now shown as
  "Home Cinema led achter" instead of "Home Cinema home cinema led
  achter". Entity ids of existing entities do not change.

- The documentation no longer claims support that was only ever tested
  against the synthetic test fixture. Checked against the Loxone structure
  file (v16): a `PresenceDetector` publishes no brightness or noise state,
  so the `Illuminance` and `Noise` sub-sensors added in 0.10.0 (#461) most
  likely never appear on a real Miniserver; the README now points to
  ticking "Use" on the detector's outputs, which turns them into
  `InfoOnlyAnalog` sensors. An `AudioZoneV2` documents no `active`, `mute`,
  `source`, `sourceList`, `favouriteList` or `metadata` state and no on/off,
  mute or source command, so power, mute, source selection and track
  metadata are described as unverified instead of supported. The outputs
  of an intercom are pushbuttons in the structure file, while the
  integration exposes them as switches that send `on` and hold the output
  until turned off; the README warns about door openers until that is
  changed. The code paths stay (they are guarded and do nothing when the
  states are missing) and are marked for a live check in
  `docs/review/LIVE-MINISERVER-CHECKS.md`.

### Security

- The Loxone token no longer reaches the Home Assistant event bus. The
  Miniserver's replies to the token requests (`getjwt`, `gettoken`,
  `refreshjwt`), which carry the raw token, and to the key and salt
  requests (`getkey2`, `getkey`, `getvisusalt`) were handed to the message
  callback like any command reply, so the token was fired as a
  `loxone_event` and written to the debug log. Those replies now stay
  inside the connection.

- `killtoken` is sent encrypted. It carries the raw token, which crossed
  the network in clear text on the unencrypted `ws://` socket of a Gen 1
  Miniserver (ported from upstream; the encrypted form is not yet verified
  live).

- Encrypted commands are logged by their command path only, so the debug
  log no longer contains the token, the credential and token hashes or
  the secured-command HMACs. Plain control commands stay readable.

- Each secured command (alarm, gate, anything behind the visualisation
  password) now waits for its own `getvisusalt` reply. The first reply used
  to send every queued secured command, and a failed reply was only
  logged, so a failed "open gate" could run together with a later
  "disarm". A failed reply now drops its command, and a command older than
  30 seconds is dropped instead of being sent late (ported from upstream).

### Fixed

- The Radio select read every non-empty lock state as locked, so the
  Miniserver's "not locked" JSON (`{"locked": 0}`) refused every
  selection.

- A temperature format with a bare degree sign (`%.1f°`, `%d°`, which is
  what Loxone Config itself writes) now reads as a temperature in the
  Miniserver's own unit (`msInfo.tempUnit`, °C or °F); before, such a
  sensor had no device class and a room controller assumed °C. Formats
  that use Loxone's `<v.1>` value placeholders no longer end up as the
  unit, and volts and amperes get the voltage and current device classes.
  The format facts come from a reverse-engineering pass over
  `LoxoneConfig.exe`.

- One malformed message no longer ends the session. A text frame that is
  valid JSON but not an object (`12345678`, `null`, `{"LL": []}`) raised an
  error the parser did not catch, and the session stayed down until a
  manual reload. Found by running upstream's 0.10.1 parsing tests against
  this fork.

- A session that ends with an error the reconnect loop does not retry (an
  HTTP 500 or a truncated `LoxAPP3.json` while reconnecting, or a bug)
  reloads the entry after 30 seconds instead of leaving every entity
  unavailable (ported from upstream).

- The token persisted since 0.10.3 now survives a reload. Every unload
  killed it on the Miniserver, and an unload runs for every reload
  (options change, reauth), so the next setup fell back to a password
  login. The token is no longer killed on unload; when the entry is
  removed, it expires on the Miniserver.

- The websocket is opened right before the key exchange instead of during
  setup (#527). The Miniserver closes a socket that is not authenticated
  within a few seconds, and Home Assistant sets up every platform in
  between, so on a slow start the key exchange went to a closed socket:
  an ERROR with traceback and entities that came up late.

- A user the Miniserver has blocked after too many failed logins (close
  code 4003) or disabled (4006) is no longer reconnected forever, which
  can extend the block; the integration starts the reauth flow instead
  (ported from upstream).

- An outage produces one WARNING instead of about four ERROR tracebacks
  per reconnect attempt. "Restored connection" is logged once the
  Miniserver has accepted the session, not when the socket opens.

- A half-open socket (pulled cable, frozen Miniserver) is detected after
  90 seconds without data instead of at the TCP timeout, minutes later.

- Commands given while the Miniserver was not connected are dropped with
  one WARNING when the next session starts, instead of being sent before
  authentication (and a gate pressed minutes ago opening now). Queued
  commands are no longer pushed into a dead socket at shutdown.

- An encrypted send to a closed socket no longer rotates the salt.

- Diagnostics include the session state and the date the token expires
  (never the token).

- Setting a single target temperature on an automatic `IRoomControllerV2`
  sent `setComfortModeTemp` the difference to the comfort temperature,
  but the Structure File defines it as the new absolute comfort
  temperature, so asking for 22 °C in a 21 °C room asked the Miniserver
  for 1 °C. It now sends the temperature itself, and no longer waits for
  the comfort temperature state to arrive first (from upstream PR #530).

- The legacy `IRoomController` follows the Structure File's mode table
  (from upstream PR #530): modes 0-2 read as auto, 3 and 5 as heat, 4 and
  6 as cool, and the "heating and cooling off" service mode as off. The
  old table was a guess that read mode 1 as heat and sent `setMode/4` —
  automatic cooling — for off. Off now sends `service/1`, auto, heat and
  cool send `mode/0`, `mode/3` and `mode/4` (leaving the off service mode
  first), target temperatures use `settemp` with the cooling index in the
  cooling modes, and `heat_cool` is no longer offered because the
  controller has no such mode; `restrictedToMode` hides heat or cool. The
  command syntax still needs a check on real V1 hardware.

- Jalousies read their sun-automation state from `autoActive` and the info
  text from `infoText`, the names the Structure File uses (from upstream
  PR #530). Only the non-standard `autoState` / `autoInfoText` were read
  before, so on a Miniserver that sends the spec names every blind showed
  its automation as off and got no `Sun auto` select. The old names are
  still read when the spec ones are missing.

- Two things that broke on the Home Assistant 2026.11 development branch
  are fixed in advance: setting up an entry failed because the group
  integration no longer re-exports `async_get_component`, and
  `loxone.sync_areas` raised for every entity that is its device's main
  entity (no name of its own), which 2026.11 no longer allows an area of
  its own. `sync_areas` now gives such an entity's device the area
  instead, and still never overwrites an area the user set.
## 0.10.9 - 2026-09-16

### Fixed

- The `via_device_id` link introduced in 0.10.8 did not take effect on
  Home Assistant 2026.9: the capability probe looked for a module-level
  key table that 2026.9 removed, so it silently fell back to the
  deprecated tuple and the start-up warning stayed. The probe now
  inspects the `DeviceInfo` type itself. Two more things the 2026.9 test
  run exposed: entities on the Miniserver device itself (keep-alive,
  version, traffic sensors) were linked to their own device, which the
  tuple form ignored and the id form rejects, so they now carry no link;
  and the dimmer and switch sub-controls of a `LightControllerV2` passed a
  stray positional `True` as the link override, which became a bogus
  `via_device` value. Verified on 2026.8.1 and 2026.9.1; the setup test
  now fails if the deprecation is logged, and the probe test is pinned to
  the Home Assistant version instead of the probe's own implementation.

## 0.10.8 - 2026-09-16

### Fixed

- Child devices are linked to the Miniserver device with `via_device_id`
  on Home Assistant 2026.8 and newer. Home Assistant 2026.9 logged a
  deprecation for the old `via_device` link once per device at every
  start, and the old form stops working in 2027.8. On the 2026.7 floor the
  old form is still used, because the new key is rejected there.

### Changed

- `manifest.json` points `issue_tracker`, `documentation` and `codeowners`
  at this repository, so Home Assistant's own "report a bug" links no
  longer send users of the fork to upstream.
- The code parses on Python 3.13 again. The 0.10.0 rewrite of
  `except A, B:` to the parenthesised form had been undone by the
  formatter: with `target-version = "py314"`, ruff strips those
  parentheses on every run. Ruff now targets py313, which keeps them and
  flags the 3.14-only form, and the two modules that relied on deferred
  annotation evaluation gained `from __future__ import annotations`. The
  supported floor is unchanged (Home Assistant 2026.7 needs 3.14).
- The stale bot marks issues stale after 180 days instead of 30.
- The incident report in `docs/incidents/` no longer contains the
  Miniserver serial and LAN address.

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

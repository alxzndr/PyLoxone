# Live-Miniserver checks required before proposing this upstream

Everything in the 2026-09 remediation work is covered by 689 automated tests, but a
test can only prove that the code does what we *believe* the Loxone protocol wants.
The items below are the places where that belief is an assumption. Each was
deliberately implemented behind a named helper so the assumption sits in one
readable place and can be flipped in one line if a real Miniserver disagrees.

Run these against a real Miniserver before opening any PR against
`JoDehli/PyLoxone`. Nothing here blocks using the fork yourself.

**How to use this document:** work top-down. Anything in the "Blocking" section can
send a wrong command to real hardware, so test those before trusting the integration
with blinds, alarms or ventilation. The rest are correctness-of-detail issues.

Enable debug logging first:

```yaml
logger:
  default: warning
  logs:
    custom_components.loxone: debug
    custom_components.loxone.pyloxone_api: debug
```

---

## Blocking — these send commands to hardware

### 1. Alarm arm-home / arm-away may be inverted (PC-31)

- **Helper:** `alarm_arm_value` in `custom_components/loxone/alarm_control_panel.py`
- **The doubt:** the write path sends `delayedon/0` for arm-home and `delayedon/1`
  for arm-away, but the read path maps "armed **and** movement detection disabled"
  to `ARMED_HOME`. Those two cannot both be right. Loxone's parameter is the
  *disable-movement* flag, which suggests the write path is backwards.
- **Test:** call `alarm_control_panel.alarm_arm_home` on a real Alarm block. Then
  read the entity state.
- **Pass:** the entity reports `armed_home`, and the Loxone app agrees that
  movement detection is off.
- **Fail:** it reports `armed_away`, or the app shows movement detection still
  active. Then swap the two values in `alarm_arm_value`.
- **Why it matters:** arming the wrong mode is a security consequence, not a
  cosmetic one.

### 2. Ventilation mode command name (PC-09)

- **Helper:** `ventilation_set_mode_command` in `custom_components/loxone/fan.py`
- **The doubt:** `set_preset_mode` was an empty no-op before this work, so there
  was no prior art to copy. The implementation sends `setMode/<id>`; the
  subcommand name is inferred, not documented in anything we hold.
- **Test:** select each preset (Low, Medium, High, Auto, Away) on a Ventilation
  block and watch the debug log for the outgoing command.
- **Pass:** the Miniserver acts on it and the fan's reported mode follows.
- **Fail:** the command is silently ignored. Capture what the Loxone app sends for
  the same action (browser devtools against the web UI) and correct the helper.

### 3. Ventilation speed uses a one-hour timer (PC-29)

- **Helper:** `ventilation_set_timer_command` / `ventilation_profile_id` in `fan.py`
- **The doubt:** `fan.set_percentage` sends `setTimer/3600/<pct>/<mode>/-1`. A
  one-hour timed override is a surprising implementation of "set the speed", and
  it is what the original code did. There may be a plain non-timed speed command.
- **Test:** set a fan percentage, then check in the Loxone app whether the change
  is permanent or expires after an hour.
- **Pass / Fail:** if it expires, find the non-timed command and use it.

### 4. Legacy `IRoomController` mode table (PC-26)

- **Helper:** the legacy mode map in `custom_components/loxone/climate.py`
- **The doubt:** the legacy V1 table says `4 = Off`, while the V2 enum in the same
  file says `4 = MANUAL_HEAT`. Both cannot be right. If V2 is correct, then
  `set_hvac_mode(OFF)` on a V1 controller currently turns on **manual heating**.
- **Test:** only applicable if you have a legacy (non-V2) `IRoomController`. Set
  HVAC mode to `off` and confirm in the Loxone app that the room controller is off
  rather than heating.
- **Note:** if you have no V1 controller, say so — the safest resolution is then to
  make the V1 path refuse unknown modes loudly instead of guessing.

### 5. Audio zone on-off commands (WP-6.7, PC-43)

- **Helper:** `audio_zone_power_command` in `custom_components/loxone/media_player.py`
- **The doubt:** `turn_on` / `turn_off` on an AudioZoneV2 send `on` / `off`
  (mirroring the power sub-command naming seen in other LoxApp
  integrations), and the zone reads `off` when its `active` stream
  reports 0. Neither the command words nor the `active`-stream
  semantics are documented anywhere we hold.
- **Test:** call `media_player.turn_on` / `media_player.turn_off` on a
  zone and watch the debug log for the outgoing command.
- **Pass:** the Loxone app agrees the zone is on/off; the entity reads
  `off` while powered off.
- **Fail:** the app does not change the zone. Capture what the Loxone
  app sends for the same action and correct the helper.

### 6. Audio zone mute / unmute commands (WP-6.7, PC-43)

- **Helper:** `audio_zone_mute_command` in `custom_components/loxone/media_player.py`
- **The doubt:** `volume_mute` sends `mute` / `unmute`, and the
  `mute` stream is read as a 0/1 mute flag via
  `audio_zone_two_state`.
- **Test:** call `media_player.volume_mute` with and without
  `is_volume_muted` on a zone; mute/unmute the same zone in the
  Loxone app and watch the `mute` stream.
- **Pass:** the wire value changes exactly when the app mutes;
  the entity's mute flag follows both directions.
- **Fail:** the app ignores the command or the stream encodes mute
  differently (e.g. `1` means *un*muted). Correct the helper / the
  two-state mapping.

### 7. Audio zone source selection and stream shapes (WP-6.7, PC-43)

- **Helpers:** `audio_zone_source_command`, `audio_zone_stream_names_list`,
  `audio_zone_metadata` in `custom_components/loxone/media_player.py`
- **The doubt:** `select_source` sends `source/<name>`; the
  `sourceList` / `favouriteList` streams are assumed to push JSON name
  lists; the `metadata` stream is assumed to be a JSON object with
  `title` / `artist` / `album`.
- **Test:** play something from two different sources; switch the
  source in the Loxone app and in HA. Watch the four
  streams (`source`, `sourceList`, `favouriteList`, `metadata`) in
  the debug log for their real payloads.
- **Pass:** the command selects the source and `source` / the media
  attributes follow.
- **Fail:** the command is ignored, or the streams carry a different
  shape (e.g. `sourceList` is a dict, `metadata` is absent). Adjust the
  helpers to the observed wiring (drop the `metadata` stream entirely if
  the server does not push it).

---

## Non-blocking — correctness of detail

### 8. Username percent-encoding: UTF-8 vs latin-1 (API-12)

- **Helper:** `percent_encode_credential` in `pyloxone_api/connection.py`
- **The doubt:** usernames are now percent-encoded in protocol commands. We encode
  UTF-8 first. The Miniserver may expect latin-1 percent-encoding.
- **Test:** log in with a username containing a non-ASCII character (e.g. `bjørn`)
  or a `/`, `+` or space.
- **Pass:** authentication succeeds. **Fail:** flip the encoding in that one helper.
- **Related:** upstream #506, where the HTTP side had the same class of bug.

### 9. Strict header-to-body frame sequencing (API-18)

- **Where:** `_do_start_listening` in `pyloxone_api/connection.py`
- **The doubt:** framing no longer guesses from message length; it reads a header
  then reads the body to `payload_length`. This assumes the Miniserver always sends
  a header frame followed by body frames, including for `TEXT_STATES`.
- **Test:** run for a few hours across a busy period. Watch for
  `Message not handled` or framing errors in the debug log.
- **Pass:** none appear and states keep updating.

### 10. Library-level ping disabled (API-06)

- **Where:** `websocket_options` in `pyloxone_api/connection.py`, `ping_interval=None`
- **The doubt:** we now rely solely on Loxone's own 30s keepalive. If the server or
  an intermediary drops idle sockets, connections may die quietly.
- **Test:** leave it connected overnight, ideally through a period of no activity.
- **Pass:** no unexplained reconnects. **Fail:** set `ping_interval` to something
  above 30s rather than re-enabling the 20s default.

### 11. Cloud DNS redirect keeps the scheme (API-11)

- **Test:** only if you use `https://dns.loxonecloud.com/<serial>`. Configure it and
  confirm setup completes and the websocket connects.
- **Pass:** connects. The bug was `wss://` being used against a plain-HTTP endpoint.

### 12. `killtoken` form for JWT tokens (API-17)

- **The doubt:** the token is now killed on entry removal using the legacy 32-char
  form. Whether it also cancels SHA256 JSON-web tokens is unconfirmed.
- **Test:** remove the integration, then check the Miniserver's token list.
- **Pass:** the token is gone.

### 13. Colour picker type mapping and default Kelvin (WP-4.3)

- **The doubt:** `PICKER_TYPE_TO_CLASS` maps integer `pickerType` values
  (`0 = RGB`, and so on), and `DEFAULT_TURN_ON_KELVIN = 4000` is a chosen default
  for turning on a tunable-white light with no prior colour.
- **Test:** with an RGB and a tunable-white picker, confirm each is created as the
  right entity type and that turning on from cold produces a sensible white.

### 14. Media player stop uses `pause` (PC-43)

- **The doubt:** `MediaPlayerEntityFeature.STOP` is implemented by sending `pause`,
  because no distinct stop command is known for AudioZoneV2.
- **Test:** press stop on a media player entity. **Pass:** playback stops.

### 15. Config flow connection test and reauth (WP-3.4)

- **Test:** add the integration with a deliberately wrong password. Then add it
  with the right one. Then change the password on the Miniserver and confirm Home
  Assistant raises a reauth prompt rather than failing silently.
- **Pass:** wrong password is rejected *in the form*, not after the entry is
  created; reauth appears and succeeds.
- **Also:** adding the same Miniserver twice should abort as already configured.

### 16. `InfoOnlyDigital` device-class inference table (WP-6.4, #402)

- **Helper:** `infer_digital_device_class` in `custom_components/loxone/binary_sensor.py`
- **The doubt:** the keyword tables are a guess at the labels and category
  names LoxConfig authors actually use. The expected outcomes are pinned in
  `tests/test_digital_device_class.py` with hand-derived literals, but the
  coverage of the tables themselves is an assumption.
- **Test:** on a real Miniserver, inspect a few `InfoOnlyDigital` controls
  (their `details.text` and category name) and check the `device_class`
  their binary sensors end up with.
- **Pass:** the class matches what a human would expect for the control, or
  it stays classless where nothing can reasonably be inferred.
- **Fail / adjust:** a labelled control ended up classless, or was classed
  wrongly — add or correct the phrase/keyword in the two tables (one place
  each).

### 17. `Tracker` entries payload shape (WP-6.6, PS-26)

- **Helper:** `tracker_entries` in `custom_components/loxone/sensor.py`
- **The doubt:** the `entries` stream of a Tracker control is assumed to push a
  JSON array of names/ids of strings (e.g. the sensor names an
  `Alarm`'s `sensors` tracker holds), delivered as a JSON string or an
  already-parsed list. If the Miniserver instead delivers object
  dictionaries, numeric codes, or another shape, `tracker_entries`
  discards the non-scalar payloads and the sensor keeps showing the last
  (possibly empty) list.
- **Test:** on a real Miniserver, enable debug logging and watch a
  `Tracker` control (for example the `sensors` tracker a
  `Alarm` exposes) while its tracked items change; compare the raw
  `entries` message with the sensor's `entries` attribute.
- **Pass:** the sensor's comma-joined state and `entries`/`count`
  attributes match the raw payload for the same list.
- **Fail / adjust:** the payload is another shape (objects with names,
  device uuids, …) — parse it in `tracker_entries` (one place) so the
  sensor reads the intended value.

### 18. `UpDownDigital` rocker on-press semantics (WP-6.6, PS-26)

- **Helper:** `LoxoneUpDownDigitalButton.async_press` in
  `custom_components/loxone/button.py` (sends `UpOn` / `DownOn`)
- **The doubt:** the control has no state streams, so pressing an HA
  button only emits the `<side>On` command. The openHAB binding models
  the same control as *toggle switches* (`On` to latch, another `On` to
  unlatch), because the rocker side latches. If the intended wiring of
  the virtual up/down inputs wants a momentary pulse instead of a
  latch, a single press will leave the corresponding side stuck on.
- **Test:** with a real `UpDownDigital` control driving something
  observable (a scene output is fine), press the `Up` button once and
  watch the target; decide whether the LoxConfig expects a latch (the
  second press must then send `UpOff`, so the entity grows a state) or
  the current momentary behaviour is acceptable.
- **Pass:** the direction the LoxConfig author wired moves for the
  expected amount of time and returns to rest, or the latch can be
  released from HA.
- **Fail / adjust:** the side stays latched with no HA-visible way to
  release it — add a state to `LoxoneUpDownDigitalButton` (one place)
  and send the matching `<side>Off` on toggle-off.

### 19. Meter-family register set (WP-6.5, PS-26)

- **Helper:** `METER_STATE_CLASSES` / `METER_FORMAT_KEYS` /
  `meter_sub_sensor_kwargs` in `custom_components/loxone/sensor.py`
- **The doubt:** the generalised loop assumes `EnergyManager`,
  `EnergyManager2`, `PowerUnit` and `Wallbox` advertise the same
  register states as the legacy `Meter` (`actual`, `total`, `totalNeg`,
  `storage`, with `actualFormat`/`totalFormat`/`storageFormat`).  The
  2026-09 catalogue says so ("Meter sub-state loop generalises"), but
  no structure file from a live Miniserver with these controls was held
  at the time of writing.
- **Test:** with debug logging on, find the structure-file dump of a
  Miniserver that has any of the four controls and compare its
  `states`/`details` against the `Meter` table above; watch how many
  sub-entities appear.
- **Pass:** registers appear as "Actual" / "Total" / "Total Neg" /
  "Level" with the right units and classes.
- **Fail / adjust:** if a real control uses different register names or
  format keys, extend the three tables in `sensor.py` (one place each);
  a control with no matching states simply yields no sub-registers today, so the
  failure mode is missing data, never a crash.

### 17. `IntercomV2` block shape and sub-control command names (WP-6.3, #466)

- **Helper:** `intercom_sub_control_kwargs` / `INTERCOM_TYPES` in
  `custom_components/loxone/switch.py` (the fixture entry
  "Entrance Intercom" in `tests/fixtures/LoxAPP3.json` encodes the
  assumed shape)
- **The doubt:** no structure file from a Miniserver that reports
  `IntercomV2` was held at the time of writing. The block is treated
  as wire-compatible with the legacy `Intercom`: same
  `subControls` fan-out, each sub-control needing one `active`
  state stream to report state, master binds on the control's own
  `uuidAction`. Turn-on sends `on` on the sub-control's action
  uuid, turn-off sends `Off` (inherited from `LoxoneSwitch`) —
  note the case mismatch with the lowercase `on`, which
  pre-dates this WP and applies to legacy Intercoms too.
- **Test:** with debug logging on, find an `IntercomV2` control in
  the structure file and compare its `states`/`subControls` against
  the fixture entry; then turn one sub-control on and off from HA
  and watch the outgoing commands in the debug log.
- **Pass:** the sub-controls appear the way the Loxone app sees them,
  the switch reflects the sub-control's real state after each feed,
  and both operands are executed on the Miniserver without a
  `Message not handled`.
- **Fail / adjust:** a V2 sub-control has a different state set
  (no `active`) or different command names — adjust the fan-out in
  `intercom_sub_control_kwargs` (one place) and, if the off
  command is case-sensitive, give `LoxoneIntercomSubControl` an
  explicit `turn_off` there (one place).

### 20. LoxLIVE broadcast discovery in the setup form (WP-6.9, CORE-19)

- **Helpers:** `_discover_miniserver` / `_discovered_prefill` in
  `custom_components/loxone/config_flow.py` and
  `parse_discovery_response` in
  `custom_components/loxone/pyloxone_api/discover.py` (the wire regex
  lives there, unchanged from the vendored original).
- **The doubts:** (1) whether the reply of a *current* Miniserver still
  matches the vendored format `^LoxLIVE:<version> <ip>:<port> (Mac ...)`,
  incl. the space after the port; (2) whether the UDP 7070 broadcast
  reaches the Miniserver from the HA host in its typical deployments
  (Docker bridge / macvlan, VLAN); (3) whether the port in the reply
  header is always the actual HTTP service port (i.e. valid as the form
  port prefill).
- **Test:** with Home Assistant on the same LAN, open
  Settings → Devices & Services → Add Device → Loxone.  Watch the host
  and port fields *before* typing anything, and confirm setup with the
  prefilled values.  Then run the same with two Miniservers on the LAN
  (or a Wi-Fi isolation profile) and verify the form still completes with
  a manually corrected address.
- **Pass:** the real address and port are prefilled, the entry is created
  and loads; on a broadcast-free LAN the fields are simply blank within
  ~2 s and setup works by typing.
- **Fail/adjust:** no prefill despite peer connectivity → packet-capture
  the 7070/7071 exchange from the HA host; if the reply format differs,
  fix **`parse_discovery_response`** (one place); if UDP is blocked, that
  is a deployment property — the probe must stay best-effort and silent.
  If the header port turns out to be the *discovery* port rather than the
  HTTP port, stop taking the port from the reply (prefill host only).

---

## Regression sweep

Worth confirming the original reported bugs are actually gone, since these are what
users will notice:

| Upstream | Symptom to confirm fixed |
|---|---|
| #475 | after a Miniserver restart, entities go `unavailable` then return to their true state, never flicking through a wrong one |
| #491 | with two Miniservers, commands reach only the intended one, and reloading one leaves the other's entities alone |
| #514 | an expected token expiry produces no ERROR-level traceback |
| #486, #457, #487 | connection survives a Miniserver reboot without a Home Assistant restart |
| #413 | a secured alarm shows a numeric PIN pad, not a text field |
| #501 | a Window without `targetPosition` sets up and reports position |
| #512 | an RGB light can be turned on with brightness in one call |
| #402 | sensors carry sensible device classes |
| #466 | an intercom control of type `IntercomV2` creates its sub-control switches (e.g. a door lock) the way a legacy `Intercom` does |

A practical way to exercise #475 and #486 together: reboot the Miniserver from the
Loxone app and watch Home Assistant recover on its own.

---

## Confirmed against live hardware (2026-09-10)

Run against a Gen 1 Miniserver, firmware 17.2.8.28, 35 controls. These are settled and
need no further testing.

| Check | Result |
|---|---|
| Connection, auth, structure download, subscribe | works; 407 state uuids, 494 updates in 45s |
| Log noise on a normal session (#514) | **no WARNING or ERROR records** |
| Meter registers (PS-21) | 7 meters, all `actual`→power/measurement, `total`→energy/total_increasing |
| Meter format details (PS-08) | all present; the crash path cannot trigger on this structure |
| `softwareVersion` shape (CORE-16) | list `[17,2,8,28]`, joined correctly |
| Dimmer min/max brightness (PC-17) | **fixed and proven**: HA 128 → Loxone 75 on a `min=50,max=100` circuit, read back independently. The old path sent 50 (the floor), and HA 1 mapped to 0 = off |
| Jalousie position inversion | Loxone `position: 1.0` → HA position 0 = closed, `_closed = True` |

Items 1-4 (alarm arming, ventilation mode and timer, legacy `IRoomController`) could **not**
be tested: that installation has no Alarm, Ventilation or legacy room-controller block.
They remain open, and the suspected alarm inversion is still unconfirmed.

Also observed on that installation: `except A, B:` (unparenthesised, PEP 758) is used in
20 places across 12 files. It is valid only on Python 3.14+, which the supported Home
Assistant floor already requires, so it is not a live defect — but it does pin the
minimum interpreter silently, and `ruff` will not flag it while `target-version = "py314"`.

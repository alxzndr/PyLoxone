# Live-Miniserver checks required before proposing this upstream

Everything in the 2026-09 remediation work is covered by 530 automated tests, but a
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

---

## Non-blocking — correctness of detail

### 5. Username percent-encoding: UTF-8 vs latin-1 (API-12)

- **Helper:** `percent_encode_credential` in `pyloxone_api/connection.py`
- **The doubt:** usernames are now percent-encoded in protocol commands. We encode
  UTF-8 first. The Miniserver may expect latin-1 percent-encoding.
- **Test:** log in with a username containing a non-ASCII character (e.g. `bjørn`)
  or a `/`, `+` or space.
- **Pass:** authentication succeeds. **Fail:** flip the encoding in that one helper.
- **Related:** upstream #506, where the HTTP side had the same class of bug.

### 6. Strict header-to-body frame sequencing (API-18)

- **Where:** `_do_start_listening` in `pyloxone_api/connection.py`
- **The doubt:** framing no longer guesses from message length; it reads a header
  then reads the body to `payload_length`. This assumes the Miniserver always sends
  a header frame followed by body frames, including for `TEXT_STATES`.
- **Test:** run for a few hours across a busy period. Watch for
  `Message not handled` or framing errors in the debug log.
- **Pass:** none appear and states keep updating.

### 7. Library-level ping disabled (API-06)

- **Where:** `websocket_options` in `pyloxone_api/connection.py`, `ping_interval=None`
- **The doubt:** we now rely solely on Loxone's own 30s keepalive. If the server or
  an intermediary drops idle sockets, connections may die quietly.
- **Test:** leave it connected overnight, ideally through a period of no activity.
- **Pass:** no unexplained reconnects. **Fail:** set `ping_interval` to something
  above 30s rather than re-enabling the 20s default.

### 8. Cloud DNS redirect keeps the scheme (API-11)

- **Test:** only if you use `https://dns.loxonecloud.com/<serial>`. Configure it and
  confirm setup completes and the websocket connects.
- **Pass:** connects. The bug was `wss://` being used against a plain-HTTP endpoint.

### 9. `killtoken` form for JWT tokens (API-17)

- **The doubt:** the token is now killed on entry removal using the legacy 32-char
  form. Whether it also cancels SHA256 JSON-web tokens is unconfirmed.
- **Test:** remove the integration, then check the Miniserver's token list.
- **Pass:** the token is gone.

### 10. Colour picker type mapping and default Kelvin (WP-4.3)

- **The doubt:** `PICKER_TYPE_TO_CLASS` maps integer `pickerType` values
  (`0 = RGB`, and so on), and `DEFAULT_TURN_ON_KELVIN = 4000` is a chosen default
  for turning on a tunable-white light with no prior colour.
- **Test:** with an RGB and a tunable-white picker, confirm each is created as the
  right entity type and that turning on from cold produces a sensible white.

### 11. Media player stop uses `pause` (PC-43)

- **The doubt:** `MediaPlayerEntityFeature.STOP` is implemented by sending `pause`,
  because no distinct stop command is known for AudioZoneV2.
- **Test:** press stop on a media player entity. **Pass:** playback stops.

### 12. Config flow connection test and reauth (WP-3.4)

- **Test:** add the integration with a deliberately wrong password. Then add it
  with the right one. Then change the password on the Miniserver and confirm Home
  Assistant raises a reauth prompt rather than failing silently.
- **Pass:** wrong password is rejected *in the form*, not after the entry is
  created; reauth appears and succeeds.
- **Also:** adding the same Miniserver twice should abort as already configured.

### 13. `InfoOnlyDigital` device-class inference table (WP-6.4, #402)

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

A practical way to exercise #475 and #486 together: reboot the Miniserver from the
Loxone app and watch Home Assistant recover on its own.

# PyLoxone

[![Latest release](https://img.shields.io/github/v/release/alxzndr/PyLoxone?label=release)](https://github.com/alxzndr/PyLoxone/releases/latest)
[![CI](https://img.shields.io/github/actions/workflow/status/alxzndr/PyLoxone/ci.yaml?branch=master&label=CI)](https://github.com/alxzndr/PyLoxone/actions/workflows/ci.yaml)
[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)

Home Assistant integration for Loxone Miniservers. It connects to the
Miniserver's websocket, mirrors every supported control as a Home Assistant
entity, and receives state changes as the Miniserver pushes them (no polling).

This is a maintained fork of [JoDehli/PyLoxone](https://github.com/JoDehli/PyLoxone),
the original integration by Jörg Dehli. It started from upstream 0.9.23 with a
full code review and fixes the reconnect, multi-Miniserver, security and
platform issues found there, and adds support for more Loxone control types.
[`docs/fork-vs-upstream.md`](docs/fork-vs-upstream.md) lists the differences
and the one migration you cannot undo. If you want to support the original
author: [buy JoDehli a coffee](https://www.buymeacoffee.com/JoDehli).
Thanks also to Pawel Pieczul of openHAB for the token authentication.

**Contents:**
[Requirements](#requirements) ·
[Installation](#installation) ·
[Setting up a Miniserver](#setting-up-a-miniserver) ·
[Configuration options](#configuration-options) ·
[What you get](#what-you-get) ·
[Events](#events) ·
[Services](#services) ·
[Repairs and diagnostics](#repairs-diagnostics-and-system-health) ·
[Logging](#log-configuration) ·
[Recorder](#recorder-configuration) ·
[Troubleshooting](#troubleshooting) ·
[Recipes](#recipes) ·
[Contributing](#contributing)

## Requirements

- **Home Assistant 2026.7.0 or newer.** This is the floor declared to HACS in
  `hacs.json`; older releases lack units the sensor platform uses.
  Home Assistant 2026.x itself runs on CPython 3.14.2 or newer, which is
  also the floor for the development environment (see
  [CONTRIBUTING.md](CONTRIBUTING.md)).
- **Miniserver firmware 7.0.0 or newer** (the JSON websocket API). Gen 1
  and Gen 2 Miniservers are supported; the integration raises a repair
  issue on older firmware.
- A Miniserver user. A dedicated user with the permissions your
  automations need is better than `admin`.

## Installation

### HACS (recommended)

1. Install [HACS](https://hacs.xyz/docs/use/download/download/).
2. HACS > Integrations > three-dot menu > *Custom repositories*: add
   `https://github.com/alxzndr/PyLoxone` with category *Integration*.
3. Download PyLoxone and restart Home Assistant.
4. Settings > Devices & Services > *Add integration* > **PyLoxone**.

### Manual

1. Download the [latest release](https://github.com/alxzndr/PyLoxone/releases/latest)
   and extract it.
2. Copy `custom_components/loxone` into the `custom_components` folder next
   to your `configuration.yaml`.
3. Restart Home Assistant and add the integration as above.

### Upgrading from JoDehli/PyLoxone

Installing this fork over the upstream integration keeps your entities, but
it migrates the config entry to a newer version that upstream cannot load.
**Take a full backup first**; restoring it is the only way back. Details in
[`docs/fork-vs-upstream.md`](docs/fork-vs-upstream.md#the-one-way-door-read-this-first).

## Setting up a Miniserver

Everything is configured through the UI; there is no YAML configuration for
the connection. Opening the setup form sends one LoxLIVE broadcast on the
LAN and prefills the address and port of a Miniserver that answers; a
Miniserver that does not answer just leaves the fields blank.

| Field | Default | Meaning |
|---|---|---|
| `host` | (probe result) | IP address or hostname of the Miniserver. For a cloud connection use `dns.loxonecloud.com/<serial>` (see below). |
| `port` | `8080` | The Miniserver's HTTP port. Many Miniservers listen on `80` locally; `8443` is the local HTTPS port and `443` the Loxone Cloud port. |
| `username` | | Loxone user. |
| `password` | | Password of that user. It is never sent in plain text: the integration authenticates with a token. |
| Verify TLS certificate | on | `verify_ssl`, see [Configuration options](#configuration-options). Only affects HTTPS connections. |

The form only submits once the Miniserver has accepted the credentials, and
the same Miniserver (by serial number) cannot be added twice. Usernames and
passwords must be representable in Latin-1; the form says so when they are
not.

### Local or cloud connection

Connect locally whenever you can; it is faster and more reliable. Use the
Loxone Cloud address only when local access is impossible:

- `host`: `dns.loxonecloud.com/123456789ABC` (replace with your Miniserver
  serial number)
- `port`: `443`

### Changing the connection later

When the Miniserver rejects the stored credentials, the entry starts a
re-authentication flow (a repair issue appears, and the entry shows that it
needs attention under Settings > Devices & Services) where you can enter
host, port, username and password again. To change the address of a Miniserver that is
simply unreachable, remove the entry and add it again: entity ids derive from
the Loxone UUIDs and are stable, so automations keep working, but entity
customisations and area assignments are lost. A backup covers that.

### Several Miniservers

Add one entry per Miniserver. Entries are isolated from each other: state
and commands never cross over, and the `loxone_event` bus event carries the
`entry_id` of the Miniserver that produced it.

## Configuration options

The TLS verification setting is asked for when adding or re-authenticating a
Miniserver; the other options live on the entry's options page (Settings >
Devices & Services > PyLoxone > *Configure*). Saving the options reloads the
entry.

| Option | Default | Effect |
|---|---|---|
| `verify_ssl` | `true` | Verify the Miniserver's TLS certificate on the HTTPS paths (local HTTPS and the Loxone Cloud address). **Keep it on**: the same connection carries your credentials and every state update, so with verification off a man-in-the-middle can read and rewrite both. Only turn it off for a self-signed certificate you trust on a network you control. The plain-HTTP local path is not affected. |
| `generate_scenes` | `true` | Create one `scene` entity per mood of every `LightControllerV2`, as soon as the Miniserver streams the controller's mood list. Off skips scene generation entirely. |
| `generate_scenes_delay` | `3` (minimum `3`) | Legacy setting kept for existing installs. Older versions waited this many seconds after setup before generating the `LightControllerV2` scenes, because the light platform had to be fully loaded first and 3 seconds was the smallest value that reliably worked on a slow install. Scenes are now generated when the mood list arrives, so the value no longer delays anything; the floor stays so migrated entries remain valid. |
| `generate_lightcontroller_subcontrols` | `false` for new installs, `true` for installs that predate the option | Whether the sub-control entities of a `LightControllerV2` (its switches, dimmers and colour pickers) start **enabled**. They are always registered; with the option off they start disabled and can be enabled per entity later (see [Enabling LightControllerV2 sub-controls](#enabling-lightcontrollerv2-sub-controls)). |
| `generate_groups` | `false` for new installs; unset on installs that predate the option, which keeps their groups on | Create the Loxone auto-groups: `group.loxone_group` plus one subgroup per control family (analog sensors, digital sensors, switches, buttons, covers, lights, dimmers, climates, ventilations, AC controllers, numbers, texts). |

## What you get

### Devices and areas

- The **Miniserver** is a device of its own, with diagnostic sensors for the
  software version, the last keep-alive message (disabled by default), the
  inbound and outbound websocket traffic (messages per minute), and the
  Miniserver's global notification text when the structure file has one.
- Every Loxone control becomes a device; a control with several entities
  (a room controller and its diagnostic sensors, a meter and its registers)
  keeps them on one device.
- The Loxone room of a control is suggested as the device's area when the
  device is created. To apply rooms to existing entities, call
  `loxone.sync_areas` (see [Services](#services)).

### Entities

Every supported control type and what it turns into:

| Loxone control | HA domain | What you get |
|---|---|---|
| `Alarm` | `alarm_control_panel` | Arm home, away, night, vacation and disarm. Night uses the same Loxone command as home (interior motion suppressed), vacation the same as away. A secured alarm asks for a numeric code when arming. Attributes: `level`, `armed_at`, `next_level_at`, `armed_delay`, `armed_delay_total_delay` (seconds). |
| `InfoOnlyDigital` | `binary_sensor` | On/off sensor. The device class is guessed from the control's own on/off text and its category (door, window, motion, smoke, gas, moisture, vibration, plug, opening) and stays unset when nothing matches. |
| `PresenceDetector` | `binary_sensor` + `sensor` | Presence binary sensor, plus `Illuminance` (lx) and `Noise` (dB) sensors on the same device when the detector reports them. |
| `SmokeAlarm` | `binary_sensor` | Smoke alarm, on when the alarm level is above 0. |
| `Pushbutton` | `button` | Pressing sends `pulse`. Home Assistant shows the time of the last press; the Miniserver's own press echo is the `last_pressed` attribute. A pushbutton has no state, so it cannot reliably *trigger* automations (see [Known limitations](#known-limitations)). |
| `UpDownDigital` | `button` | Two buttons, `Up` and `Down`, on one device; pressing sends `UpOn` / `DownOn`. The control publishes no state. |
| `IRoomControllerV2` | `climate` + `switch` + `sensor` | Room controller V2: presets (the Loxone timer modes), a target temperature, or a target range when the room heats and cools, on/off. Attributes: `is_overridden`, `demand` (1 heating, -1 cooling, 0 idle, fed by the room's `ClimateController`), `operating_mode`, `active_mode`, `current_mode`, `op_mode`, `active_state`. On the same device: a `Comfort Override` switch (configuration category) and diagnostic sensors `Override Reason`, `Comfort Temperature` and `Comfort Temperature (Cool)` for the states the controller reports. |
| `IRoomController` (legacy) | `climate` | The pre-V2 room controller, named `<room> Climate`: target temperature and on/off. Attributes: `mode`, `override`, `open_window`, `curr_heat_temp_ix`, `curr_cool_temp_ix`. The mode table is unverified on real V1 hardware. |
| `AcControl` | `climate` | Air conditioning: target temperature and on/off, plus fan and swing modes when the control publishes fan-speed or airflow lists. Reports `hvac_action`. |
| `Jalousie` | `cover` + `select` | Blinds, shutters, curtains and awnings; the device class follows the Miniserver's `animation` detail. Open, close, stop, set position. Blinds also get tilt open/close/set and the `loxone.quick_shade` service. Controls with sun automation get the `loxone.enable_sun_automation` / `loxone.disable_sun_automation` services and a `Sun auto` select (Off / Auto / Shade) on the same device. Attributes: `current_position`, `current_shade_mode`, `current_position_loxone_style`, and with sun automation `automatic_text`, `auto_state`, `is_sun_automation_enabled`, `target_position`. |
| `Gate` | `cover` | Gates and garage doors (device class garage, gate or door from the `animation` detail): open, close, stop, and set position when the gate reports one. |
| `Window` | `cover` | Motorised windows: open, close, stop, set position. Attribute: `target_position`. |
| `Ventilation` | `fan` | Speed 0-100 % and the preset profiles `Low`, `Medium`, `High`, `Auto`, `Away`. Sub-entities on the same device when reported: `Presence`, `Humidity`, `Air Quality` (as CO2), outdoor `Temperature`. **Known to be wrong on real hardware**: selecting a preset does nothing and setting a speed is a self-reverting one-hour override; see [Known limitations](#known-limitations). |
| `LightControllerV2` | `light` (+ `switch`, `scene`, sub-lights) | The controller: on/off, moods as *effects*, and brightness when it has a master dimmer (no colour on this entity). Attributes: `selected_scene`, `selected_scenes`, `subcontrols`. A controller with a presence input gets a `Presence Detection` switch on the same device. Its `Switch`, `Dimmer`, `EIBDimmer` and `ColorPickerV2` sub-controls become light entities of their own (disabled unless `generate_lightcontroller_subcontrols` is on) with a `light_controller` attribute. Moods become scenes, see below. |
| `Dimmer` / `EIBDimmer` | `light` | Standalone dimmers: on/off and brightness, scaled to the dimmer's own min/max range. |
| `ColorPickerV2` | `light` | Colour pickers, standalone or inside a light controller. `Rgb` pickers give colour and colour temperature, `TunableWhite` pickers colour temperature only (2700-6500 K), `Lumitech` behaves like RGB. |
| `LightsceneRGB` | `light` + `select` | An RGB light driven by the red, green and blue channel streams. When the control has a scene list, a `Scene` select is added on the same device. The write commands are unverified on real hardware. |
| `AudioZoneV2` | `media_player` | Speaker zone: play, pause, stop, next, previous, volume set/step/mute, on/off and source selection. Attributes: `source`, `favourites`. Power, mute and source commands are unverified on real hardware. |
| `Slider` | `number` | A number VI with the Miniserver's min, max and step; unit and precision come from the control's format string. Attribute: `state_uuid`. |
| `LightControllerV2` moods | `scene` | One scene per mood of each light controller, created when `generate_scenes` is on and the controller has pushed its mood list. Activating sends `changeTo/<moodId>`. |
| `Radio` | `select` | Radio-button block as a select. Selecting while the block is locked in Loxone raises an error; a block without outputs is skipped. Attributes: `state_uuid`, `locked`. |
| `InfoOnlyAnalog` | `sensor` | Analog value with automatic device-class detection from unit, category and name (see below). The Miniserver's error value (`-1`) reads as `unknown`. |
| `InfoOnlyText` | `sensor` | Read-only text value. |
| `TextInput` | `text` + `sensor` | A text VI as a settable `text` entity (values longer than 255 characters are truncated) and as a read-only sensor. Attribute: `state_uuid`. |
| `Meter`, `EnergyManager`, `EnergyManager2`, `PowerUnit`, `Wallbox` | `sensor` | One sensor per register the control reports, on one device: `Actual` (power, measurement), `Total` and `Total Neg` (energy, total increasing), `Level` (energy, measurement). Units and precision come from the control's format details. |
| `Tracker` | `sensor` | The tracker's entries as a comma-joined string, also when nested under another control such as an alarm. Attributes: `entries`, `count`, `state_uuid`. |
| `NfcCodeTouch` | `sensor` | Access reader: a `lastuser` sensor plus diagnostic `Code Date` and `Device State` sensors on one device, and a `loxone_nfc_auth` bus event per authentication (see [Events](#events)). The `lastcode` and `lasttag` credentials are never exposed. |
| `ClimateController` | `sensor` | Summary of the rooms demanding heat or cold (`Heating (n)`, `Cooling (n)`, `Idle`); it also feeds the `demand` attribute of each room controller. Attributes: `heat_demand`, `cool_demand`. |
| Message Center | `sensor` + repairs | One diagnostic sensor per Message Center block whose state is the highest active severity; active entries appear as repair issues and disappear when they clear. Attribute: `status` (counts per severity). |
| `Switch` | `switch` | On/off switch. |
| `TimedSwitch` | `switch` | Switch with a deactivation timer; turning on sends `pulse`. Attributes: `delay_time_total`, and `delay` (seconds remaining) while it is on. |
| `Intercom` / `IntercomV2` | `switch` | The intercom's sub-controls that report an `active` state, as switches on the intercom's device. |

Loxone entities carry the attributes `uuid` (the control's `uuidAction`, usable
with the [websocket command services](#services)), `platform` (`loxone`),
`room` and `category` (when the Miniserver supplies them), and on most
platforms `device_type` (the Loxone control family) and `state_uuid` (the
state stream the entity reads). These bookkeeping attributes are excluded from
the recorder; see [Recorder configuration](#recorder-configuration).

### Enabling LightControllerV2 sub-controls

A `LightControllerV2` groups its outputs (dimmers, colour pickers, switches)
as sub-controls. They are always registered; whether they start enabled is
the `generate_lightcontroller_subcontrols` option. To enable one later:

1. Settings > Devices & Services > Entities
2. Filter by the light controller's device
3. Enable the sub-control entities you want

### Sensor device class detection

`InfoOnlyAnalog` sensors get a device class from their unit and, where the
unit is ambiguous, from the Loxone category or name:

| Device class | Detected by |
|---|---|
| `temperature` | Unit `°C` or `°F` |
| `humidity` | Unit `%` **and** category or name contains "humidity", "vlhkost", "feucht" or "humidité" |
| `battery` | Unit `%` **and** name contains "batt", "akku" or "battery" |
| `energy` | Unit `kWh`, `Wh` or `MWh` |
| `power` | Unit `W` or `kW` |
| `volume_flow_rate` | Unit `L/h` or `L/min` |
| `water` | Unit `L` |
| `illuminance` | Unit `lx`, `Lx` or `lux` |
| `carbon_dioxide` | Unit `ppm` |
| `wind_speed` | Unit `km/h` |

A `%` sensor that matches no keyword gets no device class. An energy or water
value only becomes a total-increasing sensor (the kind the energy dashboard
wants) when its name or category says "total", "meter" or "counter";
otherwise it is a plain measurement.

To override a detected class, or set one on an unclassified sensor, use Home
Assistant's [entity customization](https://www.home-assistant.io/docs/configuration/customizing-devices/):

```yaml
homeassistant:
  customize:
    sensor.my_percentage_sensor:
      device_class: battery
  customize_glob:
    sensor.*humidity*:
      device_class: humidity
```

### Known limitations

- **Pushbuttons are stateless.** They cannot reliably trigger automations.
  Use a switch in Loxone instead and turn it off again in the automation or
  in Loxone.
- **Ventilation presets and speed do not work on real hardware.** A live
  test showed that the Miniserver ignores the mode command the fan entity
  sends, and that the speed command is a one-hour override that reverts.
  Reading the current speed works. A redesign is planned; the correct
  commands are not yet known (`docs/review/2026-09-remediation-plan.md`,
  "Ventilation fan model rework").
- Some commands were implemented from documentation and inference rather
  than observed on hardware (audio zone power/mute/source, `LightsceneRGB`
  writes, gate positioning, `UpDownDigital`, `Tracker` payloads, the legacy
  room controller mode table). Each is listed with a test procedure in
  [`docs/review/LIVE-MINISERVER-CHECKS.md`](docs/review/LIVE-MINISERVER-CHECKS.md);
  reports from real installations are welcome.

## Events

### `loxone_event`

Every state message the Miniserver pushes over the websocket is republished
on the Home Assistant bus as a `loxone_event` event. The event data is the
raw message, one key per changed Loxone UUID mapped to its new value, plus
the entry id:

| Key | Meaning |
|---|---|
| `<uuid>` | the new value of the Loxone state with that UUID; several UUIDs can change in one message |
| `entry_id` | the config entry of the Miniserver that produced the message; with more than one Miniserver this is how you tell them apart |

Keep-alive values arrive under the key `keep_alive`.

```yaml
automation:
  - alias: React to a Loxone state change
    trigger:
      - platform: event
        event_type: loxone_event
    condition:
      - condition: template
        value_template: >
          {{ '152c22de-033a-94b5-ffff403fb0c34b9e' in trigger.event.data }}
    action:
      - ...
```

### `loxone_nfc_auth`

Fired once per authentication on an `NfcCodeTouch` reader, with `uuid` and
`name` of the reader, `user` (who authenticated), `code_date` (ISO 8601, UTC)
and `entry_id`. The code or tag used is never part of the event.

### Outbound bus events (legacy)

Commands can also be sent by firing `loxone_send` (or `loxone_send_secured`
for secured controls) with `uuid`, `value` and, for the secured variant,
`code` in the event data. Each Miniserver only executes commands for UUIDs
it knows, so an event never reaches the wrong Miniserver. Prefer the
`loxone.event_websocket_command` service; the bus events remain for
existing users of the old interface.

## Services

The service schemas live in
[`custom_components/loxone/services.yaml`](custom_components/loxone/services.yaml),
which is also what Home Assistant's Developer Tools render.

| Service | Target | Fields | What it does |
|---|---|---|---|
| `loxone.event_websocket_command` | any loaded Miniserver | `uuid` **or** `device` (exactly one), `value` (default `""`) | Send an arbitrary websocket command (`value`) to a control, addressed by Loxone UUID or by one of the integration's own entities. Raises when the target belongs to no loaded Miniserver. |
| `loxone.event_secured_websocket_command` | any loaded Miniserver | `uuid` **or** `device`, `value`, `code` | As above, through the secured channel; `code` is the control's visual password. |
| `loxone.sync_areas` | all entries | `create_areas` (bool, default `false`) | Move every Loxone entity to the area named after its `room` attribute. Missing areas are created only when `create_areas` is true; otherwise such entities are left untouched. |
| `loxone.reload` | all entries or one | `entry_id` (optional) | Reload the integration, safely, through Home Assistant's own unload-then-load. Without `entry_id` every Loxone entry reloads. |
| `loxone.enable_sun_automation` | `Jalousie` covers with sun automation | | Enable the Loxone sun automation on the targeted cover. |
| `loxone.disable_sun_automation` | `Jalousie` covers with sun automation | | Disable the Loxone sun automation on the targeted cover. |
| `loxone.quick_shade` | `Jalousie` covers (blinds) | | Move the slats to the shade position the Miniserver computes. |

Example:

```yaml
service: loxone.event_websocket_command
data:
  device: switch.hall_light   # or: uuid: 0f1e0b31-0178-7f77-ffff403fb0c34b9e
  value: pulse
```

The command names for each control type are in the Loxone structure file
documentation on the [Loxone website](https://www.loxone.com/dede/kb/api/).

## Repairs, diagnostics and system health

- **Repair issues** (Settings > System > Repairs): the Miniserver rejected
  the stored credentials (with a re-authentication prompt), the Miniserver
  firmware is below 7.0.0, a leftover `loxone:` block in `configuration.yaml`
  (it was never read; remove it), and one issue per active Message Center
  entry with the Loxone help link.
- **Diagnostics download** (device page > three-dot menu > *Download
  diagnostics*) contains the structure file with the serial number, MAC,
  project name, URLs, credentials and tokens redacted. It is safe to attach
  to an issue.
- **System health** (Settings > System > Repairs > three-dot menu > *System
  information*) shows the serial, project name, local and remote URL and
  software version of each connected Miniserver.

## Log configuration

The integration logs under `custom_components.loxone`; the websocket protocol
client under `custom_components.loxone.pyloxone_api`. For an issue report:

```yaml
logger:
  default: warning
  logs:
    custom_components.loxone: debug
    custom_components.loxone.pyloxone_api: debug
```

A healthy session logs nothing at WARNING or above. A lost connection logs
one WARNING when it drops and one INFO when it is back.

## Recorder configuration

A Loxone system generates a few thousand `loxone_event` events per day.
Exclude them from the recorder unless you need them in the history:

```yaml
recorder:
  exclude:
    event_types:
      - loxone_event
```

The bookkeeping attributes of Loxone entities (`uuid`, `platform`, `room`,
`category`, `state_uuid`, `device_type`) are already excluded by the
integration.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| "Failed to perform action" on covers, climates, fans or buttons | Fixed in 0.10.7 (0.10.5 for most of them). Update. |
| The integration stays down after a Miniserver firmware update or reboot | A booting Miniserver briefly answers 401. Since 0.10.0 setup retries and only asks for re-authentication after five failures over five minutes. On upstream 0.9.23 the entry stayed dead until reloaded by hand. |
| A repair asks you to re-authenticate although the password is right | The Miniserver rejected the token and the password. Check the user is not locked or expired in Loxone Config, then complete the repair. |
| Log floods with "Callback error" on every state message | Fixed in 0.10.1. |
| Entity names look different after upgrading | 0.10.0 adopted Home Assistant's entity naming: the entity adopts the device name instead of repeating it. Entity ids are unchanged. |
| Half of a dimmer's brightness slider does nothing | Fixed in 0.10.0: dimmers now honour their Loxone min/max range. |
| Ventilation presets have no effect | Known limitation, see above. |
| Home Assistant refuses to load the entry after going back to upstream | The fork's config entry version is newer than upstream's. Restore a backup or remove and re-add the integration. |

## Recipes

### Send text to a block's API Connector through a TextInput

The Audioserver's API is limited; switching presets from Home Assistant is
not possible through the media player. Create a virtual text input (VTI) in
Loxone Config and connect it to the Audio Player block's API Connector:

<img src="./images/vti.png" width="25%">

Then send commands to the VTI's `text` entity:

```yaml
service: loxone.event_websocket_command
data:
  device: text.ingang_vti1
  value: SET(Ap;Fav;4)
```

The block's text-to-speech works the same way:

```yaml
service: loxone.event_websocket_command
data:
  device: text.ingang_vti1
  value: SET(AP;TTS;Woop-woop, that's the sound of da police)
```

### Push a Home Assistant value into the Miniserver through a Slider

The Miniserver supports far fewer devices than Home Assistant (a DSMR P1
meter, for example). Create a virtual input in Loxone Config, configure it
as a slider, and it appears here as a `number` entity. An automation that
triggers on the Home Assistant sensor and calls `number.set_value` keeps the
Miniserver in sync.

### Read any state as a YAML sensor

Any Loxone state can be read through a YAML sensor by UUID, which is how
you reach values of controls the integration does not model:

```yaml
sensor:
  - name: RoomComfortTemperature
    platform: loxone
    uuidAction: "15beed5b-01ab-d81d-ffff2b06d5b9c660"
    unit_of_measurement: "°C"
    device_class: temperature   # any Home Assistant sensor device class
    state_class: measurement    # measurement, total or total_increasing
```

Combined with the websocket command service this gives you write access
too. A script that raises or lowers a room controller's comfort temperature
by 0.5 °C:

```yaml
script:
  tempup:
    alias: TempUp
    sequence:
      - service: loxone.event_websocket_command
        data:
          uuid: "15beed5b-01ab-d81f-ffff2b06d5b9c660"
          value: "setComfortTemperature/{{ states('sensor.roomcomforttemperature') | float + 0.5 }}"
  tempdown:
    alias: TempDown
    sequence:
      - service: loxone.event_websocket_command
        data:
          uuid: "15beed5b-01ab-d81f-ffff2b06d5b9c660"
          value: "setComfortTemperature/{{ states('sensor.roomcomforttemperature') | float - 0.5 }}"
```

The same pattern drives an `UpDownAnalog`: a YAML sensor on its `uuidAction`
to read the value, and two scripts that send `<current value> + 1` and
`<current value> - 1` to the same UUID.

### Finding a UUID

Every Loxone entity exposes its control UUID as the `uuid` attribute
(Developer Tools > States). For controls that have no entity, open the
structure file in a browser and search it:

```
http://<miniserver-ip>:<port>/data/LoxAPP3.json
```

After logging in you get the whole structure file as JSON. Each control is
listed under `controls` with its `name`, `type`, `uuidAction` and a `states`
map of the UUIDs it streams, for example:

```json
"15beed5b-01ab-d81f-ffff2b06d5b9c660": {
    "name": "Intelligente Raumregelung",
    "type": "IRoomControllerV2",
    "uuidAction": "15beed5b-01ab-d81f-ffff2b06d5b9c660",
    "states": {
        "tempActual": "15beed5b-01ab-d7f7-ffff2b06d5b9c660",
        "comfortTemperature": "15beed5b-01ab-d81d-ffff2b06d5b9c660",
        "activeMode": "15beed5b-01ab-d7f1-ffff2b06d5b9c660"
    }
}
```

Commands go to the `uuidAction`; a YAML sensor reads one of the `states`.

## Contributing

Bug reports and feature requests go to the
[issue tracker](https://github.com/alxzndr/PyLoxone/issues); the issue form
asks for the Miniserver firmware, the Home Assistant version and a debug log
(see [Log configuration](#log-configuration)). Development setup, tests and
the release process are in [CONTRIBUTING.md](CONTRIBUTING.md); release notes
are in [CHANGELOG.md](CHANGELOG.md); the longer maintainer documentation is
indexed in [`docs/README.md`](docs/README.md).

Licensed under the Apache License 2.0, see [LICENSE](LICENSE).

# PyLoxone
![Installs](https://img.shields.io/badge/dynamic/json?color=41BDF5&logo=home-assistant&label=Installations&cacheSeconds=15600&url=https://analytics.home-assistant.io/custom_integrations.json&query=$.loxone.total)
[![Latest release](https://img.shields.io/github/v/release/JoDehli/PyLoxone?label=version)](https://github.com/JoDehli/PyLoxone/releases/latest)
![Hassfest](https://img.shields.io/github/actions/workflow/status/JoDehli/PyLoxone/hassfest.yaml?label=hassfest)
![HACS](https://img.shields.io/github/actions/workflow/status/JoDehli/PyLoxone/validate.yaml?label=HACS)
[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)

If you want to support my work on this binding you can buy me a coffee:

<a href="https://www.buymeacoffee.com/JoDehli" target="_blank"><img src="https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png" alt="Buy Me A Coffee" style="height: 41px !important;width: 174px !important;box-shadow: 0px 3px 2px 0px rgba(190, 190, 190, 0.5) !important;-webkit-box-shadow: 0px 3px 2px 0px rgba(190, 190, 190, 0.5) !important;" ></a>

Home Assistant binding for Loxone Miniservers.

A special thanks to Pawel Pieczul from the great openhab2 house automation software. 
He really helped me a lot to with the new token based authentication. Thanks Pawel!!!

## Requirements

- **Home Assistant 2026.7.0 or newer** -- this is the floor advertised to
  HACS (`hacs.json`) and matches the manifests of this release.
- State updates are **pushed over the websocket** connection (the
  integration never polls), and all state is event-driven.
- Home Assistant itself must run on **CPython 3.14.2 or newer** (Home
  Assistant 2026.x); that is also the minimum for the development
  environment -- see [CONTRIBUTING.md](CONTRIBUTING.md).

## Config for the gen2 miniserver
If you have the gen2 miniserver you should connect via local access. It is more reliable and faster than a cloud connection.

### Config for cloud connection
Please use this only in case local access is not possible.

Example:
- Miniserver Ip: https://dns.loxonecloud.com/123456789ABC
- Port: 443

Change 123456789ABC to your miniserver Serial Number.

## Manual installation
1. Download the zip file and extract all files.
2. Copy the ***custom_components*** folder in the same folder where your configuration.yaml is located
3. Restart Home-Assistant
4. Go to Configuration -> Integrations and search for Pyloxone
5. Add the Integration and fill out all required fields
6. Restart Home-Assistant

## Hacs installation
1. Install hacs to your homeassistant installation. See https://hacs.xyz/docs/use/download/download/
2. Add this repository to hacs: https://github.com/JoDehli/PyLoxone
3. Install the PyLoxone binding 
4. Restart Home-Assistant
5. Go to Configuration -> Integrations and search for Pyloxone
6. Add the Integration and fill out all required fields
7. Restart Home-Assistant

## Configuring the integration

The integration is configured through the UI (no YAML). Adding a Miniserver
asks for the connection description:

| Field | Default | Meaning |
|---|---|---|
| `host` | -- | IP or hostname of the Miniserver. For a cloud connection use `dns.loxonecloud.com/<serial>` (see above). |
| `port` | `8080` | HTTP port. `8443` for the local HTTPS tunnel, `443` for the Loxone Cloud address. |
| `username` | -- | Loxone Miniserver user (e.g. `admin`). |
| `password` | -- | Password of that user. Never sent over the wire in plaintext: the API layer authenticates with a token. |
| `verify_ssl` | `true` | Enable TLS certificate verification on the HTTPS paths (local HTTPS tunnel and the Loxone Cloud connection). **Do not disable this**: the same connection carries your credentials and every state update, so an unverified TLS downgrade lets an attacker read and rewrite both. Leave it on unless you have a specific, understood reason (self-signed certificate you trust) and can tie it to your network. The plain-HTTP local path (port 8080) is not affected by this setting. |

The connection description is stored in the config entry's **data**; changing
it later (moved Miniserver, new IP, renewed cloud address) is done through the
**reauthentication** flow in *Settings -> Devices & Services*, not on the
options page.

Credentials that contain characters outside of Latin-1 are rejected by the
Miniserver; the form tells you when a credential cannot be represented.

## Configuration options

The options page (*Settings -> Devices & Services -> PyLoxone -> the
Miniserver entry -> Options, Pencil icon) carries only preferences. All of them apply after the
entry reloads (saving the options triggers the reload).

| Option | Default | Effect |
|---|---|---|
| `verify_ssl` | `true` | Verifies the TLS certificate of the HTTPS paths when connecting (collected on add/reauth, not on the options page). **Keep it on**: with verification off, a man-in-the-middle can read and rewrite both credentials and state. The plain-HTTP local path (port 8080) is not affected by this setting. |
| `generate_scenes` | `true` | Generate one scene entity per mood of every `LightControllerV2`, as soon as the Miniserver streams the controller's mood list. Disable to skip scene generation entirely. |
| `generate_scenes_delay` | `3` (minimum `3`) | Legacy setting, kept for existing installs. Traditionally the number of seconds the integration waited after setup before generating the `LightControllerV2` scenes. Scene generation had to happen after the light platform was fully loaded into Home Assistant, which on a slow install takes a few seconds -- `3` sec was the smallest value that reliably waited that out, which is why the option has a minimum of 3. Current versions generate scenes event-driven (immediately when the Miniserver pushes the mood list), so the stored value does not throttle scene creation anymore; the floor stays to keep migrated entries valid. |
| `generate_lightcontroller_subcontrols` | new installs: `false`; pre-option installs: `true` (migrated default) | Controls the *default* enabled state of the individual sub-control entities (switches, dimmers, color pickers) underneath each `LightControllerV2`. With the option on, newly registered sub-control entities start **enabled**; with it off they start **disabled** but remain in the entity registry, so you can enable any of them later (see below). The sub-controls are always registered; the option only decides their initial visibility. |
| `generate_groups` | new installs: `false`; pre-option installs: option not set = groups on | Controls the Loxone auto-groups: the master group `group.loxone_group` plus one subgroup per control family (analog sensors, digital sensors, switches, buttons, covers, lights, dimmers, climates, ventilations, accontrollers, numbers, texts). New installs start with groups off; on existing installs that date from before the option existed, the legacy behaviour (groups on) is kept until you decide on the options page. |

## LightControllerV2 Sub-Controls

Each `LightControllerV2` in Loxone groups individual lighting outputs (dimmers, color pickers, switches) as sub-controls. These are registered in the Home Assistant entity registry, enabled or disabled by the **`generate_lightcontroller_subcontrols`** option (see the table above).

To access individual light outputs (or enable one that starts disabled):

1. Open **Settings → Devices & Services → Entities**
2. Filter by your LightControllerV2 device
3. Enable the sub-control entities you want to use (dimmer, color picker, etc.)

## Entities and attributes

Every Loxone control is mapped to a Home Assistant entity of the domain
shown below. All Loxone entities share a small set of state attributes:

| Common attribute | Meaning |
|---|---|
| `uuid` | the Loxone `uuidAction` of the control (use it with the websocket command services below) |
| `platform` | always `loxone` |
| `room` | the room name of the control (when the Miniserver supplies one) |
| `category` | the Loxone category name of the control (when supplied) |
| `device_type` | the Loxone control family (`Sensor analog`, `Switch`, `Jalousie`, ...) |

(`uuid`, `platform`, `room`, `category`, `state_uuid`, `device_type` are kept
out of the recorder, see [Recorder Configuration](#recorder-configuration).)
Some platforms additionally report the `state_uuid` (the stream the entity
reads its state from) and platform-specific attributes, listed here:

| Loxone control | HA domain | Notes and extra attributes |
|---|---|---|
| `InfoOnlyAnalog` | `sensor` | Automatic device-class detection from unit/category/name (see below). The Miniserver's "error" value (`-1`) is reported as `unknown`. |
| `Meter` | `sensor` | Meter block with sub-sensors for actual/total/totalNeg/storage registers (`<name> Actual`, `<name> Total`, ...), classified as energy/power. |
| `InfoOnlyDigital` | `binary_sensor` | Digital on/off sensors. |
| `PresenceDetector` | `binary_sensor` | Presence detector (`<name> Presence`). |
| `SmokeAlarm` | `binary_sensor` | Smoke alarm. |
| `Switch` | `switch` | On/off switch. |
| `TimedSwitch` | `switch` | Switch with a deactivation timer; extra attributes `delay` (seconds remaining) and `delay_time_total` (seconds total). |
| `Pushbutton` | `button` | A press is an event, not a state: the entity is press-ready (state `on`), last press is recorded in the `last_pressed` attribute; also `state_uuid`, `new_state`, `state_value`. Stateless -- cannot be used to *trigger* automations reliably (see Known Limitations). |
| `Jalousie` | `cover` | Blinds/shutters/awnings. Open/close/stop, set position; with a shade position: tilt open/close/set plus the `quick_shade` entity service; with sun automation: `enable_sun_automation` / `disable_sun_automation` services. Extra: `current_position`, `current_shade_mode`, `current_position_loxone_style`, and (sun-automated) `automatic_text`, `auto_state`, `is_sun_automation_enabled`, `target_position`. |
| `Window` | `cover` | Real windows: open/close/stop, set position. Extra: `target_position`. |
| `Gate` | `cover` | Gates/garage doors (device class derived from the Miniserver's `animation` detail: garage/gate/door): open/close/stop. |
| `Intercom` (sub-controls) | `switch` | The intercom's reachable buttons as switches. |
| `InfoOnlyAnalog` / `Meter` attributes on a `LightControllerV2` | `sensor` | Fan-out sensors for sliders and other analog states attached to lights (e.g. ventilation hubs). |
| `LightControllerV2` | `light` | The controller itself: on/off, brightness, color, effects (moods). Extra: `selected_scene`, `selected_scenes`, `subcontrols`, `device_type`. When the controller has a presence input, an auxiliary `<name> Presence Detection` **switch** is created on the same device. |
| `Dimmer` / `EIBDimmer` | `light` | Standalone dimmers (not attached to an `LightControllerV2`). |
| `ColorPickerV2` | `light` | RGB / Kelvin color pickers, standalone or as sub-controls. |
| `Alarm` | `alarm_control_panel` | Arm home / arm away (arm away and arm home are both supported; disarm always). Secured alarms additionally want a numeric **arm code**. Extra: `level`, `armed_at`, `next_level_at`, `armed_delay`, `armed_delay_total_delay`. |
| `IRoomControllerV2` | `climate` + `switch` + `sensor` | RoomControllerV2 as climate (targets, modes, comfort), plus a `<name> Comfort Override` **switch** (entity category: config) and diagnostic **sensors** for the comfort temperature and the override reason. Extra (climate): `override`, `demand` (1 = heating, -1 = cooling, 0 = idle, from the linked `ClimateController`). |
| `IRoomController` (legacy) | `climate` | The non-V2 room controller. Extra: `override`. |
| `AcControl` | `climate` | Air-conditioning control. |
| `AudioZoneV2` | `media_player` | Volume (set/up/down), play, pause, stop, next, previous track. |
| `Ventilation` | `fan` | Ventilation with preset profiles (`Low`, `Medium`, `High`, `Auto`, `Away`) and a 0-100 speed. Sub-sensors on the same device where the Miniserver provides them: presence, indoor humidity, indoor air quality (as CO2), outside temperature. |
| `Slider` | `number` | A number VI with the Miniserver's `(min, max, step, format)` applied to the HA entity. Extra: `state_uuid`. |
| `TextInput` | `text` + `sensor` | A text VI both as settable `text` entity (values over 255 characters are truncated) and as read-only sensor. Extra: `state_uuid`. |
| `Radio` (radio buttons) | `select` | Radio block as a select; when the block is locked in Loxone, selecting raises an error. Extra: `state_uuid`, `locked`. |
| `LightControllerV2` moods | `scene` | One scene per mood (created by the `generate_scenes` option). Activating the scene sends `changeTo/<moodId>` to the controller. |

### Fan
The `fan` domain is only used for `Ventilation` controls: speed is the
0-100 percentage the Miniserver reports, and the preset mode selects the
configured ventilation profiles.

### Scenes
Scenes are generated per `LightControllerV2` from the mood list the
Miniserver pushes over the websocket (no fixed wait after setup).

## Known Limitations

- Pushbuttons are stateless. They can not be used to reliably trigger automations. Use a Switch as a workaround and turn it off again in the Automation or in Loxone itself.

## Sensor Device Class Detection

Sensors (InfoOnlyAnalog, Meter) are automatically classified based on their unit and Loxone category/name. The following device classes are detected:

| Device class | Detected by |
|---|---|
| `temperature` | Unit: °C, °F |
| `humidity` | Unit: % **and** category or name contains "humidity", "vlhkost", "feucht", or "humidité" |
| `battery` | Unit: % **and** name contains "batt", "akku", or "battery" |
| `energy` | Unit: kWh, Wh, MWh |
| `power` | Unit: W, kW |
| `volume_flow_rate` | Unit: L/h, L/min |
| `water` | Unit: L |
| `illuminance` | Unit: lx, Lx, lux |
| `carbon_dioxide` | Unit: ppm |
| `wind_speed` | Unit: km/h |

Sensors with `%` unit that don't match any keyword are left without a device class.

### Overriding the detected device class

If the automatic detection assigns the wrong device class (or you want to set one for an unclassified sensor), use Home Assistant's built-in [entity customization](https://www.home-assistant.io/docs/configuration/customizing-devices/) in `configuration.yaml`:

```yaml
homeassistant:
  customize:
    sensor.my_percentage_sensor:
      device_class: battery
  customize_glob:
    sensor.*humidity*:
      device_class: humidity
```

## Events

### `loxone_event`

Every state message the Miniserver pushes over the websocket is republished
on the Home Assistant bus as a `loxone_event` event, for use with user
automations. The event data is the raw message of the Miniserver --
**one key per changed Loxone UUID, mapped to its new value** -- plus one
extra key:

| Key | Meaning |
|---|---|
| `<uuid>` | the new value of the Loxone state with that UUID (the UUID string is the key, the value is the new value; several UUIDs can change in one message) |
| `entry_id` | the config entry ID of the Miniserver that produced the message -- with **more than one** Loxone Miniserver in Home Assistant this is how you discriminate which one changed (multi-instance setups) |

Individual keep-alive values show up with the special key `keep_alive`.

Example, as an automation (`trigger`):

```yaml
automation:
  - alias: React to a Loxone state change
    trigger:
      - platform: event
        event_type: loxone_event
    condition:
      - condition: template
        value_template: >
          {{ trigger.event.data | length > 1
             and '152c22de-033a-94b5-ffff403fb0c34b9e' in trigger.event.data }}
    action:
      - ...
```

### Outbound bus events (advanced)

You can also *send* commands by firing the bus events `loxone_send`
(or `loxone_send_secured` for secured controls) with `uuid`, `value` and
-- for the secured one -- `code` in the event data. Each loaded Miniserver
only executes commands whose UUID it actually knows, so firing the event
never reaches another Miniserver. Prefer the `loxone.event_websocket_command`
service below; the bus events exist for external users of the old
interface.

## Services

The complete service schema lives in
[`custom_components/loxone/services.yaml`](custom_components/loxone/services.yaml)
(the same source that renders these services in HA's Developer Tools).

| Service | Scope | Data fields | What it does |
|---|---|---|---|
| `loxone.event_websocket_command` | domain (all entries) | `uuid` **or** `device` (exactly one), `value` (default `""`) | Send an arbitrary websocket command (`value`) to a Loxone to the UUID that is known to any of the loaded Miniservers (`uuid`), or through one of the integration's own entities (`device`). Errors are raised if the target does not belong to a loaded Miniserver. |
| `loxone.event_secured_websocket_command` | domain (all entries) | `uuid` **or** `device`, `value`, `code` | As above, but through the secured channel (used by secured controls; `code` is the security code). |
| `loxone.sync_areas` | domain | `create_areas` (bool, default `false`) | Apply Loxone rooms to HA areas of Loxone entities: it moves every Loxone entity to the area with its `room` attribute (creating missing areas only when `create_areas` is true). Entities whose room does not exist in HA are left untouched when `create_areas` is false. |
| `loxone.reload` | domain | `entry_id` (optional string) | Reload the Loxone integration: `async_schedule_reload` per entry, so it is the safe reload (unload-then-load owned by HA). Without `entry_id` all loaded Loxone entries reload; with it, only the named one. |
| `loxone.enable_sun_automation` | entity (Jalousie covers with sun automation) | -- | Enable the Loxone sun automation on the targeted `Jalousie` cover. |
| `loxone.disable_sun_automation` | entity (Jalousie covers with sun automation) | -- | Disable the Loxone sun automation on the targeted `Jalousie` cover. |
| `loxone.quick_shade` | entity (Jalousie covers with shade position) | -- | Move the slats of the targeted `Jalousie` cover to the Loxone-computed shade position (the position depends on multiple values on the Miniserver). |

### Websocket direct command service (example)

```yaml
service: loxone.event_websocket_command
data:
  device: switch.hall_light   # or: uuid: 0f1e0b31-0178-7f77-ffff403fb0c34b9e
  value: pulse
```

You can choose to target a Loxone entity by UUID or by HA entity ID (a Loxone
entity of *any of the loaded* Miniservers). Websocket commands let you, for
example, send data captured by other Home Assistant devices immediately to a
VI on the Miniserver.

## Log Configuration
If you want to paste a log into an issue, the integration's loggers are the
integration root (`custom_components.loxone`) and the websocket protocol
client (`custom_components.loxone.pyloxone_api`); individual modules log
under their own modules:

```yaml
logger:
  default: warning
  logs:
    homeassistant: warning
    homeassistant.helpers: warning
    custom_components.loxone: debug
    custom_components.loxone.pyloxone_api: debug
```

## Recorder Configuration
A Loxone system generates a few thousand events per day. These events are recorded in your homeassistant and the database file can grow a lot per day. It is recommended to exclude loxone events from the recorder using the following settings:

```yaml
recorder:
  exclude:
    event_types:
      - loxone_event
```

In addition, the bookkeeping attributes of Loxone entities (`uuid`,
`platform`, `room`, `category`, `state_uuid`, `device_type`) are excluded
from the recorder by the integration itself.

## Some examples

### Using a TextInput to control a block's API Connector

The TextInput Virtual Input in the Miniserver enables some neat advanced applications.  
The Audioserver for example, has limited API-support. If you want to switch presets from Home Assistant, this is not possible using the traditional Audio Player API interface.

A neat way around this, is by adressing the Audio Player block's API Connector using a VTI (Virtual Text Input). This enables all of the block's functionality in Home Assistant.

First, create a VTI and connect it to the block's API Connector.

<img src="./images/vti.png" width=25%>

Second, send anything you want to this VTI from Home Assistant. In this use case, we want the Audio Player to switch to preset 4:

```
service: loxone.event_websocket_command
data:
  value: SET(Ap;Fav;4)
  device: text.ingang_vti1
```
You can even directly address the Audio Player's TTS engine.
```
service: loxone.event_websocket_command
data:
  device: text.ingang_vti1
  value: SET(AP;TTS;Woop-woop, that's the sound of da police)

```
### Using a Slider to send data from Home Assistant to the Loxone Miniserver

The Miniserver connects to/interfaces with a wide range of third party devices and services. However, support is quite limited in comparison to Home Assistant.  
If you take the DSMR P1 digital meter interface for example. This (serial) interface is supported on a wide range of platforms, but not on the Miniserver.  
A workaround could be creating a VI on the Miniserver and configuring it like a Slider. This VI will then appear in this integration as a Number entity to which you can send any numerical value.  
Any change in the sensor value in Home Assistant should trigger an automation that sets the new value to the entity in the Loxone integration.

## Advanced usage: what to do if your device is not supported
You can integrate nearly every Loxone Entity in your Home Assistant system by adding a custom sensor to your yaml file. 

### Example 1 with a RoomComfortTemperature
Here is a example of a sensor which is displaying the comfort temperature of a room controller v2:
```yaml
sensor:
  - name: RoomComfortTemperature
    platform: loxone
    uuidAction: "15beed5b-01ab-d81d-ffff2b06d5b9c660"
    unit_of_measurement: "°C"
    device_class: "temperature"    # Use device classes from homeassitant for example temperature, humidity, voltage   
    state_class: "total"           # measurement, total or total_increasing see https://developers.home-assistant.io/docs/core/entity/sensor/#long-term-statistics
```
In this example a sensor with the name roomcomforttemperature (sensor.roomcomforttemperature) is created. The sensor is listening to all events from the loxone system with the specified uuid ([How do you get the uuid?](https://github.com/JoDehli/PyLoxone?tab=readme-ov-file#how-do-you-get-the-uuid)).

You can also send any websocket to a loxone entity for example to increase and decrease the temperature of a room controller v2. Here is a script that raises and lowers the temperature in 0.5 °C steps:

```yaml
script:
  tempup:
    alias: TempUp
    mode: single
    sequence:
    - data_template:
        uuid: "15beed5b-01ab-d81f-ffff2b06d5b9c660" 
        value: "setComfortTemperature/{{ states('sensor.roomcomforttemperature')|float+0.5}}"
      service: loxone.event_websocket_command
  tempdown:
    alias: TempDown
    mode: single
    sequence:
    - data_template:
        uuid: "15beed5b-01ab-d81f-ffff2b06d5b9c660"
        value: "setComfortTemperature/{{ states('sensor.roomcomforttemperature')|float-0.5}}"
      service: loxone.event_websocket_command
```

### Example 2 with a UpDownAnalog

- First get the uuidAction as described above for example. Let's assume your uuidAction for the UpDownAnalog is 152ecfaa-03ac-f715-ffff403fb0c34b9e.
- Create a Sensor do display the current value of the UpDownAnalog like this:
```yaml
sensor:
  - name: "Up and Down Sensor"
    platform: loxone
    uuidAction: "152ecfaa-03ac-f715-ffff403fb0c34b9e"
    unit_of_measurement: ""
```
- Create a script for incrementing up and down like this:
```yaml
down:
  alias: Down
  mode: single
  sequence:
  - data_template:
      uuid: "152ecfaa-03ac-f715-ffff403fb0c34b9e"
      value: "{{ states('sensor.up_and_down_sensor')|float-1}}"
    service: loxone.event_websocket_command

up:
  alias: Up
  mode: single
  sequence:
  - data_template:
      uuid: "152ecfaa-03ac-f715-ffff403fb0c34b9e"
      value: "{{ states('sensor.up_and_down_sensor')|float+1}}"
    service: loxone.event_websocket_command
```

The commands for each entity can be found in the structure file. You can download it from the [Loxone Homepage](https://www.loxone.com/dede/kb/api/).

## How do you get the uuid?

If you need the UUID of an entity to use it in a service call or to manually add it to Home Assistant, you can get it from your Loxone setup by visit the following site with your prefered browser:

```
http://{ip-address-of-your-loxone}:{port}/data/LoxAPP3.json

{ip-address-of-your-loxone} --> replace with the ip of your loxone 

{port} --> replace with your port (default: 80)
```
After entering your username and password you will see your LoxApp3.json. You can paste it in your prefered JSON editor/viewer (eg. https://jsonformatter.org/). 
In this file you can find all your uuid ids for all your devices.  


Here is a example of a Room Controller V2: 
```json
        "15beed5b-01ab-d81f-ffff2b06d5b9c660": {
            "name": "Intelligente Raumregelung",
            "type": "IRoomControllerV2",
            "uuidAction": "15beed5b-01ab-d81f-ffff2b06d5b9c660",
            "room": "13efd3e5-019d-8ad2-ffff403fb0c34b9e",
            "cat": "152c22de-0338-94b5-ffff2b06d5b9c660",
            "defaultRating": 2,
            "isFavorite": false,
            "isSecured": false,
            "details": {
                "timerModes": [
                    {
                        "name": "Anwesend",
                        "description": "Komfortbetrieb",
                        "id": 1
                    },
                    {
                        "name": "Abwesend",
                        "description": "Sparbetrieb",
                        "id": 0
                    },
                    {
                        "name": "Aus",
                        "description": "Geb\u0041udeschutz",
                        "id": 2
                    }
                ],
                "format": "%.1f\u00b0",
                "connectedInputs": 0
            },
            "states": {
                "tempActual": "15beed5b-01ab-d7f7-ffff2b06d5b9c660",
                "tempTarget": "15beed5b-01ab-d7f7-ffff2b06d5b9c660",
                "comfortTemperature": "15beed5b-01ab-d81d-ffff2b06d5b9c660",
                "comfortTolerance": "15beed5b-01ab-d800-ffff2b06d5b9c660",
                "absentMinOffset": "15beed5b-01ab-d801-ffff2b06d5b9c660",
                "absentMaxOffset": "15beed5b-01ab-d802-ffff2b06d5b9c660",
                "frostProtectTemperature": "15beed5b-01ab-d803-ffff2b06d5b9c660",
                "heatProtectTemperature": "15beed5b-01ab-d804-ffff2b06d5b9c660",
                "activeMode": "15beed5b-01ab-d7f1-ffff2b06d5b9c660",
                "comfortTemperatureOffset": "15beed5b-01ab-d7ec-ffff2b06d5b9c660",
                "overrideEntries": "15beed5b-01ab-d7ed-ffff2b06d5b9c660",
                "prepareState": "15beed5b-01ab-d7ee-ffff2b06d5b9c660",
                "useOutdoor": "15beed5b-01ab-d7ef-ffff2b06d5b9c660",
                "operatingMode": "15beed5b-01ab-d7f2-ffff2b06d5b9c660",
                "overrideReason": "15beed5b-01ab-d7f4-ffff2b06d5b9c660",
                "openWindow": "15beed5b-01ab-d7f8-ffff2b06d5b9c660",
                "modeList": "15beed5b-01ab-d7f6-ffff2b06d5b9c660"
            },
            "subControls": {
                "15beed5b-01ab-d7eb-ffff2b06d5b9c660": {
                    "name": "Heating and Cooling",
                    "type": "IRCV2Daytimer",
                    "uuidAction": "15beed5b-01ab-d7eb-ffff2b06d5b9c660",
                    "defaultRating": 0,
                    "isFavorite": false,
                    "isSecured": false,
                    "details": {
                        "analog": true,
                        "format": "%.1f\u00b0"
                    },
                    "states": {
                        "entriesAndDefaultValue": "15beed5b-01ab-d7eb-ffff2b06d5b9c660",
                        "mode": "15beed5b-01ab-d81e-ffff2b06d5b9c660",
                        "modeList": "15beed5b-01ab-d7f3-ffff2b06d5b9c660",
                        "value": "15beed5b-01ab-d7f1-ffff2b06d5b9c660"
                    }
                }
            }
        },
```

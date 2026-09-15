"""Every service on every Loxone entity, through the real service registry.

This is the test class that was missing when the executor-thread bug shipped:
the unit tests called handlers directly on the loop thread, but Home Assistant
dispatches a plain ``def`` handler to a worker thread, and only a real
``hass.services.async_call(..., blocking=True)`` reproduces that. Any
thread-context failure (or any other exception) surfaces here as a failure
naming the entity and service. Services an entity does not support are
skipped (HA raises ServiceNotSupported for those, which is correct behaviour).
"""

from __future__ import annotations

from homeassistant.exceptions import ServiceNotSupported, ServiceValidationError
from homeassistant.helpers import entity_registry as er

# (service, static data) or (service, attribute holding valid choices, data key)
SERVICES: dict[str, list] = {
    "cover": [
        ("open_cover", {}),
        ("close_cover", {}),
        ("stop_cover", {}),
        ("set_cover_position", {"position": 50}),
        ("open_cover_tilt", {}),
        ("close_cover_tilt", {}),
        ("stop_cover_tilt", {}),
        ("set_cover_tilt_position", {"tilt_position": 50}),
    ],
    "climate": [
        ("set_temperature", {"temperature": 21}),
        ("set_temperature", {"target_temp_low": 19, "target_temp_high": 23}),
        ("set_hvac_mode", "hvac_modes", "hvac_mode"),
        ("set_preset_mode", "preset_modes", "preset_mode"),
        ("set_fan_mode", "fan_modes", "fan_mode"),
        ("set_swing_mode", "swing_modes", "swing_mode"),
    ],
    "fan": [
        ("turn_on", {}),
        ("turn_off", {}),
        ("set_percentage", {"percentage": 50}),
        ("set_preset_mode", "preset_modes", "preset_mode"),
    ],
    "button": [("press", {})],
    "select": [("select_option", "options", "option")],
    "number": [("set_value", "min", "value")],
    "text": [("set_value", {"value": "smoke"})],
    "light": [("turn_on", {}), ("turn_off", {}), ("turn_on", {"brightness": 128})],
    "switch": [("turn_on", {}), ("turn_off", {})],
    "alarm_control_panel": [
        ("alarm_arm_home", {}),
        ("alarm_arm_away", {}),
        ("alarm_arm_night", {}),
        ("alarm_disarm", {}),
    ],
    "scene": [("turn_on", {})],
    "media_player": [
        ("media_play", {}),
        ("media_pause", {}),
        ("media_stop", {}),
        ("volume_set", {"volume_level": 0.5}),
    ],
}


def _data_for(spec, state):
    if isinstance(spec[1], dict):
        return dict(spec[1])
    attr, key = spec[1], spec[2]
    choices = state.attributes.get(attr)
    if choices is None:
        return None  # attribute not advertised -> nothing valid to send
    if isinstance(choices, list):
        if not choices:
            return None
        return {key: choices[0]}
    return {key: choices}


async def test_every_service_on_every_loxone_entity(hass, mock_connection, mock_entry) -> None:
    mock_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    entities = [e for e in registry.entities.values() if e.config_entry_id == mock_entry.entry_id]
    assert entities, "fixture set-up produced no entities"

    failures: list[str] = []
    calls = 0
    exercised: dict[str, int] = {}
    skipped: dict[str, int] = {}
    for entry in entities:
        domain = entry.entity_id.split(".", 1)[0]
        state = hass.states.get(entry.entity_id)
        if state is None:
            continue
        for spec in SERVICES.get(domain, []):
            data = _data_for(spec, state)
            if data is None:
                continue
            if domain == "alarm_control_panel" and state.attributes.get("code_format"):
                data["code"] = "1234"
            data["entity_id"] = entry.entity_id
            key = f"{domain}.{spec[0]}"
            try:
                await hass.services.async_call(domain, spec[0], data, blocking=True)
                await hass.async_block_till_done()
                calls += 1
                exercised[key] = exercised.get(key, 0) + 1
            except ServiceNotSupported:
                skipped[key] = skipped.get(key, 0) + 1
                continue  # the entity does not advertise that feature: correct
            except ServiceValidationError as err:
                if "does not support" in str(err):
                    # e.g. range parameters on a single-setpoint thermostat: HA
                    # rejects the data shape before the handler runs -- correct.
                    skipped[key] = skipped.get(key, 0) + 1
                    continue
                failures.append(f"{entry.entity_id} {spec[0]} {data}: validation: {err}")
            except Exception as err:  # noqa: BLE001 - we want *every* failure listed, not just the first
                failures.append(f"{entry.entity_id} {spec[0]} {data}: {type(err).__name__}: {err}")

    print("\nexercised:", dict(sorted(exercised.items())))  # noqa: T201 - visible with -s, documents coverage
    print("skipped (unsupported):", dict(sorted(skipped.items())))  # noqa: T201
    # Guard against the smoke test quietly skipping whole platforms: every
    # platform-service pair the fixture can exercise must show up at least once.
    for must in (
        "cover.open_cover",
        "cover.stop_cover",
        "climate.set_temperature",
        "climate.set_hvac_mode",
        "fan.set_percentage",
        "button.press",
        "select.select_option",
        "light.turn_on",
        "switch.turn_on",
        "alarm_control_panel.alarm_arm_home",
    ):
        assert exercised.get(must), f"{must} was never exercised (skipped={skipped.get(must, 0)})"
    assert calls > 50, f"only {calls} service calls were exercised"
    assert not failures, "service calls failed:\n  " + "\n  ".join(failures)

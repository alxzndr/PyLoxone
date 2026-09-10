"""WP-6.1 regression tests: PresenceDetector illuminance/noise sub-sensors (#461).

A PresenceDetector control publishes an ``illuminance`` (lux) and a
``noise`` (dB) state alongside its presence signal.  WP-6.1 turns the
advertised sub-states into analog sub-sensors on the sensor platform that
share the parent presence device (the WP-4.x sub-sensor pattern; expected
values below are derived by hand from ``tests/fixtures/LoxAPP3.json``,
never from the code under test).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock, patch

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.loxone import DOMAIN
from custom_components.loxone.pyloxone_api.connection import LoxoneConnection
from custom_components.loxone.sensor import presence_sub_sensor_kwargs

REPO_ROOT = Path(__file__).resolve().parent.parent

# Hand-transcribed from tests/fixtures/LoxAPP3.json ("Hallway Presence").
PRESENCE_ACTION_UUID = "63746c3a-014c-96c6-ffff-761792050726000076"
PRESENCE_ACTIVE_UUID = "36333734-014d-9336-ffff-d303134632d3000077"
PRESENCE_ILLUMINANCE_UUID = "36333734-014e-9336-ffff-d303134632d3000078"
PRESENCE_NOISE_UUID = "36333734-014f-9336-ffff-d303134632d3000079"


def _control(**overrides) -> dict:
    base = {
        "name": "Hallway Presence",
        "type": "PresenceDetector",
        "uuidAction": PRESENCE_ACTION_UUID,
        "room": "Hall",
        "cat": "Cap",
        "details": {"detected": True},
        "states": {
            "active": PRESENCE_ACTIVE_UUID,
            "illuminance": PRESENCE_ILLUMINANCE_UUID,
            "noise": PRESENCE_NOISE_UUID,
            "time": "never-created-uuid",
        },
    }
    base.update(overrides)
    return base


def _fresh_loxapp3() -> dict:
    return json.loads((REPO_ROOT / "tests/fixtures/LoxAPP3.json").read_text())


def _fake_open_with(config):
    async def _fake_open(self, session=None):
        self.structure_file = config
        self.miniserver_version = config.get("softwareVersion")
        self.connected = True
        self.connection = Mock()
        self.connection.protocol.state.name = "OPEN"
        self._session_key = b"\x00" * 32
        return self

    return patch.object(LoxoneConnection, "open", _fake_open)


# --------------------------------------------------------------------------- #
# Pure helper: presence_sub_sensor_kwargs
# --------------------------------------------------------------------------- #
def test_sub_sensor_kwargs_for_control_with_both_states():
    """Both advertised sub-states yield one LoxoneSensor kwargs each, in
    spec order (illuminance, then noise); the unhandled ``time`` state is
    ignored."""
    kwargs_list = presence_sub_sensor_kwargs(_control(), None)
    assert len(kwargs_list) == 2

    illum, noise = kwargs_list
    for expected_state, kwargs in (
        (PRESENCE_ILLUMINANCE_UUID, illum),
        (PRESENCE_NOISE_UUID, noise),
    ):
        assert kwargs["uuidAction"] == expected_state
        # WP-5.1 short sub-entity name; the device carries the control name.
        assert kwargs["name"] in ("Illuminance", "Noise")
        assert kwargs["name"] != "Hallway Presence"
        assert kwargs["parent_id"] == PRESENCE_ACTION_UUID
        assert kwargs["type"] == "analog"
        assert kwargs["room"] == "Hall"
        assert kwargs["cat"] == "Cap"
        # The format fixes the unit: lux for illumination, dB for noise.
        assert kwargs["details"] in ({"format": "%.0f lx"}, {"format": "%.0f dB"})
        # The sub-sensor attaches to the PARENT control's device
        # (shared identifiers -> one merged device, not one per sub-sensor).
        info = kwargs["device_info"]
        assert info["identifiers"] == {(DOMAIN, PRESENCE_ACTION_UUID)}
        assert info["name"] == "Hallway Presence"
        assert info["model"] == "presence"


def test_sub_sensor_kwargs_units():
    illum, noise = presence_sub_sensor_kwargs(_control(), None)
    assert illum["name"] == "Illuminance"
    assert illum["details"] == {"format": "%.0f lx"}
    assert noise["name"] == "Noise"
    assert noise["details"] == {"format": "%.0f dB"}


def test_sub_sensor_kwargs_without_states():
    assert presence_sub_sensor_kwargs(_control(states=None), None) == []


def test_sub_sensor_kwargs_without_states_key():
    control = _control()
    del control["states"]
    assert presence_sub_sensor_kwargs(control, None) == []


def test_sub_sensor_kwargs_without_uuid_action():
    control = _control(uuidAction="")
    assert presence_sub_sensor_kwargs(control, None) == []


def test_sub_sensor_kwargs_only_advertised_states():
    """A detector with only the noise hardware yields only the noise
    sub-sensor."""
    kwargs_list = presence_sub_sensor_kwargs(
        _control(states={"active": PRESENCE_ACTIVE_UUID, "noise": PRESENCE_NOISE_UUID}), None
    )
    assert [k["name"] for k in kwargs_list] == ["Noise"]
    assert kwargs_list[0]["uuidAction"] == PRESENCE_NOISE_UUID


def test_sub_sensor_kwargs_skips_empty_state_uuids():
    kwargs_list = presence_sub_sensor_kwargs(
        _control(states={"active": PRESENCE_ACTIVE_UUID, "illuminance": "", "noise": None}), None
    )
    assert kwargs_list == []


# --------------------------------------------------------------------------- #
# Setup from the fixture: the new entities appear and land on the parent
# device
# --------------------------------------------------------------------------- #
async def test_setup_creates_illuminance_noise_sub_sensors(
    hass, mock_connection, mock_entry, enable_custom_integrations
):
    mock_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    illum = registry.async_get_entity_id("sensor", "loxone", PRESENCE_ILLUMINANCE_UUID)
    noise = registry.async_get_entity_id("sensor", "loxone", PRESENCE_NOISE_UUID)
    assert illum == "sensor.hall_hallway_presence_illuminance"
    assert noise == "sensor.hall_hallway_presence_noise"

    # Short names, parent presence device for both sub-sensors.
    devices = dr.async_get(hass)
    for entity_id in (illum, noise):
        device = devices.async_get(registry.async_get(entity_id).device_id)
        assert device is not None
        assert device.name == "Hallway Presence"
        assert device.model == "presence"

    # ... and that is the *same* device as the presence binary sensor.
    presence = registry.async_get_entity_id("binary_sensor", "loxone", PRESENCE_ACTION_UUID)
    assert presence == "binary_sensor.hall_hallway_presence"
    assert devices.async_get(registry.async_get(presence).device_id) is devices.async_get(
        registry.async_get(illum).device_id
    )

    # Unit-grade classification comes from the format: lux -> illuminance;
    # dB has no device class in the integration's unit table.
    assert hass.states.get(illum).attributes["device_class"] == "illuminance"
    assert hass.states.get(illum).attributes["unit_of_measurement"] == "lx"
    assert hass.states.get(noise).attributes.get("device_class") is None
    assert hass.states.get(noise).attributes["unit_of_measurement"] == "dB"

    await hass.config_entries.async_unload(mock_entry.entry_id)
    await hass.async_block_till_done()


async def test_setup_presence_detector_without_sub_states_creates_no_sub_sensors(
    hass, mock_entry, enable_custom_integrations
):
    """Guards: a detector advertising no illuminance/noise state yields the
    presence binary sensor and nothing more (no KeyError, no crash)."""
    config = _fresh_loxapp3()
    config["controls"][PRESENCE_ACTION_UUID]["states"] = {"active": PRESENCE_ACTIVE_UUID}

    mock_entry.add_to_hass(hass)
    with _fake_open_with(config):
        assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    entity_ids = hass.states.async_entity_ids("sensor")
    assert not any("hall_hallway_presence" in i for i in entity_ids)
    # The presence binary sensor itself is still there.
    assert "binary_sensor.hall_hallway_presence" in hass.states.async_entity_ids("binary_sensor")

    await hass.config_entries.async_unload(mock_entry.entry_id)
    await hass.async_block_till_done()


# --------------------------------------------------------------------------- #
# State updates: a fed state event updates the sub-sensor
# --------------------------------------------------------------------------- #
async def test_sub_sensors_update_on_fed_state_event(hass, mock_connection, mock_entry, enable_custom_integrations):
    mock_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    mock_connection.feed(PRESENCE_ILLUMINANCE_UUID, 13)
    await hass.async_block_till_done()
    state = hass.states.get("sensor.hall_hallway_presence_illuminance")
    assert state.state == "13"
    assert state.attributes["unit_of_measurement"] == "lx"

    mock_connection.feed(PRESENCE_NOISE_UUID, 42)
    await hass.async_block_till_done()
    state = hass.states.get("sensor.hall_hallway_presence_noise")
    assert state.state == "42"
    assert state.attributes["unit_of_measurement"] == "dB"

    # A feed on the parent presence signal does not touch the sub-sensors.
    mock_connection.feed(PRESENCE_ACTIVE_UUID, 1.0)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.hall_hallway_presence_illuminance").state == "13"
    assert hass.states.get("sensor.hall_hallway_presence_noise").state == "42"

    await hass.config_entries.async_unload(mock_entry.entry_id)
    await hass.async_block_till_done()

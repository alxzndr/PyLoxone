"""Forward compatibility with Home Assistant 2026.11 (and the 2026.7 floor).

* Climate: 2026.11 renamed ``current_temperature`` / ``target_temperature`` /
  ``target_temperature_high`` / ``_low`` / ``temperature_unit`` to ``native_*``
  and warns about every class still implementing the old names. The Loxone
  climate entities implement the native names; ``NativeTemperatureCompat``
  serves the old names on releases that don't know the new ones.
* Device info: 2026.11 turned ``DeviceInfo`` into a dataclass; plain dicts are
  still accepted but "to be deprecated". ``device_info_for`` builds the
  ``DeviceInfo`` of whatever release runs.

Run against the pinned release *and* the HA beta CI job: the deprecation
assertions are only non-vacuous on 2026.11+, the state assertions on both.
Uuids are copied from tests/fixtures/LoxAPP3.json.
"""

from __future__ import annotations

import dataclasses
import inspect

import pytest
from homeassistant.components.climate import ClimateEntity
from homeassistant.helpers import deprecation
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.loxone.climate import (
    CLIMATE_HAS_NATIVE_TEMPERATURES,
    LoxoneAcControl,
    LoxoneRoomController,
    LoxoneRoomControllerV2,
    NativeTemperatureCompat,
)
from custom_components.loxone.const import DOMAIN
from custom_components.loxone.helpers import device_info_for

BEDROOM_V2 = "63746c3a-0126-9647-ffff-f6f6d20436f6000038"  # climate.bedroom_bedroom_controller
BEDROOM_TEMP_ACTUAL = "36333734-0127-9336-ffff-d303132362d3000039"
BEDROOM_TEMP_TARGET = "36333734-0128-9336-ffff-d303132362d3000040"
BEDROOM_OPERATING_MODE = "36333734-0134-9336-ffff-d303132362d3000052"
OFFICE_LEGACY = "63746c3a-013e-9666-ffff-36520436f6e7000062"  # IRoomController
OFFICE_TEMP_ACTUAL = "36333734-013f-9336-ffff-d303133652d3000063"
OFFICE_TEMP_TARGET = "36333734-0140-9336-ffff-d303133652d3000064"
AC_HALLWAY = "63746c3a-0145-9204-ffff-16c6c7761793000069"  # AcControl
AC_TEMPERATURE = "36333734-0146-9336-ffff-d303134352d3000070"
AC_TARGET = "2d646464-7265-9e0aa-4cff-1c87409cd75d"


async def _setup(hass, mock_entry) -> None:
    mock_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()


def _climate_entity_id(hass, unique_id: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id("climate", DOMAIN, unique_id)
    assert entity_id is not None
    return entity_id


async def test_climate_temperatures_reach_the_state(hass, mock_connection, mock_entry) -> None:
    await _setup(hass, mock_entry)

    mock_connection.feed(BEDROOM_OPERATING_MODE, 4)  # MANUAL_HEAT: single target
    mock_connection.feed(BEDROOM_TEMP_ACTUAL, 19.5)
    mock_connection.feed(BEDROOM_TEMP_TARGET, 21.0)
    mock_connection.feed(OFFICE_TEMP_ACTUAL, 18.0)
    mock_connection.feed(OFFICE_TEMP_TARGET, 20.5)
    mock_connection.feed(AC_TEMPERATURE, 24.0)
    mock_connection.feed(AC_TARGET, 22.0)
    await hass.async_block_till_done()

    expected = {
        BEDROOM_V2: (19.5, 21.0),
        OFFICE_LEGACY: (18.0, 20.5),
        AC_HALLWAY: (24.0, 22.0),
    }
    for unique_id, (current, target) in expected.items():
        state = hass.states.get(_climate_entity_id(hass, unique_id))
        assert state is not None
        assert state.attributes["current_temperature"] == current, unique_id
        assert state.attributes["temperature"] == target, unique_id


async def test_set_temperature_service_still_dispatches(hass, mock_connection, mock_entry) -> None:
    """The rename must not break the write path HA validates against ``native_*``."""
    await _setup(hass, mock_entry)
    mock_connection.feed(BEDROOM_OPERATING_MODE, 4)
    await hass.async_block_till_done()

    sent_before = len(mock_connection.sent)
    await hass.services.async_call(
        "climate",
        "set_temperature",
        {"entity_id": _climate_entity_id(hass, BEDROOM_V2), "temperature": 22},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert mock_connection.sent[sent_before:] == [
        {"uuid": BEDROOM_V2, "value": "setManualTemperature/22.0", "code": None}
    ]


def test_native_probe_matches_the_running_release() -> None:
    assert hasattr(ClimateEntity, "native_current_temperature") is CLIMATE_HAS_NATIVE_TEMPERATURES


_NATIVE_NAMES = (
    "native_current_temperature",
    "native_target_temperature",
    "native_target_temperature_high",
    "native_target_temperature_low",
    "native_temperature_unit",
)


@pytest.mark.parametrize("cls", [LoxoneRoomControllerV2, LoxoneRoomController, LoxoneAcControl])
def test_climate_classes_provide_no_deprecated_temperature_member(cls) -> None:
    """2026.11 warns once per class, at class creation (import time, so no
    caplog sees it), for each old name a class provides, and installs a
    fallback that serves the ``native_*`` name from the old one.  No fallback
    means the class implements the native API itself."""
    fallback = getattr(deprecation, "_DeprecatedEntityFallback", None)
    if fallback is None:
        assert not CLIMATE_HAS_NATIVE_TEMPERATURES
        # Pre-2026.11: the compat mixin serves the old names HA reads.
        for name in ("current_temperature", "target_temperature", "temperature_unit"):
            assert inspect.getattr_static(cls, name) is vars(NativeTemperatureCompat)[name], name
        return
    for name in _NATIVE_NAMES:
        assert not isinstance(inspect.getattr_static(cls, name), fallback), name


def test_device_info_for_builds_the_releases_device_info_type() -> None:
    info = device_info_for(None, "uuid-1", "Kitchen Light", "LightControllerV2", "Kitchen")
    if dataclasses.is_dataclass(dr.DeviceInfo):
        # 2026.11+: the dataclass, not a plain mapping ("to be deprecated")
        assert isinstance(info, dr.DeviceInfo)
        assert info.as_dict() == {
            "identifiers": {(DOMAIN, "uuid-1")},
            "manufacturer": "Loxone",
            "name": "Kitchen Light",
            "model": "LightControllerV2",
            "suggested_area": "Kitchen",
        }
    else:
        assert type(info) is dict
    # Read access the rest of the integration relies on works on both shapes.
    assert info["identifiers"] == {(DOMAIN, "uuid-1")}
    assert info.get("via_device_id") is None
    assert "via_device" not in info


async def test_entity_devices_link_to_the_miniserver_by_id(hass, mock_connection, mock_entry) -> None:
    """via_device_id survives the DeviceInfo type change (CORE-20, HA 2026.9)."""
    await _setup(hass, mock_entry)
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)

    entry = entity_registry.async_get(_climate_entity_id(hass, BEDROOM_V2))
    device = device_registry.async_get(entry.device_id)
    assert device is not None
    assert device.via_device_id is not None
    host = device_registry.async_get(device.via_device_id)
    assert host is not None
    assert host.id != device.id

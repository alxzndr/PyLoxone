"""WP-3.3: device registry and identity.

Acceptance criteria (docs/review/2026-09-remediation-plan.md, WP-3.3):

  * a snapshot of the device registry after setup shows one Miniserver
    device with ``sw_version``, and one device per control carrying the
    control's own name/model/area and ``via_device`` (CORE-16, CORE-20,
    PS-17 device link);
  * the Ventilation device is *named after the fan*, not after its
    presence sub-sensor (PC-04, regression of upstream PR #513);
  * ``group.loxone_dimmers`` contains the dimmers and
    ``group.loxone_analog`` is non-empty when the ``generate_groups``
    option is on, and nothing is created when it is off (CORE-15, PC-35);
  * the WP-0.2 ``device_type`` contract test passes (PS-14 —
    tests/test_contracts.py);
  * PS-20: keep-alive and version sensors sit on the Miniserver device,
    are diagnostic and no longer store the literal string "unknown".
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from homeassistant.const import EntityCategory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.loxone import DOMAIN
from custom_components.loxone import helpers as loxone_helpers
from custom_components.loxone.binary_sensor import LoxoneDigitalSensor
from custom_components.loxone.const import (
    DEVICE_TYPE_ANALOG,
    DEVICE_TYPE_BINARY_SENSOR,
    DEVICE_TYPE_VENTILATION,
)
from custom_components.loxone.fan import LoxoneVentilation
from custom_components.loxone.helpers import (
    device_info_for,
    get_or_create_device,
    miniserver_via,
    software_version_string,
)
from custom_components.loxone.lights.colorpickers import RGBColorPicker
from custom_components.loxone.miniserver import MiniServer
from custom_components.loxone.sensor import (
    LoxoneKeepAliveSensor,
    LoxoneSensor,
    LoxoneTextSensor,
    LoxoneVersionSensor,
)

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

# Hand-read from tests/fixtures/LoxAPP3.json (the shape of
# api.structure_file); these are the literals the assertions below
# compare against — never values produced by the code under test.
MS_SERIAL = "TEST-SERIAL-0001"
MS_NAME = "PyLoxone Test Miniserver"
MS_MODEL = "Miniserver (Gen 2)"
MS_SW_VERSION = "7.1.0.28"

FAN_UUID = "63746c3a-01ac-9746-ffff-8656e2056656000172"
FAN_NAME = "Kitchen Ventilation"
FAN_ROOM = "Kitchen"
FAN_PRESENCE_UUID = "2d646464-7265-9c005-4cff-0526a55d76f4"

LCV2_UUID = "63746c3a-0183-9766-ffff-e67204c69676000131"
LCV2_NAME = "Living Light Controller"
LCV2_ROOM = "Parlour"

DIMMER_UUID = "63746c3a-019f-9616-ffff-4616c6f6e652000159"
DIMMER_NAME = "Standalone Dimmer"
# the state streams of the fixture's Standalone Dimmer (LoxAPP3.json)
DIMMER_MIN_UUID = "64696d6d6572-7661-4c75-9cff-052664696d6d696e6d696e2d6d696e"
DIMMER_MAX_UUID = "64696d6d6572-7661-4c75-9cff-052664696d6d696d6178"
DIMMER_POSITION_UUID = "64696d6d6572-7661-4c75-9cff-052664696d6d696c706f73"

TEMP_UUID = "63746c3a-0109-96d7-ffff-572617475726000009"
TEMP_NAME = "Temperature Parlour"
TEMP_ROOM = "Parlour"


def _fixture_ventilation_control() -> dict:
    """The fixture's Ventilation control, with room/cat resolved like
    the platform does (add_room_and_cat_to_value_values)."""
    cfg = json.loads((FIXTURES_DIR / "LoxAPP3.json").read_text())
    control = next(c for c in cfg["controls"].values() if c["type"] == "Ventilation")
    return dict(control, room=FAN_ROOM, cat="")


async def _setup_entry(hass, mock_entry) -> None:
    mock_entry.add_to_hass(hass)
    ok = await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()
    assert ok, "entry setup failed"
    assert mock_entry.state is ConfigEntryState.LOADED


def _find_device(device_registry, identifier: tuple[str, str]):
    for device in device_registry.devices.values():
        if identifier in device.identifiers:
            return device
    return None


# --------------------------------------------------------------------------- #
# CORE-16: type-aware software version (string values must not be character-split)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (["7", "1", "0", "28"], "7.1.0.28"),
        ("7.1.0", "7.1.0"),
        (["7", "1", "0"], "7.1.0"),
        (7, "7"),
        (None, ""),
        ("", ""),
    ],
)
def test_software_version_string(raw, expected):
    # The old ``".".join([str(x) for x in version])`` turned the string
    # "7.1.0" into "7..1..0" (one dot inserted between each character).
    assert software_version_string(raw) == expected


@pytest.mark.parametrize(
    ("json", "expected"),
    [
        ({"softwareVersion": ["7", "1", "0", "28"]}, "7.1.0.28"),
        ({"softwareVersion": "7.1.0"}, "7.1.0"),
        ({}, ""),
    ],
)
def test_mini_server_software_version_property(json, expected):
    server = MiniServer(hass=None, lox_config=json, config_entry=None)
    assert server.software_version == expected


# --------------------------------------------------------------------------- #
# CORE-20: no module-level cache, fresh payloads, via_device
# --------------------------------------------------------------------------- #
def test_no_module_level_device_cache():
    assert not hasattr(loxone_helpers, "device_registry"), (
        "the module-level ``device_registry`` dict must be gone (CORE-20)"
    )


def test_get_or_create_device_returns_fresh_objects():
    a = get_or_create_device("uuid-1", "A", "Gate", "")
    b = get_or_create_device("uuid-1", "B", "Window", "")
    # CORE-20: no shared dict — a first writer cannot poison siblings.
    assert a is not b
    assert a["name"] == "A"
    assert b["name"] == "B"
    assert a["identifiers"] == {("loxone", "uuid-1")}


def test_device_info_for_via_linking():
    entry = SimpleNamespace(loxone_via=("loxone", MS_SERIAL))
    info = device_info_for(entry, "uuid-9", "Name", "Ventilation", "Kitchen")
    info2 = device_info_for(entry, "uuid-9", "Name", "Ventilation", "Kitchen")
    assert info is not info2, "device_info_for must build a fresh payload per call"
    assert info["identifiers"] == {("loxone", "uuid-9")}
    assert info["name"] == "Name"
    assert info["model"] == "Ventilation"
    assert info["suggested_area"] == "Kitchen"
    assert info["via_device"] == ("loxone", MS_SERIAL)
    assert info2["via_device"] == ("loxone", MS_SERIAL)

    assert miniserver_via(None) is None
    assert miniserver_via(SimpleNamespace()) is None
    # without an entry (or without a stamped via tuple) there is no link
    assert "via_device" not in device_info_for(None, "u", "n", "m")


def test_device_info_for_rejects_missing_uuid():
    # PC-05: an identifier of None is what detached standalone pickers
    # from every other entity; the factory must refuse rather than emit
    # (DOMAIN, None) identifiers.
    with pytest.raises(ValueError):
        device_info_for(None, None, "n", "m")
    with pytest.raises(ValueError):
        device_info_for(None, "", "n", "m")


# --------------------------------------------------------------------------- #
# PC-04: parent-first construction, unique ids never overwritten (PR #513)
# --------------------------------------------------------------------------- #
def test_ventilation_device_named_after_fan_not_presence():
    cfg = _fixture_ventilation_control()
    cfg["async_add_devices"] = lambda *_: None

    # fan.py now constructs the parent first and hands its device payload
    # to the sub-sensors; replicate that order here.
    fan = LoxoneVentilation(**cfg)
    presence = LoxoneDigitalSensor(
        parent_id=FAN_UUID,
        uuidAction=FAN_PRESENCE_UUID,
        type="presence",
        room=FAN_ROOM,
        cat="",
        name=f"{FAN_NAME} - Presence",
        states={"active": FAN_PRESENCE_UUID},
        device_info=fan._attr_device_info,
        config_entry=None,
    )

    # regression (PR #513): the device is named after the fan and typed
    # with the fan's model, *not* "<fan> - Presence" / "presence".
    assert fan._attr_device_info["name"] == FAN_NAME
    assert fan._attr_device_info["model"] == DEVICE_TYPE_VENTILATION
    assert fan._attr_device_info["identifiers"] == {("loxone", FAN_UUID)}
    assert fan._attr_device_info["suggested_area"] == FAN_ROOM

    # the presence sub-sensor keeps its *state uuid* as unique id — the
    # old code overwrote uuidAction with the parent id, so its unique_id
    # collided with the fan's.
    assert presence.unique_id == FAN_PRESENCE_UUID
    assert presence.unique_id != FAN_UUID

    # and it shares the parent's device (fresh payload, same identifiers).
    assert presence._attr_device_info is fan._attr_device_info
    assert presence._attr_device_info["identifiers"] == {("loxone", FAN_UUID)}


# --------------------------------------------------------------------------- #
# PC-05: standalone colour pickers register a real identifier
# --------------------------------------------------------------------------- #
def test_standalone_color_picker_device_identifier():
    uuid = "63746c3a-standalone-picker"
    picker = RGBColorPicker(
        name="Standalone RGB",
        uuidAction=uuid,
        room="",
        cat="",
        states={"color": f"{uuid}-state", "sequence": f"{uuid}-seq"},
        async_add_devices=lambda *_: None,
        config_entry=None,
    )
    info = picker._attr_device_info
    assert info["identifiers"] == {("loxone", uuid)}, (
        "a standalone picker must be identified by its own uuidAction, never None"
    )
    assert info["name"] == "Standalone RGB"
    assert info["model"] == "ColorPickerV2"


# --------------------------------------------------------------------------- #
# PS-10 / PC-35: device_class never returns Loxone control-type strings
# --------------------------------------------------------------------------- #
def test_fan_device_class_is_default_none():
    assert "device_class" not in vars(LoxoneVentilation), (
        "LaxoneVentilation's dead device_class property/setter must be deleted"
    )


def test_text_sensor_device_class_is_default_none():
    sensor = LoxoneTextSensor(
        name="Note",
        uuidAction="uuid-txt",
        room="",
        cat="",
        states={"text": "uuid-txt-text"},
        config_entry=None,
    )
    assert "device_class" not in vars(LoxoneTextSensor)
    assert sensor.device_class is None
    assert sensor.type == "TextInput"


# --------------------------------------------------------------------------- #
# PS-20: version sensor value/not-unknown, category, default disabled keepalive
# --------------------------------------------------------------------------- #
def test_version_sensor_values():
    assert LoxoneVersionSensor("s1", ["7", "1", "0", "28"])._attr_native_value == "7.1.0.28"
    # hand-derived: the string form must pass through untouched — the old
    # character join of "7.1.0" is "7..1..0".
    assert LoxoneVersionSensor("s1", "7.1.0")._attr_native_value == "7.1.0"
    assert LoxoneVersionSensor("s2", None)._attr_native_value is None
    assert LoxoneVersionSensor("s3", "7.1.0").entity_category == EntityCategory.DIAGNOSTIC


def test_keep_alive_sensor_is_diagnostic_and_disabled_by_default():
    entity = LoxoneKeepAliveSensor("serial-x", None)
    assert entity.entity_category == EntityCategory.DIAGNOSTIC
    # hidden in the UI by default: the registry entry will be created disabled
    assert entity._attr_entity_registry_enabled_default is False
    assert entity._attr_native_value is None


# --------------------------------------------------------------------------- #
# PS-14: the platform emits exactly the group-table constants
# --------------------------------------------------------------------------- #
def test_device_type_constants_are_literals():
    # PS-14: both sides of the group table must carry these same strings.
    # (values hand-read from const.py)
    assert DEVICE_TYPE_ANALOG == "Sensor analog"
    assert DEVICE_TYPE_BINARY_SENSOR == "digital_sensor"
    assert DEVICE_TYPE_VENTILATION == "Ventilation"


def test_analog_sensor_type_is_the_group_constant():
    sensor = LoxoneSensor(
        name=TEMP_NAME,
        uuidAction=TEMP_UUID,
        room=TEMP_ROOM,
        cat="",
        states={"active": "other-uuid"},
        details={"format": "%.1f °C"},
        config_entry=None,
    )
    assert sensor.type == DEVICE_TYPE_ANALOG
    assert sensor._attr_device_info["model"] == DEVICE_TYPE_ANALOG
    # the old extras value appended "_sensor", which nothing matched.
    assert sensor.extra_state_attributes["device_type"] == "Sensor analog"


# --------------------------------------------------------------------------- #
# CORE-17: dead dispatcher wiring is gone
# --------------------------------------------------------------------------- #
def test_dead_dispatcher_wiring_is_deleted():
    import custom_components.loxone.miniserver as ms_module

    assert not hasattr(MiniServer, "async_signal_new_device")
    assert not hasattr(ms_module, "NEW_COVERS")
    assert not hasattr(ms_module, "NEW_SENSOR")
    # and MiniServer no longer carries the never-iterated listener list:
    server = MiniServer.__new__(MiniServer)
    assert not hasattr(server, "listeners")


# --------------------------------------------------------------------------- #
# CORE-16 / CORE-20: the device registry after a full setup
# --------------------------------------------------------------------------- #
async def test_device_registry_snapshot_after_setup(hass, mock_connection, mock_entry, enable_custom_integrations):
    await _setup_entry(hass, mock_entry)
    device_registry = dr.async_get(hass)

    # one Miniserver host device, with its real identity fields and no
    # fake MAC connection from the host IP (CORE-16).
    ms_device = _find_device(device_registry, (DOMAIN, MS_SERIAL))
    assert ms_device is not None, "the Miniserver host device was not registered"
    assert ms_device.name == MS_NAME
    assert ms_device.model == MS_MODEL
    assert ms_device.sw_version == MS_SW_VERSION
    # no fake MAC connection built from the host IP (CORE-16)
    assert ("mac", "loxberry.local") not in (ms_device.connections or set())

    # one device per control: the fan carries its own name/model/area and
    # hangs off the Miniserver device (PC-04 regression, PR #513).
    fan_device = _find_device(device_registry, (DOMAIN, FAN_UUID))
    assert fan_device is not None
    assert fan_device.name == FAN_NAME
    assert fan_device.model == DEVICE_TYPE_VENTILATION
    assert fan_device.suggested_area == FAN_ROOM
    assert fan_device.via_device_id == ms_device.id

    lcv2_device = _find_device(device_registry, (DOMAIN, LCV2_UUID))
    assert lcv2_device is not None
    assert lcv2_device.name == LCV2_NAME
    assert lcv2_device.model == "LightControllerV2"
    assert lcv2_device.suggested_area == LCV2_ROOM
    assert lcv2_device.via_device_id == ms_device.id

    dimmer_device = _find_device(device_registry, (DOMAIN, DIMMER_UUID))
    assert dimmer_device is not None
    assert dimmer_device.name == DIMMER_NAME
    assert dimmer_device.model == "Dimmer"
    assert dimmer_device.via_device_id == ms_device.id

    temp_device = _find_device(device_registry, (DOMAIN, TEMP_UUID))
    assert temp_device is not None
    assert temp_device.name == TEMP_NAME
    assert temp_device.model == DEVICE_TYPE_ANALOG
    assert temp_device.suggested_area == TEMP_ROOM
    assert temp_device.via_device_id == ms_device.id

    # the pre-fix symptom: the first sub-sensor written seeded the shared
    # dict with its own name — that device must no longer exist.
    for device in device_registry.devices.values():
        assert device.name != f"{FAN_NAME} - Presence"


# --------------------------------------------------------------------------- #
# CORE-16: entry unique_id becomes the serial (config flow never set it)
# --------------------------------------------------------------------------- #
async def test_entry_unique_id_is_set_to_the_serial(hass, mock_connection, enable_custom_integrations):
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=4,
        data={},
        options=dict(
            host="loxberry.local",
            port=8080,
            username="admin",
            password="secret",
            verify_ssl=True,
            generate_scenes=False,
            generate_scenes_delay=3,
            generate_lightcontroller_subcontrols=False,
            generate_groups=False,
        ),
        unique_id=None,
    )
    entry.add_to_hass(hass)
    ok = await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert ok
    assert entry.unique_id == MS_SERIAL


# --------------------------------------------------------------------------- #
# CORE-15 / PC-35: auto-groups, gated on generate_groups
# --------------------------------------------------------------------------- #
async def test_groups_created_for_installed_entry(hass, mock_connection, mock_entry, enable_custom_integrations):
    import custom_components.loxone as lx

    # mock_entry never sets the option: installed entries keep groups on.
    assert "generate_groups" not in mock_entry.options
    mock_entry.add_to_hass(hass)
    ok = await hass.config_entries.async_setup(mock_entry.entry_id)
    assert ok
    await hass.async_block_till_done()

    # phase 1: groups are created from the entities that are available at
    # group-creation time.  (The pre-fix symptom of the *upstream* bug was
    # that "Loxone Dimmer" got the `lights` list while `dimmers` sat
    # unused and three table literals matched nothing at all.)
    analog = hass.states.get("group.loxone_analog")
    assert analog is not None, "group.loxone_analog was never created"
    assert len(analog.attributes.get("entity_id", [])) >= 1

    digital = hass.states.get("group.loxone_digital")
    assert digital is not None
    assert len(digital.attributes.get("entity_id", [])) >= 1

    master = hass.states.get("group.loxone_group")
    assert master is not None
    master_members = master.attributes.get("entity_id", [])
    assert "group.loxone_analog" in master_members
    assert "group.loxone_climates" in master_members
    assert "group.loxone_covers" in master_members

    # phase 2: the fixture's standalone dimmer only becomes available once
    # its dimming states have been streamed (its pre-stream state carries
    # no user extras).  After it is up, a group re-creation — e.g. the
    # user deleted the groups — must pick the dimmer up, and the master
    # group must include it (CORE-15: the old master dropped the dimmer
    # group entirely).
    mock_connection.feed(DIMMER_MIN_UUID, 0)
    mock_connection.feed(DIMMER_MAX_UUID, 100)
    mock_connection.feed(DIMMER_POSITION_UUID, 50)
    await hass.async_block_till_done()
    dimmer_state = hass.states.get("light.bedroom_standalone_dimmer")
    assert dimmer_state is not None and dimmer_state.attributes.get("device_type") == "Dimmer"

    # Reset: the groups (bare states of the `group` component, not entity
    # registry entries in this HA version) must go, so the master-state
    # idempotence gate lets the re-creation run.
    for state in hass.states.async_all("group"):
        hass.states.async_remove(state.entity_id)
    await hass.async_block_till_done()
    assert hass.states.get("group.loxone_group") is None, "groups test: stale master survived the reset"

    await lx.create_loxone_groups(hass, mock_entry)
    await hass.async_block_till_done()

    dimmers_state = hass.states.get("group.loxone_dimmers")
    assert dimmers_state is not None, "group.loxone_dimmers was never created"
    assert set(dimmers_state.attributes.get("entity_id", ())) == {"light.bedroom_standalone_dimmer"}

    master = hass.states.get("group.loxone_group")
    assert master is not None
    master_members = master.attributes.get("entity_id", [])
    assert "group.loxone_dimmers" in master_members
    assert "group.loxone_climates" in master_members
    assert "group.loxone_covers" in master_members
    assert "group.loxone_analog" in master_members


async def test_groups_skipped_when_option_is_off(hass, mock_connection, mock_entry, enable_custom_integrations):
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=4,
        data={},
        options={
            "host": "loxberry.local",
            "port": 8080,
            "username": "admin",
            "password": "secret",
            "verify_ssl": True,
            "generate_scenes": False,
            "generate_scenes_delay": 3,
            "generate_lightcontroller_subcontrols": False,
            # new installs are stamped with the option off by the config flow
            "generate_groups": False,
        },
        unique_id="TEST-SERIAL-0002",
    )
    await _setup_entry(hass, entry)
    await hass.async_block_till_done()
    assert hass.states.get("group.loxone_group") is None
    assert hass.states.get("group.loxone_analog") is None
    assert hass.states.get("group.loxone_dimmers") is None


# --------------------------------------------------------------------------- #
# PS-20 (harness): version sensor on the Miniserver device, diagnostic, real value
# --------------------------------------------------------------------------- #
async def test_version_sensor_on_miniserver_device_diagnostic(
    hass, mock_connection, mock_entry, enable_custom_integrations
):
    await _setup_entry(hass, mock_entry)
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    ms_device = _find_device(device_registry, (DOMAIN, MS_SERIAL))

    by_unique = {}
    for entry in er.async_entries_for_config_entry(entity_registry, mock_entry.entry_id):
        by_unique[entry.unique_id] = entry

    version_entry = by_unique.get(f"{MS_SERIAL}-loxone_software_version_uuid")
    assert version_entry is not None
    assert version_entry.entity_category == EntityCategory.DIAGNOSTIC
    assert version_entry.device_id == ms_device.id
    version_state = hass.states.get(version_entry.entity_id)
    assert version_state is not None
    # hand-derived from the fixture's ``softwareVersion: ["7","1","0","28"]``
    assert version_state.state == MS_SW_VERSION

    keep_alive_entry = by_unique.get(f"{MS_SERIAL}-loxone_keep_alive_sensor_uuid")
    assert keep_alive_entry is not None
    assert keep_alive_entry.device_id == ms_device.id
    assert keep_alive_entry.entity_category == EntityCategory.DIAGNOSTIC
    # disabled by default: no state for it
    assert keep_alive_entry.disabled or hass.states.get(keep_alive_entry.entity_id) is None

"""WP-4.2 regression tests: cover platform (Jalousie / Gate / Window).

Expected values are hand-derived literals — e.g. the server's 0.0–1.0
position scale is scaled to 0–100 (fed ``0.25`` → state ``25.0``), and
`map_range(p, 0, 100, 100, 0)` is the mirror ``100 - p``, so a Loxone-side
position of 40.0 (hass 60.0) must be sent back on `set_cover_position` as
``manualPosition/40.0``.

Items marked VERIFY in the catalogue are pinned here to their *intended*
semantics behind the named helpers (`gate_stop_command`, the fixed-format
`manualLamelle` payloads); a live-Miniserver check is required before
relying on them (see the PR body).
"""

from __future__ import annotations

import copy

import pytest
from homeassistant.helpers import entity_registry as er
from homeassistant.components.cover import CoverDeviceClass
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import ServiceValidationError

from custom_components.loxone.const import SENDDOMAIN, SUPPORT_QUICK_SHADE, SUPPORT_SUN_AUTOMATION
from custom_components.loxone.cover import (
    LoxoneGate,
    LoxoneJalousie,
    LoxoneWindow,
    gate_device_class,
    gate_stop_command,
    jalousie_device_class,
)
from custom_components.loxone.helpers import map_range

UUID_ACTION = "U68KEE6-COV-0001-0000-000000000001"
POS_UUID = "36333734-01c1-9336-ffff-d30316c676c3000101"
ACTIVE_UUID = "36333734-01c1-9336-ffff-d30316c676c3000102"
DIR_UUID = "36333734-01c1-9336-ffff-d30316c676c3000103"
TARGET_UUID = "36333734-01c1-9336-ffff-d30316c676c3000104"
SHADE_UUID = "36333734-01c1-9336-ffff-d30316c676c3000105"
UP_UUID = "36333734-01c1-9336-ffff-d30316c676c3000106"
DOWN_UUID = "36333734-01c1-9336-ffff-d30316c676c3000107"
AUTO_TEXT_UUID = "36333734-01c1-9336-ffff-d30316c676c3000108"
AUTO_STATE_UUID = "36333734-01c1-9336-ffff-d30316c676c3000109"


def _jalousie(**overrides) -> LoxoneJalousie:
    kwargs = {
        "hass": None,
        "name": "Test Jalousie",
        "nameRu": "Test Jalousie",
        "uuidAction": UUID_ACTION,
        "room": "",
        "type": "jalousie",
        "details": {"animation": 0},
        "states": {
            "position": POS_UUID,
            "shadePosition": SHADE_UUID,
            "up": UP_UUID,
            "down": DOWN_UUID,
            "autoInfoText": AUTO_TEXT_UUID,
            "autoState": AUTO_STATE_UUID,
            "targetPosition": TARGET_UUID,
            "active": ACTIVE_UUID,
        },
        "isSecured": False,
    }
    kwargs.update(overrides)
    return LoxoneJalousie(**kwargs)


def _gate(**overrides) -> LoxoneGate:
    kwargs = {
        "hass": None,
        "name": "Test Gate",
        "nameRu": "Test Gate",
        "uuidAction": "U68KEE6-COV-0001-0000-000000000002",
        "room": "",
        "type": "gate",
        "details": {"animation": 1},
        "states": {"position": POS_UUID, "active": ACTIVE_UUID},
        "isSecured": False,
    }
    kwargs.update(overrides)
    return LoxoneGate(**kwargs)


def _window(**overrides) -> LoxoneWindow:
    kwargs = {
        "hass": None,
        "name": "Test Window",
        "nameRu": "Test Window",
        "uuidAction": "U68KEE6-COV-0001-0000-000000000003",
        "room": "",
        "type": "window",
        "details": {},
        "states": {"position": POS_UUID, "active": ACTIVE_UUID, "direction": DIR_UUID},
        "isSecured": False,
    }
    kwargs.update(overrides)
    return LoxoneWindow(**kwargs)


def _hass_write_stub(e) -> None:
    e.async_schedule_update_ha_state = lambda *a, **k: None
    e.schedule_update_ha_state = lambda *a, **k: None


async def _fan_bus(hass):
    fired = []
    hass.bus.async_listen(SENDDOMAIN, lambda ev: fired.append(ev.data))
    return fired


# ===========================================================================
# PC-07 stop_cover
# ===========================================================================


def test_gate_stop_command_is_stop() -> None:
    """PC-07 (VERIFY — intended semantics): a real stop, not the reversal."""
    assert gate_stop_command() == "stop"


async def test_window_stop_sends_stop_regardless_of_direction(hass) -> None:
    """PC-07: stop must be `stop` whether closing or opening (#501)."""
    e = _window()
    e.hass = hass
    _hass_write_stub(e)
    fired = await _fan_bus(hass)

    e.event_handler({DIR_UUID: -1})  # closing
    e.stop_cover()
    e.event_handler({DIR_UUID: 1})  # opening
    e.stop_cover()
    await hass.async_block_till_done()

    assert fired == [
        {"uuid": "U68KEE6-COV-0001-0000-000000000003", "value": "stop"},
        {"uuid": "U68KEE6-COV-0001-0000-000000000003", "value": "stop"},
    ]


async def test_gate_stop_sends_stop_regardless_of_direction(hass) -> None:
    """PC-07: the old opposite-direction logic is gone for Gates as well."""
    e = _gate()
    e.hass = hass
    _hass_write_stub(e)
    fired = await _fan_bus(hass)

    e.event_handler({ACTIVE_UUID: -1})  # closing
    e.stop_cover()
    e.event_handler({ACTIVE_UUID: 1})  # opening
    e.stop_cover()
    await hass.async_block_till_done()

    assert fired == [
        {"uuid": "U68KEE6-COV-0001-0000-000000000002", "value": "stop"},
        {"uuid": "U68KEE6-COV-0001-0000-000000000002", "value": "stop"},
    ]


# ===========================================================================
# PC-14 services: required_features + async methods
# ===========================================================================


@pytest.mark.parametrize(
    ("service", "uuid_expected"),
    [
        ("enable_sun_automation", "auto"),
        ("disable_sun_automation", "NoAuto"),
        ("quick_shade", "shade"),
    ],
)
async def test_jalousie_services_fire_their_commands(hass, service, uuid_expected) -> None:
    """PC-14: the three Jalousie services are async and fire on the loop."""
    e = _jalousie(details={"animation": 0, "isAutomatic": True})
    e.hass = hass
    _hass_write_stub(e)
    fired = await _fan_bus(hass)

    await getattr(e, service)()
    await hass.async_block_till_done()

    assert fired == [{"uuid": UUID_ACTION, "value": uuid_expected}]


def test_jalousie_had_quick_shade_bit_when_tilt_known() -> None:
    """SUPPORT_QUICK_SHADE only appears once tilt + BLIND are both known."""
    e = _jalousie(details={"animation": 0})
    assert (e.supported_features & SUPPORT_SUN_AUTOMATION) == 0  # not automatic
    assert (e.supported_features & SUPPORT_QUICK_SHADE) == 0  # no tilt yet


async def test_jalousie_quick_shade_bit_appears_for_blind_with_tilt(hass) -> None:
    """A BLIND that reports a shade position advertises QUICK_SHADE."""
    e = _jalousie(details={"animation": 0})
    e.hass = hass
    _hass_write_stub(e)
    fired = await _fan_bus(hass)

    assert (e.supported_features & SUPPORT_QUICK_SHADE) == 0  # tilt unknown yet
    e.event_handler({SHADE_UUID: 0.5})  # → tilt 50.0
    assert (e.supported_features & SUPPORT_QUICK_SHADE) != 0

    await e.quick_shade()
    await hass.async_block_till_done()

    assert fired == [{"uuid": UUID_ACTION, "value": "shade"}]


# ===========================================================================
# PC-15 animation detail missing → no KeyError
# ===========================================================================


@pytest.mark.parametrize(
    ("details", "expected_device_class"),
    [
        ({}, CoverDeviceClass.BLIND),  # no `animation` at all
        ({"animation": 0}, CoverDeviceClass.BLIND),
        ({"animation": 1}, CoverDeviceClass.SHUTTER),
        ({"animation": 6}, CoverDeviceClass.AWNING),
        ({"animation": 99}, None),
    ],
)
def test_jalousie_device_class_without_animation_does_not_raise(details, expected_device_class) -> None:
    """PC-15: `device_class` used to `KeyError` on `details["animation"]`."""
    e = _jalousie(details=details)
    assert e.device_class == expected_device_class
    # `extra_state_attributes` reads `device_class` too:
    assert e.extra_state_attributes["device_type"] == "Jalousie"


def test_gate_device_class_without_animation_does_not_raise() -> None:
    e = _gate(details={})
    assert e.device_class == CoverDeviceClass.GARAGE


# ===========================================================================
# animation → device_class tables (hand-derived)
# ===========================================================================


@pytest.mark.parametrize(
    ("animation", "expected"),
    [
        (0, CoverDeviceClass.GARAGE),
        (1, CoverDeviceClass.GATE),
        (2, CoverDeviceClass.GATE),
        (3, CoverDeviceClass.GATE),
        (4, CoverDeviceClass.DOOR),
        (5, CoverDeviceClass.DOOR),
        (6, None),  # unknown value must not leak the type string
        (None, None),
    ],
)
def test_gate_device_class_table(animation, expected) -> None:
    assert gate_device_class(animation) == expected


@pytest.mark.parametrize(
    ("animation", "expected"),
    [
        (0, CoverDeviceClass.BLIND),
        (1, CoverDeviceClass.SHUTTER),
        (2, CoverDeviceClass.CURTAIN),
        (3, CoverDeviceClass.SHUTTER),
        (4, CoverDeviceClass.CURTAIN),
        (5, CoverDeviceClass.CURTAIN),
        (6, CoverDeviceClass.AWNING),
        (7, None),
        (None, None),
    ],
)
def test_jalousie_device_class_table(animation, expected) -> None:
    assert jalousie_device_class(animation) == expected


# ===========================================================================
# PC-16 missing optional state uuids
# ===========================================================================


def test_gate_without_position_state_construction_does_not_raise() -> None:
    """PC-16: a Gate whose structure file lacks `position` must still build."""
    e = _gate(states={})
    assert e.current_cover_position is None
    assert e.is_closed is True


def test_window_without_target_position_or_direction_construction_does_not_raise() -> None:
    """PC-16 (#501): Windows commonly lack `direction`/`targetPosition`."""
    e = _window(states={"position": POS_UUID})
    assert e.target_position is None
    assert e.is_opening is False
    assert e.is_closing is False


async def test_jalousie_event_handler_survives_missing_state_uuids(hass) -> None:
    """PC-16: a Jalousie without shade/auto/timer states must not crash."""
    e = _jalousie(
        states={"position": POS_UUID},
        details={},
    )
    e.hass = hass
    _hass_write_stub(e)

    e.event_handler({POS_UUID: 0.25})
    assert e.current_cover_position == 75.0  # 0.25 → loxone 25.0 → hass 100-25.0
    assert e.current_cover_tilt_position is None
    assert e.target_position is None
    assert not e.is_opening and not e.is_closing

    # A brand-new structure event on an unknown uuid: nothing happens.
    e.event_handler({"nonexistent-uuid": 1})
    assert e.current_cover_position == 75.0


# ===========================================================================
# PC-36 shared structure dict must not be mutated
# ===========================================================================


def test_jalousie_construction_does_not_mutate_structure(loxapp3) -> None:
    """PC-36: no `autoInfoText`/`autoState` `""` keys are injected."""
    control = next(c for c in loxapp3["controls"].values() if c["type"] == "Jalousie")
    control_copy = copy.deepcopy(control)
    states_before = copy.deepcopy(control_copy["states"])
    details_before = copy.deepcopy(control_copy["details"])

    LoxoneJalousie(hass=None, **control_copy)

    assert control_copy["states"] == states_before
    assert control_copy["details"] == details_before


# ===========================================================================
# position inversion (map_range mirror identity)
# ===========================================================================


@pytest.mark.parametrize("position", [0.0, 10.0, 37.41, 100.0])
def test_position_inversion_identity(position) -> None:
    """`map_range(p, 0,100,100,0)` is `100 - p`, so it is an involution."""
    inverted = map_range(position, 0, 100, 100, 0)
    assert inverted == pytest.approx(100.0 - position)
    assert map_range(inverted, 0, 100, 100, 0) == pytest.approx(position)


async def test_jalousie_set_cover_position_sends_inverted_loxone_value(hass) -> None:
    """A server position of 0.4 (loxone 40.0 / hass 60.0) goes back as 40.0."""
    e = _jalousie(details={"animation": 0})
    e.hass = hass
    _hass_write_stub(e)
    fired = await _fan_bus(hass)

    e.event_handler({POS_UUID: 0.4})
    assert e.current_cover_position == 60.0  # 0.4 → 40.0 → hass 100-40.0

    e.set_cover_position(**{"position": 60.0})
    await hass.async_block_till_done()

    assert fired == [
        {"uuid": UUID_ACTION, "value": "manualPosition/40.0"},
    ]


# ===========================================================================
# PC-19 explicit command formatting (manualLamelle)
# ===========================================================================


async def test_open_close_tilt_send_fixed_format_lamelle_commands(hass) -> None:
    """PC-19: fixed 3-decimal format; the jitter stays below 0.0091."""
    e = _jalousie(details={"animation": 0})
    e.hass = hass
    _hass_write_stub(e)
    fired = await _fan_bus(hass)

    e.open_cover_tilt()
    e.close_cover_tilt()
    await hass.async_block_till_done()

    assert len(fired) == 2
    open_value = fired[0]["value"]
    close_value = fired[1]["value"]
    # fixed 3-decimal format (previously e.g. `manualLamelle/0.004233015...`)
    assert open_value.startswith("manualLamelle/")
    assert close_value.startswith("manualLamelle/")
    open_pos = float(open_value.removeprefix("manualLamelle/"))
    close_pos = float(close_value.removeprefix("manualLamelle/"))
    assert 0.0 <= open_pos <= 0.0091  # "slat open"
    assert 100.0 <= close_pos <= 100.0091  # "slat closed"


# ===========================================================================
# full setup with the LoxAPP3 fixture
# ===========================================================================


def _cover_state_ids(hass, loxapp3) -> dict[str, str]:
    """Map Loxone control type -> entity_id, via the entity registry.

    The state machine cannot be used for this. Once entity availability
    follows the connection (CORE-28/API-09), a cover may still be
    `unavailable` at assert time, and Home Assistant does not publish
    extra_state_attributes for an unavailable entity -- so `device_type`
    would be missing for reasons unrelated to what these tests check.
    The registry records every entity that was created, regardless of state.
    """
    type_by_uuid = {
        control["uuidAction"]: control["type"]
        for control in loxapp3["controls"].values()
        if control.get("type") in ("Gate", "Window", "Jalousie")
    }
    registry = er.async_get(hass)
    collected = {}
    for entry in registry.entities.values():
        if entry.domain != "cover":
            continue
        control_type = type_by_uuid.get(entry.unique_id)
        if control_type:
            collected[control_type] = entry.entity_id
    return collected


async def test_window_without_target_position_sets_up_and_updates(hass, loxapp3, mock_connection, mock_entry) -> None:
    """PC-16 acceptance (#501): the fixture Window has no `targetPosition`."""
    window_control = next(c for c in loxapp3["controls"].values() if c["type"] == "Window")
    assert "targetPosition" not in window_control["states"]
    assert "direction" not in window_control["states"]
    position_uuid = window_control["states"]["position"]

    mock_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_entry.entry_id)
    assert hass.config_entries.async_get_entry(mock_entry.entry_id).state is ConfigEntryState.LOADED

    ids = _cover_state_ids(hass, loxapp3)
    assert "Window" in ids, "no Window entity was set up"

    # Set up: without a `direction`/`targetPosition` state the Window used to
    # raise KeyError on the very first event.
    mock_connection.feed(position_uuid, 0.25)  # 0.25 → 25.0
    await hass.async_block_till_done()

    st = hass.states.get(ids["Window"])
    # 0.25 → 25.0 (`current_position` is HA's state attribute name)
    assert st.attributes["current_position"] == 25.0
    assert st.state == "open"  # position > 0
    assert st.attributes["target_position"] is None

    mock_connection.feed(position_uuid, 0.0)
    await hass.async_block_till_done()
    assert hass.states.get(ids["Window"]).state == "closed"


async def test_quick_shade_on_gate_raises_service_validation_error(hass, loxapp3, mock_connection, mock_entry) -> None:
    """PC-14 acceptance: required_features, not AttributeError."""
    mock_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_entry.entry_id)
    assert hass.config_entries.async_get_entry(mock_entry.entry_id).state is ConfigEntryState.LOADED

    ids = _cover_state_ids(hass, loxapp3)
    assert set(ids) == {"Gate", "Window", "Jalousie"}

    for target, service in (
        (ids["Gate"], "quick_shade"),
        (ids["Gate"], "enable_sun_automation"),
        (ids["Window"], "quick_shade"),
        (ids["Window"], "disable_sun_automation"),
        # the fixture Jalousie is a SHUTTER (animation) without slats → no
        # QUICK_SHADE bit either
        (ids["Jalousie"], "quick_shade"),
        (ids["Jalousie"], "enable_sun_automation"),
    ):
        with pytest.raises(ServiceValidationError):
            await hass.services.async_call(
                "loxone",
                service,
                {"entity_id": target},
                blocking=True,
            )
            await hass.async_block_till_done()


@pytest.fixture
def _jalousie_control_key(loxapp3):
    """Return the fixture Jalousie's key so it can be restored."""
    return next(k for k, c in loxapp3["controls"].items() if c["type"] == "Jalousie")


@pytest.fixture
def _automatic_jalousie(loxapp3, _jalousie_control_key):
    """Give the fixture Jalousie sun automation + slat states."""
    jalousie = loxapp3["controls"][_jalousie_control_key]
    jalousie["details"]["isAutomatic"] = True
    jalousie["states"]["autoInfoText"] = AUTO_TEXT_UUID
    jalousie["states"]["autoState"] = AUTO_STATE_UUID
    jalousie["states"]["targetPosition"] = TARGET_UUID
    jalousie["states"]["shadePosition"] = SHADE_UUID
    jalousie["states"]["up"] = UP_UUID
    jalousie["states"]["down"] = DOWN_UUID
    yield jalousie
    # Restore: drop exactly the keys this fixture added (never copy the control
    # back wholesale — a prior test run may have added a live `hass` key).
    del jalousie["details"]["isAutomatic"]
    for key in ("autoInfoText", "autoState", "targetPosition", "shadePosition", "up", "down"):
        jalousie["states"].pop(key, None)


async def test_sun_automation_service_fires_auto_on_automatic_jalousie(
    hass, loxapp3, mock_connection, mock_entry, _automatic_jalousie, _jalousie_control_key
) -> None:
    """The entity service reaches an automatic Jalousie and fires `auto`."""
    jalousie = _automatic_jalousie
    mock_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_entry.entry_id)
    assert hass.config_entries.async_get_entry(mock_entry.entry_id).state is ConfigEntryState.LOADED

    # WP-3.2: commands travel via the entry's own Miniserver API, not the
    # outbound bus — assert on the recorded websocket send.
    await hass.services.async_call(
        "loxone",
        "enable_sun_automation",
        {"entity_id": _cover_state_ids(hass, loxapp3)["Jalousie"]},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert mock_connection.sent == [
        {"uuid": jalousie["uuidAction"], "value": "auto", "code": None},
    ]


@pytest.fixture
def _autostate_jalousie(loxapp3, _jalousie_control_key):
    """Copy of the fixture Jalousie's `states`/`details` before setup."""
    jalousie = loxapp3["controls"][_jalousie_control_key]
    return {
        "states": copy.deepcopy(jalousie["states"]),
        "details": copy.deepcopy(jalousie["details"]),
    }


async def test_setup_does_not_mutate_cover_structure_dicts(
    hass, loxapp3, mock_connection, mock_entry, _autostate_jalousie, _jalousie_control_key
) -> None:
    """PC-36 acceptance: the shared `states`/`details` dicts are untouched by setup."""
    loxapp3["controls"][_jalousie_control_key].pop("hass", None)  # leftover of a prior test
    jalousie = _autostate_jalousie
    mock_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_entry.entry_id)
    assert hass.config_entries.async_get_entry(mock_entry.entry_id).state is ConfigEntryState.LOADED

    control = loxapp3["controls"][_jalousie_control_key]
    assert control["states"] == jalousie["states"]
    assert control["details"] == jalousie["details"]

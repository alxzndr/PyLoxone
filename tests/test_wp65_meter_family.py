"""WP-6.5 regression tests: the Meter family (``Meter``, ``EnergyManager``,
``EnergyManager2``, ``PowerUnit``, ``Wallbox``) shares one generalised
sub-state loop (PS-26).

All expected values are hand-derived literals against the fixture
(``tests/fixtures/LoxAPP3.json``): the fixture registers four family
members with a *different* register set each, so the guards (advertising
only the registers present on the control) are exercised, not assumed.

Fixture register map (name → states):

* ``Energy Manager`` (EnergyManager, Garden): actual/total/totalNeg/storage
* ``Energy Manager 2`` (EnergyManager2, Hall): total/totalNeg/storage
  (no ``actual`` — verifies the per-register guard)
* ``Power Unit`` (PowerUnit, Garden): actual/total/storage
* ``Wallbox Garage`` (Wallbox, Garden): actual/total
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import device_registry as dr, entity_registry as er

from custom_components.loxone.helpers import get_all
from custom_components.loxone.sensor import (
    METER_FAMILY_TYPES,
    meter_device_info,
    meter_device_model,
    meter_sub_sensor_kwargs,
)

# --------------------------------------------------------------------------- #
# Fixture control identities (read from tests/fixtures/LoxAPP3.json)
# --------------------------------------------------------------------------- #
EM_ACTION = "63746c3a-01e0-96d7-ffff-f656e657267796d0001a0"
EM_ACTUAL = "36333734-01e1-9336-ffff-d30313163796d0001a1"
EM_TOTAL = "36333734-01e2-9336-ffff-d30313163796d0001a2"
EM_TOTAL_NEG = "36333734-01e3-9336-ffff-d30313163796d0001a3"
EM_STORAGE = "36333734-01e4-9336-ffff-d30313163796d0001a4"
EM2_ACTION = "63746c3a-01e8-96d7-ffff-f656e65726779320001b0"
EM2_TOTAL = "36333734-01e9-9336-ffff-d30313163796d0001b1"
EM2_TOTAL_NEG = "36333734-01ea-9336-ffff-d30313163796d0001b2"
EM2_STORAGE = "36333734-01eb-9336-ffff-d30313163796d0001b3"
PU_ACTION = "63746c3a-01f0-96d7-ffff-f506f776572756e69740001c0"
PU_ACTUAL = "36333734-01f1-9336-ffff-d3031316575740001c1"
PU_TOTAL = "36333734-01f2-9336-ffff-d3031316575740001c2"
PU_STORAGE = "36333734-01f3-9336-ffff-d3031316575740001c3"
WB_ACTION = "63746c3a-01f8-96d7-ffff-f57616c6c626f786761720001d0"
WB_ACTUAL = "36333734-01f9-9336-ffff-d3031316626f780001d1"
WB_TOTAL = "36333734-01fa-9336-ffff-d3031316626f780001d2"


# --------------------------------------------------------------------------- #
# Pure helpers: device model string
# --------------------------------------------------------------------------- #
def test_meter_device_model_literals():
    # Legacy Meter: free-form details.type -> "<Type> Meter" (keeps the
    # historical capitalize() semantics); without it -> "Meter".
    assert meter_device_model({"type": "Meter", "details": {"type": "module meter"}}) == "Module meter Meter"
    assert meter_device_model({"type": "Meter"}) == "Meter"
    assert meter_device_model({"type": "Meter", "details": {}}) == "Meter"
    assert meter_device_model({"type": "Meter", "details": "junk"}) == "Meter"
    assert meter_device_model({"type": "Meter", "details": {"type": 7}}) == "Meter"
    # Family members: the control type name is the model.
    assert meter_device_model({"type": "EnergyManager"}) == "EnergyManager"
    assert meter_device_model({"type": "EnergyManager2"}) == "EnergyManager2"
    assert meter_device_model({"type": "PowerUnit"}) == "PowerUnit"
    assert meter_device_model({"type": "Wallbox"}) == "Wallbox"
    # A broken/missing type never crashes; it falls back to "Meter".
    assert meter_device_model({}) == "Meter"
    assert meter_device_model({"type": ""}) == "Meter"
    assert meter_device_model({"type": 42}) == "Meter"


# --------------------------------------------------------------------------- #
# Pure helpers: sub-sensor kwargs (the generalised loop body)
# --------------------------------------------------------------------------- #
def _control(**overrides) -> dict:
    control = {
        "name": "Energy Manager",
        "type": "EnergyManager",
        "uuidAction": EM_ACTION,
        "room": "Garden",
        "cat": "Energy",
        "details": {
            "actualFormat": "%d W",
            "totalFormat": "%d kWh",
            "storageFormat": "%d kWh",
        },
        "states": {
            "actual": EM_ACTUAL,
            "total": EM_TOTAL,
            "totalNeg": EM_TOTAL_NEG,
            "storage": EM_STORAGE,
        },
    }
    control.update(overrides)
    return control


def test_meter_sub_sensor_kwargs_literal():
    kwargs_list = meter_sub_sensor_kwargs(_control(), None)
    # Hand-derived: one entry per advertised register, in
    # METER_STATE_CLASSES order (actual, total, totalNeg, storage), all
    # fields literals readable from the control above.
    assert [kw["uuidAction"] for kw in kwargs_list] == [EM_ACTUAL, EM_TOTAL, EM_TOTAL_NEG, EM_STORAGE]
    names = [kw["name"] for kw in kwargs_list]
    assert names == ["Actual", "Total", "Total Neg", "Level"]
    for kw in kwargs_list:
        assert kw["parent_id"] == EM_ACTION
        assert kw["type"] == "analog"
        assert kw["room"] == "Garden"
        assert kw["cat"] == "Energy"
        assert kw["config_entry"] is None
        assert kw["device_info"] is not None  # shared device, see below
    assert [kw["details"]["format"] for kw in kwargs_list] == ["%d W", "%d kWh", "%d kWh", "%d kWh"]
    # (device_class, state_class) literals from METER_STATE_CLASSES:
    # actual -> power/measurement; total & totalNeg -> energy/total_increasing;
    # storage -> energy/measurement (PS-21).
    classes = [(str(kw["device_class"]), str(kw["state_class"])) for kw in kwargs_list]
    assert classes == [
        ("power", "measurement"),
        ("energy", "total_increasing"),
        ("energy", "total_increasing"),
        ("energy", "measurement"),
    ]


def test_meter_sub_sensor_kwargs_only_advertised_registers():
    # AC-01 (WP-6.5): a control advertises fewer registers than the full
    # table -> only those registers yield kwargs.
    kwargs_list = meter_sub_sensor_kwargs(_control(states={"total": EM2_TOTAL, "storage": EM2_STORAGE}), None)
    assert [kw["uuidAction"] for kw in kwargs_list] == [EM2_TOTAL, EM2_STORAGE]
    assert [kw["name"] for kw in kwargs_list] == ["Total", "Level"]
    assert [kw["details"]["format"] for kw in kwargs_list] == ["%d kWh", "%d kWh"]


def test_meter_sub_sensor_kwargs_guards():
    # states missing / not a dict -> no sub-sensors, no exception (PS-08).
    assert meter_sub_sensor_kwargs({"name": "X", "type": "Wallbox", "uuidAction": WB_ACTION}, None) == []
    assert meter_sub_sensor_kwargs(_control(states="oops"), None) == []
    # a state whose uuid is not a usable string is skipped
    kwargs_list = meter_sub_sensor_kwargs(
        _control(states={"actual": 123, "total": "", "totalNeg": None, "storage": EM_STORAGE}), None
    )
    assert [kw["uuidAction"] for kw in kwargs_list] == [EM_STORAGE]
    # no details -> the neutral %.1f format fallback per register
    kwargs_list = meter_sub_sensor_kwargs(_control(details=None), None)
    assert [kw["details"]["format"] for kw in kwargs_list] == ["%.1f", "%.1f", "%.1f", "%.1f"]
    # a single missing format key only degrades that register
    # totalNeg shares the totalFormat key (METER_FORMAT_KEYS), so it gets
    # the total's format, not the neutral fallback.
    control = _control(details={"totalFormat": "%d kWh", "storageFormat": "%d kWh"})
    formats = [kw["details"]["format"] for kw in meter_sub_sensor_kwargs(control, None)]
    assert formats == ["%.1f", "%d kWh", "%d kWh", "%d kWh"]
    # no uuidAction -> registers are still built, device link falls back
    control = _control()
    del control["uuidAction"]
    kwargs_list = meter_sub_sensor_kwargs(control, None)
    assert len(kwargs_list) == 4
    assert all(kw["device_info"] is None for kw in kwargs_list)
    assert all(kw["parent_id"] == "" for kw in kwargs_list)


def test_meter_device_info_literals():
    device = meter_device_info(_control(), None)
    assert device is not None
    # (DOMAIN, parent uuidAction) identifier, control name, type-based
    # model, room from the control.
    assert device["identifiers"] == {("loxone", EM_ACTION)}
    assert device["name"] == "Energy Manager"
    assert device["model"] == "EnergyManager"
    assert device["suggested_area"] == "Garden"
    assert meter_device_info({"name": "Nope", "type": "Wallbox"}, None) is None
    assert meter_device_info({"name": "Nope", "type": "Wallbox", "uuidAction": ""}, None) is None


def test_get_all_family_types():
    structure = {
        "controls": {
            "a": {"name": "A", "type": "Meter", "uuidAction": "m-1"},
            "b": {"name": "B", "type": "EnergyManager", "uuidAction": "em-1"},
            "c": {"name": "C", "type": "EnergyManager2", "uuidAction": "em2-1"},
            "d": {"name": "D", "type": "PowerUnit", "uuidAction": "pu-1"},
            "e": {"name": "E", "type": "Wallbox", "uuidAction": "wb-1"},
            "f": {"name": "F", "type": "InfoOnlyAnalog", "uuidAction": "ioa-1"},
        }
    }
    found = get_all(structure, list(METER_FAMILY_TYPES))
    assert [c["uuidAction"] for c in found] == ["m-1", "em-1", "em2-1", "pu-1", "wb-1"]


# --------------------------------------------------------------------------- #
# Full setup with the LoxAPP3 fixture: appear, update, device linkage
# --------------------------------------------------------------------------- #
async def test_meter_family_appears_and_updates(hass, mock_connection, mock_entry) -> None:
    """Acceptance (WP-6.5): every family control's registers appear after
    setup, update on fed state events, and the registers of one control
    share one device."""
    mock_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.config_entries.async_get_entry(mock_entry.entry_id).state is ConfigEntryState.LOADED

    entity_reg = er.async_get(hass)
    device_reg = dr.async_get(hass)
    by_unique_id = {entry.unique_id: entry for entry in entity_reg.entities.values()}

    # -- the registers appear, with the fixture's per-control coverage ---
    # EnergyManager: all four registers ...
    for uuid in (EM_ACTUAL, EM_TOTAL, EM_TOTAL_NEG, EM_STORAGE):
        entry = by_unique_id.get(uuid)
        assert entry is not None, f"register {uuid} was not set up"
        assert entry.domain == "sensor"
        assert hass.states.get(entry.entity_id) is not None
    # ... EnergyManager2: three (no `actual` on the control) ...
    for uuid in (EM2_TOTAL, EM2_TOTAL_NEG, EM2_STORAGE):
        assert by_unique_id.get(uuid) is not None, f"register {uuid} was not set up"
    # ... PowerUnit: three (no `totalNeg`) ...
    for uuid in (PU_ACTUAL, PU_TOTAL, PU_STORAGE):
        assert by_unique_id.get(uuid) is not None, f"register {uuid} was not set up"
    # ... Wallbox: two (actual/total only).
    for uuid in (WB_ACTUAL, WB_TOTAL):
        assert by_unique_id.get(uuid) is not None, f"register {uuid} was not set up"

    # No phantom registers for registers the controls do not advertise:
    # the registry holds exactly 4+3+3+2 = 12 new registers (the three
    # legacy Meter registers already in the fixture apart).
    family_result = [
        e
        for e in entity_reg.entities.values()
        if e.config_entry_id == mock_entry.entry_id
        and e.domain == "sensor"
        and e.unique_id
        in {
            EM_ACTUAL,
            EM_TOTAL,
            EM_TOTAL_NEG,
            EM_STORAGE,
            EM2_TOTAL,
            EM2_TOTAL_NEG,
            EM2_STORAGE,
            PU_ACTUAL,
            PU_TOTAL,
            PU_STORAGE,
            WB_ACTUAL,
            WB_TOTAL,
        }
    ]
    assert len(family_result) == 12

    # -- fed state events update the registers (per-control isolation) --
    mock_connection.feed(EM_ACTUAL, 1240)
    mock_connection.feed(EM_TOTAL, 5123)
    mock_connection.feed(EM2_TOTAL, 77)
    mock_connection.feed(PU_STORAGE, 8)
    mock_connection.feed(WB_ACTUAL, 11000)
    mock_connection.feed(WB_TOTAL, 42)
    await hass.async_block_till_done()

    assert hass.states.get(by_unique_id[EM_ACTUAL].entity_id).state == "1240"
    assert hass.states.get(by_unique_id[EM_TOTAL].entity_id).state == "5123"
    assert hass.states.get(by_unique_id[EM2_TOTAL].entity_id).state == "77"
    assert hass.states.get(by_unique_id[PU_STORAGE].entity_id).state == "8"
    assert hass.states.get(by_unique_id[WB_ACTUAL].entity_id).state == "11000"
    assert hass.states.get(by_unique_id[WB_TOTAL].entity_id).state == "42"

    # A fed event for one control's register must not leak into a
    # sibling control's register of the same name.
    kept = hass.states.get(by_unique_id[EM_TOTAL].entity_id).state
    mock_connection.feed(WB_TOTAL, 1337)
    await hass.async_block_till_done()
    assert hass.states.get(by_unique_id[WB_TOTAL].entity_id).state == "1337"
    assert hass.states.get(by_unique_id[EM_TOTAL].entity_id).state == kept

    # -- the registers of one control share one device (PS-20) ----------
    for control_uuid, model in {
        EM_ACTION: "EnergyManager",
        EM2_ACTION: "EnergyManager2",
        PU_ACTION: "PowerUnit",
        WB_ACTION: "Wallbox",
    }.items():
        device_ids = {
            e.device_id
            for e in entity_reg.entities.values()
            if e.config_entry_id == mock_entry.entry_id
            and e.device_id
            and device_reg.async_get(e.device_id) is not None
            and any(
                (ident[0] == "loxone" and ident[1] == control_uuid)
                for ident in (device_reg.async_get(e.device_id).identifiers)
            )
        }
        assert len(device_ids) == 1, f"expected exactly one device for {control_uuid}"
        device = device_reg.async_get(next(iter(device_ids)))
        assert device.model == model
        # every registered register of that control sits on the shared
        # device
        assert device_ids == {by_unique_id[u].device_id for u in _family_registers(control_uuid)}


def _family_registers(control_uuid: str) -> list[str]:
    return {
        EM_ACTION: [EM_ACTUAL, EM_TOTAL, EM_TOTAL_NEG, EM_STORAGE],
        EM2_ACTION: [EM2_TOTAL, EM2_TOTAL_NEG, EM2_STORAGE],
        PU_ACTION: [PU_ACTUAL, PU_TOTAL, PU_STORAGE],
        WB_ACTION: [WB_ACTUAL, WB_TOTAL],
    }[control_uuid]


# --------------------------------------------------------------------------- #
# PS-08: a broken family control must not abort the sensor platform
# --------------------------------------------------------------------------- #
async def test_broken_wallbox_does_not_abort_platform(hass, mock_connection, mock_entry) -> None:
    # A Wallbox whose ``details`` is a string: every guarded lookup in
    # the generalised loop falls through (no crash, neutral %.1f
    # formats) instead of aborting the sensor platform (PS-08).
    kwargs = meter_sub_sensor_kwargs(
        {
            "name": "Wallbox Garage",
            "type": "Wallbox",
            "uuidAction": WB_ACTION,
            "room": "Garden",
            "cat": "Energy",
            "details": "corrupted",
            "states": {"actual": WB_ACTUAL, "total": WB_TOTAL},
        },
        None,
    )
    assert [kw["uuidAction"] for kw in kwargs] == [WB_ACTUAL, WB_TOTAL]
    assert [kw["details"]["format"] for kw in kwargs] == ["%.1f", "%.1f"]

    # and a real setup from the fixture (which contains the four family
    # members) still brings the entry up to LOADED
    mock_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.config_entries.async_get_entry(mock_entry.entry_id).state is ConfigEntryState.LOADED

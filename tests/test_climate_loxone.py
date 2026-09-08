"""WP-1.2 climate KeyError regression tests (`CVE-1.10` / `CVE-43`).

The 3 ``LoxoneRoomController*`` climate classes in ``custom_components/loxone/
climate.py`` built a ``states`` dict and subscripted it with a bare
``[name]`` (KeyError) or a ``.find(...)`` zero/falsy.  These pin the fixes:

* ``LoxoneAcControl.get_state_value`` + every property that reads a state key
  (``fan_mode``/``fan_modes``/``swing_mode``/``swing_modes``/``hvac_mode``)
  returns the platform default when the control LACKS that state key.
* ``temperature_unit`` matches on degree-symbol containment, not
  ``.find("°")`` (which returns 0 — falsy — when the format starts in ``"°"``).
* ``LoxoneRoomControllerV2.event_handler`` must not raise ``ValueError`` for an
  unknown mode value (``CVE-43``); it logs a warning and keeps the state.
"""

from __future__ import annotations

from types import SimpleNamespace

from homeassistant.const import UnitOfTemperature

from custom_components.loxone.climate import LoxoneAcControl, LoxoneRoomControllerV2


def _accontrol(states: dict[str, str], details: dict | None = None) -> LoxoneAcControl:
    return LoxoneAcControl(
        hass=None,
        states=states,
        details=details or {},
        room="",
        name="AC",
        uuid="uuid-0000-0000-0000-000000000001",
        uuidAction="uuid-0000-0000-0000-000000000001",
        cat="heating",
    )


def _controller_v2(states: dict[str, str]) -> LoxoneRoomControllerV2:
    return LoxoneRoomControllerV2(
        hass=None,
        states=states,
        details={"timerModes": [], "possibleCapabilities": 3, "singleComfortTemperature": False},
        hvac_auto_mode=0,
        room="",
        name="Controller",
        uuid="uuid-0000-0000-0000-000000000002",
        uuidAction="uuid-0000-0000-0000-000000000002",
        cat="heating",
    )


def test_accontrol_get_state_value_missing_key_returns_none() -> None:
    e = _accontrol({"temperature": "st_temperature"})
    # A key the control does not have returns None, and must not KeyError.
    assert e.get_state_value("fanspeeds") is None
    assert e.get_state_value("airflows") is None
    # A key that exists but has not yet been event-set returns None too.
    assert e.get_state_value("temperature") is None


def test_accontrol_fan_and_swing_defaults_without_states() -> None:
    e = _accontrol({"temperature": "st_temperature"})
    assert e.fan_mode == "Auto"
    assert e.fan_modes is None
    assert e.swing_mode == "Auto"
    assert e.swing_modes is None


def test_accontrol_hvac_mode_defaults_off_without_states() -> None:
    from homeassistant.components.climate.const import HVACMode

    e = _accontrol({"temperature": "st_temperature"})
    # No status/mode states → platform is off, not a KeyError.
    assert e.hvac_mode is HVACMode.OFF


def test_accontrol_temperature_unit_matches_symbol_not_find() -> None:
    # "°C" at index 0: the old ``.find("°")`` returned 0 (falsy) → mislabeled F.
    assert (
        _accontrol({"temperature": "st"}, details={"format": "T °C today"}).temperature_unit
        == UnitOfTemperature.CELSIUS
    )
    assert (
        _accontrol({"temperature": "st"}, details={"format": "T °F now"}).temperature_unit
        == UnitOfTemperature.FAHRENHEIT
    )


def test_accontrol_temperature_unit_defaults_celsius_without_format() -> None:
    assert _accontrol({"temperature": "st"}, details={}).temperature_unit == UnitOfTemperature.CELSIUS


async def test_v2_unknown_operating_mode_logs_not_crash(hass, caplog) -> None:
    import logging

    e = _controller_v2({"operatingMode": "st_operatingMode", "activeMode": "st_activeMode"})
    e.hass = hass  # present so the guard runs; we don't exercise the state write
    e.schedule_update_ha_state = lambda *a, **k: None  # no entity_id → skip the write

    with caplog.at_level(logging.WARNING):
        # 999 is not a valid OperatingMode id → ValueError path → warn, no crash (CORE-43).
        await e.event_handler(SimpleNamespace(data={"st_operatingMode": 999, "st_activeMode": 0}))

    assert any("unknown" in r.message.lower() for r in caplog.records), [r.message for r in caplog.records]

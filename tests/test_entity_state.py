"""WP-1.3 initial-state correctness.

No entity may report a concrete state before its first value is seen (PS-02):
the backing attributes start as ``None`` (HA renders that as ``unknown``), and
the switch availability / on-state only become real after a *state-uuid* event
arrives, not merely from setup.
"""

from __future__ import annotations

from types import SimpleNamespace

from custom_components.loxone.switch import LoxoneSwitch
from custom_components.loxone.text import LoxoneText


def _switch() -> LoxoneSwitch:
    # A constructor without a real state uuid populated: the entity must start
    # unknown even though it exists in the registry.
    return LoxoneSwitch(
        hass=None,
        states={"active": "active-uuid"},
        name="S",
        room="",
        uuidAction="sw-0001",
    )


def _text() -> LoxoneText:
    return LoxoneText(
        hass=None,
        states={"text": "text-uuid"},
        name="T",
        room="",
        uuidAction="tx-0001",
    )


def _stub_ha_write(e) -> None:
    e.async_schedule_update_ha_state = lambda *a, **k: None
    e.schedule_update_ha_state = lambda *a, **k: None


def test_switch_is_on_is_none_before_event() -> None:
    # PS-02: no switch may report a concrete state (not even "unknown" string)
    # before its first value.
    assert _switch()._attr_is_on is None


async def test_switch_becomes_on_hass_event(hass) -> None:
    e = _switch()
    e.hass = hass
    _stub_ha_write(e)
    assert e._attr_is_on is None

    # A real "active" state event makes the switch True and available.
    await e.event_handler(SimpleNamespace(data={e.states["active"]: True}))
    assert e._attr_is_on is True
    assert e._attr_available is True


def test_text_native_value_is_none_before_event() -> None:
    # PS-09: the Text entity's native_value is None (unknown) before a value.
    assert _text().native_value is None


async def test_text_reports_value_after_event(hass) -> None:
    e = _text()
    e.hass = hass
    _stub_ha_write(e)
    assert e.native_value is None

    await e.event_handler(SimpleNamespace(data={e.uuidAction: "hello world"}))
    assert e.native_value == "hello world"

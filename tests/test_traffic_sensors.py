"""Unit tests for the diagnostic in/out traffic-rate sensors.

``LoxoneTrafficRateSensor`` samples the connection's monotonic message
counters on a timer and reports the average rate (msg/min) over the elapsed
window. The expected rates below are derived by hand (delta / seconds * 60),
never from the code under test; the connection is faked so nothing touches a
socket.
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest

from homeassistant.util import dt as dt_util

from custom_components.loxone.sensor import LoxoneTrafficRateSensor


def _sensor(direction: str, rx: int = 0, tx: int = 0):
    s = LoxoneTrafficRateSensor("SERIAL", direction)
    s.entity_id = None  # keep async_write_ha_state a no-op (LoxoneEntity guard)
    api = SimpleNamespace(messages_received=rx, messages_sent=tx)
    coord = SimpleNamespace(api=api, connected=True)
    s._connection_coordinator = lambda: coord  # type: ignore[method-assign]
    return s, api


def test_direction_validation() -> None:
    with pytest.raises(ValueError):
        LoxoneTrafficRateSensor("SERIAL", "sideways")


def test_unique_id_and_name_per_direction() -> None:
    si, _ = _sensor("in")
    so, _ = _sensor("out")
    assert si.unique_id == "SERIAL-loxone_traffic_inbound"
    assert so.unique_id == "SERIAL-loxone_traffic_outbound"
    assert si.name == "Loxone Traffic In"
    assert so.name == "Loxone Traffic Out"


def test_inbound_rate_is_messages_per_minute() -> None:
    s, api = _sensor("in", rx=100)
    t0 = dt_util.utcnow()
    s._last_total, s._last_sample = 100, t0
    api.messages_received = 130  # 30 messages over 30s -> 60 msg/min
    s._sample(t0 + timedelta(seconds=30))
    assert s.native_value == 60.0
    assert s.extra_state_attributes["total"] == 130


def test_outbound_uses_the_sent_counter() -> None:
    s, api = _sensor("out", tx=10)
    t0 = dt_util.utcnow()
    s._last_total, s._last_sample = 10, t0
    api.messages_sent = 12  # 2 commands over 60s -> 2 msg/min
    s._sample(t0 + timedelta(seconds=60))
    assert s.native_value == 2.0


def test_first_tick_only_baselines() -> None:
    s, api = _sensor("in", rx=50)
    s._last_total, s._last_sample = None, None
    api.messages_received = 50
    s._sample(dt_util.utcnow())
    assert s.native_value is None  # no rate until there is a prior sample
    assert s._last_total == 50


def test_counter_reset_reports_zero_and_rebaselines() -> None:
    # After a full entry reload the connection object is fresh (counter 0),
    # while the sensor still holds the old higher baseline: report 0, not a
    # negative rate, and re-anchor.
    s, api = _sensor("in", rx=5)
    t0 = dt_util.utcnow()
    s._last_total, s._last_sample = 1000, t0
    api.messages_received = 5
    s._sample(t0 + timedelta(seconds=30))
    assert s.native_value == 0.0
    assert s._last_total == 5


def test_disconnected_drops_baseline() -> None:
    s, _ = _sensor("in", rx=5)
    s._connection_coordinator = lambda: None  # no coordinator/api
    s._last_total = 5
    s._sample(dt_util.utcnow())
    assert s.native_value is None
    assert s._last_total is None

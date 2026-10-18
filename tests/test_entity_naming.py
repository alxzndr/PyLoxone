"""Entity names do not repeat their device's name (has_entity_name)."""

from __future__ import annotations

import pytest
from homeassistant.helpers import device_registry as dr

from custom_components.loxone import LoxoneEntity


def _entity(name: str, device_name: str | None) -> LoxoneEntity:
    entity = LoxoneEntity(name=name, uuidAction="63746c3a-0157-9766-ffff-e6720526f6f6000087")
    entity._attr_name = name
    entity._attr_device_info = dr.DeviceInfo(name=device_name) if device_name is not None else None
    return entity


@pytest.mark.parametrize(
    ("name", "device", "expected"),
    [
        # From the maintainer's installation: outputs named after their controller.
        ("home cinema led achter", "Home Cinema", "led achter"),
        ("tuinhuis straler", "Tuinhuis", "straler"),
        # Equal to the device name: kept (a second unnamed entity would collide).
        ("slaapkamer ouders", "slaapkamer ouders", "slaapkamer ouders"),
        # Only a whole leading word sequence counts.
        ("badkamerspiegel", "badkamer", "badkamerspiegel"),
        ("led achter", "Home Cinema", "led achter"),
        ("spots", None, "spots"),
    ],
)
def test_name_drops_a_leading_device_name(name, device, expected) -> None:
    assert _entity(name, device).name == expected

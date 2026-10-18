"""Home Assistant 2026.11: entity_id changes happen in place.

From 2026.11 a custom entity class that overrides an add/remove method is
removed and re-added on an entity_id change (losing its runtime state and
re-subscribing) unless the class, or a subclass of it, implements
``async_entity_id_changed``. Every entity class of the integration must be
covered, including ones added later.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil

import custom_components.loxone as loxone
from homeassistant.helpers.entity import Entity

_ADD_REMOVE = ("async_prepare_to_add_to_hass", "async_added_to_hass", "async_will_remove_from_hass")


def _integration_entity_classes() -> list[type]:
    classes = set()
    for mod_info in pkgutil.walk_packages(loxone.__path__, "custom_components.loxone."):
        if ".pyloxone_api" in mod_info.name or mod_info.name.endswith("__main__"):
            continue
        module = importlib.import_module(mod_info.name)
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if issubclass(obj, Entity) and obj.__module__.startswith("custom_components.loxone"):
                classes.add(obj)
    return sorted(classes, key=lambda c: c.__qualname__)


def _requires_readd(cls: type) -> bool:
    """The 2026.11 rule (``entity._entity_class_requires_readd``)."""
    mro = [c for c in cls.__mro__ if not c.__module__.startswith("homeassistant.")]
    owners = [c for c in mro if "async_entity_id_changed" in c.__dict__]
    return any(any(m in c.__dict__ for m in _ADD_REMOVE) and not any(issubclass(o, c) for o in owners) for c in mro)


def test_every_entity_class_changes_its_entity_id_in_place():
    classes = _integration_entity_classes()
    assert len(classes) > 30  # the walk found the platforms
    assert [c.__qualname__ for c in classes if _requires_readd(c)] == []


def test_the_rule_matches_home_assistant_where_it_exists():
    from homeassistant.helpers import entity as entity_helper

    ha_rule = getattr(entity_helper, "_entity_class_requires_readd", None)
    if ha_rule is None:
        return  # Home Assistant before 2026.11: entities are changed in place anyway
    for cls in _integration_entity_classes():
        assert ha_rule(cls) is False, cls.__qualname__

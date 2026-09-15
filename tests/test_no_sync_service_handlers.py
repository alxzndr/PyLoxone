"""Ratchet: entity service handlers must be coroutines, never plain ``def``.

Home Assistant dispatches a plain ``def`` entity service handler to its
executor thread, so every line of such a handler runs off the event loop.
That bit this integration twice: ``LoxoneEntity._send`` created its outbound
task from the worker thread (v0.10.5), and two climate handlers ended with
``async_write_ha_state()`` off-loop (v0.10.6). Both were fixed with
loop-hopping workarounds; the real fix is that the handlers are ``async def``,
which HA runs inline on the loop.

This guard keeps them that way: any class that defines one of the HA entity
service handler names as a plain ``def`` fails here with a file:class.method
listing.
"""

from __future__ import annotations

import ast
from pathlib import Path

# HA entity service handler names. HA looks each up on the entity and, when the
# ``async_``-prefixed twin is absent, runs the plain one in its executor.
SERVICE_HANDLER_NAMES = frozenset(
    {
        "turn_on",
        "turn_off",
        "toggle",
        "open_cover",
        "close_cover",
        "stop_cover",
        "set_cover_position",
        "open_cover_tilt",
        "close_cover_tilt",
        "stop_cover_tilt",
        "set_cover_tilt_position",
        "set_percentage",
        "set_preset_mode",
        "set_direction",
        "oscillate",
        "set_temperature",
        "set_hvac_mode",
        "set_fan_mode",
        "set_swing_mode",
        "select_option",
        "set_value",
        "set_native_value",
        "press",
        "activate",
        "alarm_disarm",
        "alarm_arm_home",
        "alarm_arm_away",
        "alarm_arm_night",
        "alarm_arm_vacation",
        "alarm_trigger",
        "media_play",
        "media_pause",
        "media_stop",
        "media_next_track",
        "media_previous_track",
        "set_volume_level",
        "mute_volume",
        "select_source",
    }
)

INTEGRATION_ROOT = Path(__file__).parent.parent / "custom_components" / "loxone"


def _sync_handlers(path: Path) -> list[str]:
    """Return ``file:Class.method`` for every plain-``def`` service handler in ``path``."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for item in node.body:
            # ast.AsyncFunctionDef is deliberately not matched -- coroutines are the goal.
            if isinstance(item, ast.FunctionDef) and item.name in SERVICE_HANDLER_NAMES:
                offenders.append(f"{path.name}:{node.name}.{item.name}")
    return offenders


def test_no_sync_entity_service_handlers() -> None:
    """No class may define an HA entity service handler as a plain ``def``."""
    offenders: list[str] = []
    for path in sorted(INTEGRATION_ROOT.rglob("*.py")):
        offenders.extend(_sync_handlers(path))

    assert not offenders, (
        "Entity service handlers must be `async def async_<name>` -- a plain `def` is run in "
        f"Home Assistant's executor thread, off the event loop. Found {len(offenders)}:\n"
        + "\n".join(f"  {offender}" for offender in offenders)
    )

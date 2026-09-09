"""
Loxone Scenes

For more details about this component, please refer to the documentation at
https://github.com/JoDehli/PyLoxone
"""

import json
import logging

from homeassistant.components.scene import Scene
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_SCENE_GEN, EVENT, SENDDOMAIN
from .helpers import device_info_for, iter_controls

_LOGGER = logging.getLogger(__name__)


def parse_mood_list(raw):
    """Return the moods from a LCV2 ``moodList`` value, or ``None``.

    Accepts the streamed JSON string
    (``[{"id": "107", "name": "Cozy"}, ...]``) or an already-parsed list;
    anything else (e.g. a state uuid that is not a mood list) is ignored.
    (PS-17: the mood list content is data-driven, so scenes no longer have
    to wait for another platform's entities to fill in.)
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError, TypeError:
            return None
    if isinstance(raw, list):
        return [mood for mood in raw if isinstance(mood, dict) and mood.get("id") is not None and "name" in mood]
    return None


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Prepare scenes for every LightControllerV2 in the structure file.

    No artificial delay: the old ``hass.loop.call_later`` ran even after the
    entry was unloaded, and the generated scenes scraped
    ``hass.data["light"]`` for entities of a *different* platform (PS-17).
    Each LightControllerV2 gets its device entry straight from the structure
    file, and its scene entities are added as soon as the LCV2 pushes its
    ``moodList`` (a live Miniserver streams that immediately after
    connecting). The subscription is the only one in this module and it is
    stored on the Miniserver, so entry unload cancels it.
    """
    if not config_entry.options.get(CONF_SCENE_GEN, False):
        return

    @callback
    def add_scene_entities(entities):
        if entities:
            _LOGGER.info("Generated %i Loxone scene(s)", len(entities))
            async_add_entities(entities)

    for controller in iter_controls(hass, config_entry, "LightControllerV2"):
        states = controller.get("states") or {}
        mood_list_uuid = states.get("moodList")
        if not mood_list_uuid:
            continue

        # PS-17 device link: the scenes share the controller's device
        # (a fresh payload per build; CORE-20), linked to the Miniserver
        # host device via ``via_device``.
        # PS-17 device link: the scenes share the controller's device
        # (a fresh payload per build; CORE-20).
        device_info = device_info_for(
            config_entry,
            controller["uuidAction"],
            controller.get("name", "LightControllerV2"),
            "LightControllerV2",
            controller.get("room", ""),
        )

        processed = []

        @callback
        def build_scenes(mood_list, controller=controller, device_info=device_info):
            return [
                Loxonelightscene(
                    name=mood["name"],  # WP-5.1: mood name only; device is the LCV2's (CORE-26)
                    mood_id=mood["id"],
                    uuid=controller["uuidAction"],
                    light_controller_id=controller["uuidAction"],
                    device_info=device_info,
                )
                for mood in mood_list
            ]

        @callback
        def emit_scenes(mood_list, processed=processed, build_scenes=build_scenes):
            if not mood_list or processed:
                return
            processed.append(True)
            add_scene_entities(build_scenes(mood_list))

        # Some structure files already carry the mood list in the
        # ``states`` value; the normal case is that it is streamed.
        emit_scenes(parse_mood_list(mood_list_uuid))

        @callback
        def on_mood_list_event(event, mood_list_uuid=mood_list_uuid, emit_scenes=emit_scenes):
            """Create the scenes as soon as the mood list arrives via the bus."""
            data = event.data if isinstance(event.data, dict) else {}
            if mood_list_uuid not in data:
                return
            parsed = parse_mood_list(data[mood_list_uuid])
            if not parsed:
                return
            emit_scenes(parsed)

        # CORE-17: the mood-list bus subscription is entry-scoped (the
        # dead ``MiniServer.listeners`` list was never iterated, so these
        # listeners leaked on unload).
        config_entry.async_on_unload(hass.bus.async_listen(EVENT, on_mood_list_event))

    return True


class Loxonelightscene(Scene):
    """Representation of a Loxone light scene.

    One scene per LCV2 mood. The controller it belongs to is the LCV2's
    action uuid (scenes are addressed with ``changeTo/<moodId>`` at that
    uuid), and the scene links to the same device as the light (PS-17).
    """

    def __init__(self, name, mood_id, uuid, light_controller_id, device_info=None):
        """Initialize the scene."""
        self._attr_name = name
        self._attr_unique_id = f"{light_controller_id}-{mood_id}"
        self.mood_id = mood_id
        self.uuidAction = uuid
        self._light_controller_id = light_controller_id
        if device_info is not None:
            self._attr_device_info = device_info

    async def async_activate(self, **kwargs):
        """Activate scene. Try to get entities into requested state."""
        self.hass.bus.async_fire(
            SENDDOMAIN,
            {"uuid": self.uuidAction, "value": f"changeTo/{self.mood_id}"},
        )

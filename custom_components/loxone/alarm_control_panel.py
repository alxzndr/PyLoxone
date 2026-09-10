"""Loxone Alarm controls as alarm control panels."""

import logging

from homeassistant.components.alarm_control_panel import AlarmControlPanelEntity, AlarmControlPanelState
from homeassistant.components.alarm_control_panel.const import AlarmControlPanelEntityFeature, CodeFormat
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LoxoneEntity
from .helpers import add_room_and_cat_to_value_values, get_all, get_or_create_device
from .miniserver import get_miniserver_from_hass

_LOGGER = logging.getLogger(__name__)


def _state_uuid(states: dict, name: str) -> str | None:
    """Un-guarded ``states[name]`` indexing crashes the alarm platform when a
    structure file omits an attribute (PC-16); return ``None`` instead."""
    return states.get(name)


def alarm_arm_value(arm_state: AlarmControlPanelState) -> str:
    """Command value that arms an Loxone alarm (PC-31, **VERIFY**).

    Intended semantics: the argument of ``delayedon/<x>`` is Loxone's
    movement-disabled flag, and it must agree with the state mapping below (``armed and disabled_move`` →
    ``ARMED_HOME``): armed-home disables movement ⇒ ``delayedon/1``,
    armed-away ⇒ ``delayedon/0``. The previous code sent ``delayedon/0`` for
    home and ``delayedon/1`` for away, which round-trips home to *away*.
    VERIFY — confirm the round-trip on a live Miniserver before relying on the
    swapped mapping.
    """
    if arm_state == AlarmControlPanelState.ARMED_HOME:
        return "delayedon/1"
    if arm_state == AlarmControlPanelState.ARMED_AWAY:
        return "delayedon/0"
    raise ValueError(f"{arm_state} is not an arm state")


def alarm_night_arm_value() -> str:
    """Command value that arms an Loxone alarm in night mode (#323).

    Loxone alarms have exactly two arming flavours — with delay
    (`delayedon/1`, motion suppressed) and without (`delayedon/0`) — and no
    distinct night or vacation command. Night arming (the Mushroom "night"
    icon, the use case in #323) is the *delayed* arm, i.e. the same command
    as ARMED_HOME, because night is "everyone is home". **VERIFY**: confirm
    on a live Miniserver that a `delayedon/1` arm initiated from the night
    service behaves as the user expects.
    """
    return alarm_arm_value(AlarmControlPanelState.ARMED_HOME)


def alarm_vacation_arm_value() -> str:
    """Command value that arms an Loxone alarm in vacation mode (#323).

    Vacation means the home is empty, so it maps to the non-delayed arm —
    the same command as ARMED_AWAY. **VERIFY**: confirm on a live Miniserver
    that a `delayedon/0` arm initiated from the vacation service behaves as
    the user expects.
    """
    return alarm_arm_value(AlarmControlPanelState.ARMED_AWAY)


def _as_int_seconds(value):
    """Loxone websocket values → whole seconds, or ``None`` when absent/
    non-numeric. The surfaced arming-delay attributes keep a single, stable
    type (int seconds) no matter what the raw stream carried."""
    if value is None:
        return None
    try:
        return int(float(value))
    except TypeError, ValueError:
        return None


def alarm_arm_delay_attributes(armed_delay, armed_delay_total):
    """The arming-delay attributes surfaced on the panel entity (#323).

    Loxone reports `armedDelay` (seconds remaining until the alarm engages)
    and `armedDelayTotal` (the configured total delay of the current arm).
    Both are exposed as int seconds (or ``None`` before they have been
    streamed), in addition to the raw ``level``/``armed_at``/``next_level_at``
    attributes.
    """
    return {
        "armed_delay": _as_int_seconds(armed_delay),
        "armed_delay_total_delay": _as_int_seconds(armed_delay_total),
    }


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Loxone Alarms."""
    miniserver = get_miniserver_from_hass(hass, config_entry)
    loxconfig = miniserver.lox_config.json
    entities = []
    for loxone_alarm in get_all(loxconfig, "Alarm"):
        loxone_alarm = add_room_and_cat_to_value_values(loxconfig, loxone_alarm)
        loxone_alarm.update({"code": None})
        new_alarm = LoxoneAlarm(**loxone_alarm)
        entities.append(new_alarm)

    async_add_entities(entities, True)


class LoxoneAlarm(LoxoneEntity, AlarmControlPanelEntity):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._state = 0.0
        self._disabled_move = 0.0
        self._level = 0.0
        # None until the first stream value: 0.0 would publish "delay 0 s"
        # for an alarm that never streamed its delay (#323).
        self._armed_delay = None
        self._armed_delay_total_delay = None
        self._armed_at = 0
        self._next_level_at = 0
        # Fixed at setup time from the structure file (PC-06): a secured alarm
        # needs a numeric arm/disarm code, an unsecured one needs none. They
        # must not depend on ``code_arm_required`` being read first.
        is_secured = bool(kwargs.get("isSecured"))
        self._attr_code_arm_required = is_secured
        self._attr_code_format = CodeFormat.NUMBER if is_secured else None
        self._attr_device_info = get_or_create_device(self.unique_id, self._lox_name, "Alarm", self.room)

    @property
    def supported_features(self):
        # ARM_NIGHT / ARM_VACATION (#323): the Mushroom card's night and
        # vacation icons are only usable when these are advertised.
        return (
            AlarmControlPanelEntityFeature.ARM_HOME
            | AlarmControlPanelEntityFeature.ARM_AWAY
            | AlarmControlPanelEntityFeature.ARM_NIGHT
            | AlarmControlPanelEntityFeature.ARM_VACATION
        )

    def _state_uuids(self) -> frozenset[str]:
        # CORE-27: every state stream the handler mocks.
        return frozenset(
            uuid
            for uuid in (
                _state_uuid(self.states, "armed"),
                _state_uuid(self.states, "disabledMove"),
                _state_uuid(self.states, "armedAt"),
                _state_uuid(self.states, "nextLevelAt"),
                _state_uuid(self.states, "armedDelay"),
                _state_uuid(self.states, "armedDelayTotal"),
                _state_uuid(self.states, "level"),
            )
            if isinstance(uuid, str) and uuid
        )

    @callback
    def event_handler(self, e):
        request_update = False

        if (u := _state_uuid(self.states, "armed")) and u in e:
            self._state = e[u]
            request_update = True

        if (u := _state_uuid(self.states, "disabledMove")) and u in e:
            self._disabled_move = e[u]
            request_update = True

        if (u := _state_uuid(self.states, "armedAt")) and u in e:
            self._armed_at = e[u]
            request_update = True

        if (u := _state_uuid(self.states, "nextLevelAt")) and u in e:
            self._next_level_at = e[u]
            request_update = True

        if (u := _state_uuid(self.states, "armedDelay")) and u in e:
            self._armed_delay = e[u]
            request_update = True

        if (u := _state_uuid(self.states, "armedDelayTotal")) and u in e:
            self._armed_delay_total_delay = e[u]
            request_update = True

        if (u := _state_uuid(self.states, "level")) and u in e:
            self._level = e[u]
            request_update = True

        if request_update:
            self.async_write_ha_state()

    @property
    def armed_at(self):
        return self._armed_at

    @property
    def next_level_at(self):
        return self._next_level_at

    @property
    def armed_delay(self):
        return self._armed_delay

    @property
    def armed_delay_total_delay(self):
        return self._armed_delay_total_delay

    @property
    def disabled_move(self):
        return self._disabled_move

    @property
    def level(self):
        return self._level

    async def async_alarm_disarm(self, code=None):
        """Send disarm command."""
        if self.isSecured:
            self._send("off", code=code, secured=True)
        else:
            self._send("off")
        self.async_schedule_update_ha_state()

    async def async_alarm_arm_home(self, code=None):
        """Send arm home command."""
        value = alarm_arm_value(AlarmControlPanelState.ARMED_HOME)
        if self.isSecured:
            self._send(value, code=code, secured=True)
        else:
            self._send(value)
        self.async_schedule_update_ha_state()

    async def async_alarm_arm_away(self, code=None):
        """Send arm away command."""
        value = alarm_arm_value(AlarmControlPanelState.ARMED_AWAY)
        if self.isSecured:
            self._send(value, code=code, secured=True)
        else:
            self._send(value)
        self.async_schedule_update_ha_state()

    async def async_alarm_arm_night(self, code=None):
        """Arm for the night (delayed arm, motion suppressed) (#323)."""
        value = alarm_night_arm_value()
        if self.isSecured:
            self._send(value, code=code, secured=True)
        else:
            self._send(value)
        self.async_schedule_update_ha_state()

    async def async_alarm_arm_vacation(self, code=None):
        """Arm for vacation (non-delayed arm) (#323)."""
        value = alarm_vacation_arm_value()
        if self.isSecured:
            self._send(value, code=code, secured=True)
        else:
            self._send(value)
        self.async_schedule_update_ha_state()

    @property
    def alarm_state(self) -> AlarmControlPanelState | None:
        """Return the state of the device."""
        if self._level >= 1.0:
            return AlarmControlPanelState.TRIGGERED
        if self._armed_delay or self._armed_at:
            return AlarmControlPanelState.ARMING
        if self._state and self._disabled_move:
            return AlarmControlPanelState.ARMED_HOME
        if self._state:
            return AlarmControlPanelState.ARMED_AWAY
        return AlarmControlPanelState.DISARMED

    @property
    def extra_state_attributes(self):
        """Return the state attributes."""
        return {
            **self._attr_extra_state_attributes,
            "device_type": self.type,
            "level": self._level,
            "armed_at": self._armed_at,
            "next_level_at": self._next_level_at,
            **alarm_arm_delay_attributes(self._armed_delay, self._armed_delay_total_delay),
        }

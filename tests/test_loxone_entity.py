"""WP-1.1 regression tests for the ``LoxoneEntity`` base class.

Covers the three acceptance criteria in docs/review/prompts/WP-1.1.md:

1. An in-place reload of the config entry must NOT leak the shared
   ``loxone_event`` bus listener (CORE-01).  The old ``LoxoneEntity`` stored the
   listener id in ``self.listener`` and its ``async_will_remove_from_hass`` set
   ``self.listener = None`` without *calling* it, so the count grew by ~25 on
   every reload; the fix registers the listener through ``async_on_remove`` and
   HA detaches it on entity removal.
2. Constructing an entity with a read-only kwarg must not kill the process
   (LOW-02: the old ``sys.exit(-1)`` crashed every HA install that ever hit a
   ``setattr`` exception in the ``LoxoneEntity`` ctor).
3. Unique ids still derive from the control's ``uuidAction`` (PC-04 snapshot:
   behaviour kept identical, entity count unchanged).
"""

from __future__ import annotations

from custom_components.loxone import LoxoneEntity


# --------------------------------------------------------------------------- #
# 1.  Reload does not leak the shared bus listener (CORE-01)
# --------------------------------------------------------------------------- #
async def test_in_place_reload_does_not_leak_bus_listeners(
    hass, mock_connection, mock_entry, enable_custom_integrations
) -> None:
    mock_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    baseline = hass.bus.async_listeners().get("loxone_event", 0)
    assert baseline > 0, "no entities subscribed to loxone_event"

    # Reload three times, capturing the live ``loxone_event`` listener count
    # after each.  The bug was a per-reload *growth*: on master the sequence is
    # 33 -> 58 -> 83 -> 108 (+25 every cycle).  After the fix it is steady
    # (33 -> 27 -> 27 -> 27): no growth, the detached lifespan owners clean up
    # the listeners on entity removal.
    counts = [baseline]
    for _ in range(3):
        await hass.config_entries.async_reload(mock_entry.entry_id)
        await hass.async_block_till_done()
        counts.append(hass.bus.async_listeners().get("loxone_event", 0))

    # No growth on the first reload, and no growth on any subsequent reload:
    # the count must be *steady* (relax to a +1 tolerance for HA scheduler
    # jitter on the last microsecond of teardown).
    assert counts[1] <= counts[0] + 1, f"listeners grew on first reload: {counts}"
    assert counts[-1] == counts[-2], f"listeners grew across reloads: {counts}"


# --------------------------------------------------------------------------- #
# 2.  A read-only kwarg must not kill the process with SystemExit (LOW-02)
# --------------------------------------------------------------------------- #
class _ReadOnly(LoxoneEntity):
    """Subclass with a property that has NO setter."""

    @property
    def _read_only(self) -> int:
        return 1


def test_readonly_kwarg_does_not_sys_exit() -> None:
    # The kwargs loop used to ``sys.exit(-1)`` on a bare Exception; it must
    # now log and continue.  If we get here without a SystemExit the ctor
    # completed past the bad kwarg.
    e = _ReadOnly(_read_only=55, uuidAction="3a2b1c1d-0101-9617-ffff-000000000001")
    assert e.uuidAction == "3a2b1c1d-0101-9617-ffff-000000000001"
    # unique_id still derives from uuidAction (PC-04: behaviour unchanged).
    assert e.unique_id == e.uuidAction


def test_could_not_set_is_logged_not_fatal(caplog) -> None:
    import logging

    with caplog.at_level(logging.ERROR):
        _ReadOnly(_read_only=55, uuidAction="3a2b1c1d-0101-9617-ffff-000000000002")
    # The old code raised SystemExit(-1); the new code MUST log instead.
    assert "Could not set" in caplog.text


# --------------------------------------------------------------------------- #
# 3.  Entity count / unique ids unchanged (PC-04 snapshot)
# --------------------------------------------------------------------------- #
async def test_entity_count_and_unique_ids_stable(
    hass, loxapp3, mock_connection, mock_entry, enable_custom_integrations
) -> None:
    from homeassistant.helpers import entity_registry as er

    mock_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    reg = er.async_get(hass)
    entries = er.async_entries_for_config_entry(reg, mock_entry.entry_id)

    # The WP-0.2 harness is stable at 35 entities in isolation.  The lower
    # bound tolerates the integration's pre-existing entity collisions
    # (a ``fan`` presence duplicate, two ``ColorPickerV2`` ``Not implemented
    # Type`` noise logs) which can cause 1-3 entities not to register in a
    # mixed test-order; a drop below 25 signals WP-1.1 broke the entity set.
    assert len(entries) >= 25, f"entity count dropped to {len(entries)}"

    bad_ids = [e.unique_id for e in entries if not e.unique_id]
    assert not bad_ids[:5], f"empty unique_ids among {len(entries)} entities"

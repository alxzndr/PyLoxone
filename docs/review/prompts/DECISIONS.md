# Integration of provenance

Cross-package design decisions that were not made by the plan itself, but
triggered by finding-implementation on the ground. Keep entries short;
reference the WP and the findings they affect.

- **WP-1.1 (CORE-26 `cached_property` removal) — DEFERRED.** The plan
  (and CORE-26) wanted `LoxoneEntity` to set `self._attr_name` /
  `self._attr_unique_id = uuidAction` in `__init__` and delete the
  `name`/`unique_id` `@cached_property` overrides. When I did that, the
  ctor loop's `setattr(self, "name", …)` entry for subclasses changed:
  HA's `Entity.name` is a read-only property, so the loop only log
  instead of doing the cached-property's hit-stale test-patch idiom,
  `LoxoneLightSwitch` started failing with `no attribute __attr_name`
  (a name-mangled access), and the WP-0.2 fixture's entity count dropped
  35 → ~22. Reverting the two `cached_property`s and re-keeping
  `self._attr_name` and `if key == "name"` in the ctor restores exactly
  the original behaviour, which is what **PC-04**
  ("keep behaviour identical") mandates. So CORE-26 became a follow-up
  hardcoded; the `name`/`unique_id` removal MUST ship with a separately-
  audited subclass propagation (the whole set of `self.name = …` and
  `self.unique_id` read-sites) instead of silently here. The other parts
  of WP-1.1 all landed: CORE-01 (the real listener leak is fixed and
  captured by a regression test), CORE-02 (the `sys.exit` crash → log),
  CORE-32 (the dead `_clean_unit` twin), PS-12 + PS-22 (`should_poll`
  → `_attr_should_poll = False` on the base plus a `_unrecorded_
  attributes` frozenset).

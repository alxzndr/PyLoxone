# WP-6.7 — AudioZoneV2 sources / favourites / metadata / mute / on-off

Branch: `fix/wp-6.7-audio-zone` from `d632ebd` == `origin/master`
(the local `master` ref is absent here; `origin/master` is the master
tip). Prerequisite **WP-5.3 is present on master** (commit `862f688`,
"widen the blocking rule set and sweep dead code"); the earlier Phase 6
packages WP-6.1 … WP-6.6 / WP-6.8 have all landed, and this branch sits
on top of them.

Catalogue entry behind this WP: **PC-43** ("Missing capabilities" —
"`AudioZoneV2` lacks sources/favourites/metadata/mute/on-off/…").
There is **no upstream issue number** for this feature set: the
original media-player request is the long-closed
JoDehli/PyLoxone#346, and the master-side `media_player.py` still only
ships play/pause/stop/next/prev/volume. **Upstream PR check (done
first, per the plan):** no upstream PR implements any of these audio
behaviours, so nothing to port; the implementation below follows the
Phase 4 platform pattern and the phase-6.1–6.6 helpers idiom.

## What changed (by file)

- `custom_components/loxone/media_player.py`
  - Advertised features extended with `TURN_ON` / `TURN_OFF` /
    `VOLUME_MUTE` / `SELECT_SOURCE` (the WP-4.4 set is unchanged).
  - `async_turn_on` / `async_turn_off` send `on` / `off` via the new
    `audio_zone_power_command` helper; `async_mute_volume(mute)` sends
    `mute` / `unmute` (`audio_zone_mute_command`);
    `async_select_source(name)` sends `source/<name>`
    (`audio_zone_source_command`) and refuses unknown names as no-ops.
  - New 0/1 stream coercion `audio_zone_two_state` (string `"0"` is
    `False`, not a truthy string; `None` stays unknown).
  - New stream subscriptions (all read through the existing
    `_state_uuid` guard, PC-16): `active` (powered-off zones read
    `off` regardless of `playState`), `mute` (`is_volume_muted`),
    `source` (current source + media-title fallback), `sourceList`
    (JSON name list replaces the selectable list seeded from
    `details.sources`), `favouriteList` (JSON name list → the
    `favourites` state attribute), `metadata` (JSON object with
    `title` / `artist` / `album` → `media_title` / `media_artist` /
    `media_album_name`; unknown keys dropped, garbage keeps the
    previous value).
  - New pure parsing helpers, each a one-line flip if live checks
    disagree: `audio_zone_source_options`,
    `audio_zone_names_in_details`, `audio_zone_stream_names_list`,
    `audio_zone_metadata`.
- `tests/fixtures/LoxAPP3.json` — the fixture "Parlour Audio Zone"
  (AudioZoneV2) gained a `playState` stream (it had none, so the
  player could never leave its default state), a `metadata` stream, a
  `favouriteList` stream, and `details.sources` / `details.favourites`
  lists. No new controls were added — the control already existed and
  is what the WP's fixture-control step refers to.
- `tests/test_wp67_audio_zone.py` (new, 50 tests) — pure-helper tables
  (all expected values hand-derived literals), entity-behaviour tests
  (on/off/mute/source commands, unknown-source no-op, powered-off
  state, mute/source/metadata/favourite stream handling), and the full
  fixture acceptance test: the entity appears after setup, tracks all
  fed state streams, and the `turn_on` / `turn_off` / `volume_mute` /
  `select_source` services send exactly the intended commands.
- `tests/test_fan_alarm_media.py` — the WP-4.4 assertion
  "SELECT_SOURCE is not advertised" flipped to the intended WP-6.7
  set (a deliberate behaviour change of this WP, not a weakening).
- `CHANGELOG.md` — entry under *Unreleased → Added*.
- `docs/review/LIVE-MINISERVER-CHECKS.md` — three new Blocking items
  (#5–7) for the audio wire values; the non-blocking items were
  renumbered 5–16 → 8–19 and the suite count updated to the observed
  643.

Nothing else was touched: no `manifest.json` version bump (WP-0.3 owns
releases), no new `# noqa`, no changes outside the AudioZoneV2
surface.

## Definition-of-done commands (run from the repo root, 2026-07)

```
$ ruff check . --select F,E9,PLE,B,T20,S307,ASYNC,RUF006
All checks passed!

$ ruff format --check .
77 files already formatted

$ pytest -q
643 passed, 1 deselected in 70.52s
```

(coverage floor 20% reached at 58.84%).

## VERIFY items — live-Miniserver check required before merge

All of this WP sits behind named helpers precisely because the LoxApp
documentation for AudioZoneV2 command words and stream payload shapes
is not available in this repo (and the openHAB/ioBroker ports cover
the other Loxone protocol, not these streams):

1. `audio_zone_power_command` — `on` / `off` for zone power, and the
   `active` stream meaning 0/1 power (LIVE-MINISERVER-CHECKS #5).
2. `audio_zone_mute_command` — `mute` / `unmute`, and `mute` stream
   0/1 = mute flag (LIVE-MINISERVER-CHECKS #6).
3. `audio_zone_source_command` plus the stream shapes
   (`sourceList` / `favouriteList` as JSON name lists; `metadata` as a
   JSON object; `source` as a name) (LIVE-MINISERVER-CHECKS #7).

If a live Miniserver disagrees, each item is a one-line helper fix —
no entity changes are required (all parsing is delegated).

## Behaviour notes (intended semantics, pinned by tests)

- A zone the server reports powered off reads as `off` even while
  `playState` says playing; power-on restores the last `playState`.
- `media_title` follows explicit `metadata` when present and falls
  back to the active `source` name until metadata arrives.
- Selecting a source that is not in the known list sends nothing
  (a typo must not guess).
- `select_source` / `volume_mute` / `turn_on` / `turn_off` are
  fire-and-echo: the state arrives via the server's state streams,
  not optimistically.

## Follow-ups (outside this WP's scope)

- PC-43 also lists shuffle / repeat / play_media for AudioZoneV2 —
  not part of this package ("sources/favourites/metadata/mute/on-off");
  needs its own WP and its own command verification.
- `fadeIn` / `fadeOut` streams are subscribed nowhere yet (no HA
  media-player primitive maps cleanly to fade times); left as
  attributes-free.
- `details.devices` ("Living Room Speaker" etc.) is not surfaced;
  could back an `app_name`/device-list attribute in a follow-up.
- `ruff format <file.json>` (explicit path) rewrites repo JSON in
  JSON5-ish style with trailing commas that `json.loads` refuses,
  while `ruff format --check .` (the gate) leaves the fixture alone.
  Not a WP deliverable, but worth pinning in WP-5.3's toolchain work
  (e.g. an explicit `.json` exclusion in `ruff.toml`'s format
  section).
- There is no upstream issue to `Fixes`; the audio features were
  requested long ago in JoDehli/PyLoxone#346 (closed with the basic
  media player).

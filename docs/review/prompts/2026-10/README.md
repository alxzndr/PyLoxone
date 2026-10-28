# Work-package prompts, 2026-10 (upstream 0.10.2)

Prompts for coding agents, one per work package, from the review in
`docs/review/2026-10-upstream-0.10.2-gaps.md`. Each prompt is
self-contained: goal, upstream commits to read, fork files, the facts that
decide the semantics, tests, docs, out-of-scope list, commit rules and
verification commands. Each runs in its own git worktree off `master`.

| WP | Prompt | Area | Live check | Hardware in the house |
|---|---|---|---|---|
| WP-7.1 | [LightControllerV2 state and plain turn-on](WP-7.1-lcv2-state-and-turn-on.md) | lights | **#29, required before release** | 20 LCV2 (7 with master) |
| WP-7.2 | [ColorPickerV2 range and TunableWhite turn-on](WP-7.2-colorpicker-range-and-turn-on.md) | lights | **#30 (RGB), #13 update** | 3 RGB pickers; no TunableWhite |
| WP-7.3 | [TextInput, Meter totalFormatNeg, binary sensors](WP-7.3-entity-state-correctness.md) | sensors, text | #31 (entry only) | none of these blocks |
| WP-7.4 | [Climate schedule preset and unit detection](WP-7.4-climate-schedule-and-units.md) | climate | #32 (entry only) | no room controller |
| WP-7.5 | [Secured alarm code format option](WP-7.5-alarm-code-format.md) | alarm | #33 (entry only) | alarm present but not secured |
| WP-7.6 | [CLI pretty, uuid filter, web view](WP-7.6-cli-formats-and-web.md) | tooling | none (it is the live-check tool) | n/a |
| WP-7.7 | [Docs sync for upstream 0.10.2](WP-7.7-docs-sync-0.10.2.md) | docs | adds #34 (Gate stop) | Gate |

Live-check numbers #29-#34 are reserved here so parallel WPs do not
collide in `docs/review/LIVE-MINISERVER-CHECKS.md`.

## Before starting

- **WP-7.5 needs a decision** from the maintainer: default `text` (the
  recommendation; nobody gets locked out) or `number` (keeps the #413 PIN
  pad for existing installs). The prompt says what to do if no choice was
  made.
- **The Structure File v16 text was missing** at review time (expected at
  ~/Library/Caches/pyloxone/StructureFile-v16.txt). WP-7.1, 7.2, 7.3 and 7.4 each
  contain a rule they should confirm in it. Giving the agents a copy makes
  those WPs decisive instead of leaving VERIFY items behind.

## Suggested order

1. **WP-7.1** (largest impact on the house).
2. **WP-7.2**, after 7.1 is merged.
3. **WP-7.3, WP-7.4, WP-7.5, WP-7.6** in any order, in parallel with each
   other and with 7.1 / 7.2.
4. Live checks #29 and #30 on the house, then a release.
5. **WP-7.7** last, once the maintainer has decided what ships.

## What can run in parallel

| | 7.1 | 7.2 | 7.3 | 7.4 | 7.5 | 7.6 |
|---|---|---|---|---|---|---|
| 7.1 | - | **no** | yes | yes | yes | yes |
| 7.2 | **no** | - | yes | yes | yes | yes |
| 7.3 | yes | yes | - | yes | yes | yes |
| 7.4 | yes | yes | yes | - | yes | yes |
| 7.5 | yes | yes | yes | yes | - | yes |
| 7.6 | yes | yes | yes | yes | yes | - |

- 7.1 and 7.2 both edit `tests/fixtures/LoxAPP3.json`,
  `tests/snapshots/entity_names.json`, `tests/test_lights.py` and possibly
  `custom_components/loxone/light.py`: run them one after the other.
- 7.3 and 7.5 add test blocks through a module-level override of the
  `loxapp3` fixture, so they do not touch the shared fixture or the
  snapshot.
- Every WP adds to `CHANGELOG.md` `## Unreleased` and most add an entry to
  `LIVE-MINISERVER-CHECKS.md`. Those merges conflict trivially (append
  both); entry numbers are reserved above.
- 7.5 is the only WP that touches `config_flow.py`, `const.py` and the
  translations.

## Common rules (repeated in every prompt)

- Real `hass` fixture and real service calls
  (`hass.services.async_call(..., blocking=True)`) through the
  `mock_connection` / `mock_entry` fixtures; no `__new__` / `SimpleNamespace`
  entity harness as upstream uses.
- Prove every new test non-vacuous by reintroducing the defect and
  recording the failure in the commit message.
- Anything inferred rather than observed goes behind a named helper and a
  `LIVE-MINISERVER-CHECKS.md` entry.
- Cite upstream hashes, issue numbers and finding ids in commits; end
  commits with `Co-Authored-By: Claude Opus 5.5 (1M context)
  <noreply@anthropic.com>`.
- `ruff.toml` targets py313 on purpose.
- Never push, tag, open PRs or post on GitHub; never contact the live
  Miniserver or Home Assistant. Never copy contents of the maintainer's
  private structure file into the repository.
- Verify on HA 2026.8.1 (`/Users/alexandergeeraerts/github/PyLoxone/.venv`)
  and, when present, the 2026.9.1 and 2026.11-dev venvs in the review's
  scratchpad directory.

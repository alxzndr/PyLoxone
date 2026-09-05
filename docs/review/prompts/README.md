# Agent prompts — one file per work package

Each file is self-contained: rules, decisions, the package text and the full text of every finding it fixes.
Hand a file to an agent as its task prompt. Respect the prerequisite column; packages in the same phase with
the same prerequisites are parallel-safe unless the file says otherwise.

Example: `claude "$(cat docs/review/prompts/WP-0.1.md)"` or paste the file into a new session.

| Prompt file | Title | Phase | Prerequisites | Findings |
|---|---|---|---|---|
| `WP-0.1.md` | CI, lint and test harness baseline | Phase 0 | none | 17 |
| `WP-0.2.md` | Test fixtures and contract tests | Phase 0 | WP-0.1 | 6 |
| `WP-0.3.md` | Packaging, metadata, translations, docs baseline | Phase 0 | WP-0.1 | 8 |
| `WP-1.1.md` | `LoxoneEntity` base class hardening | Phase 1 | WP-0.2 | 8 |
| `WP-1.2.md` | Remove `eval()` from the light platform | Phase 1 | WP-0.2 | 2 |
| `WP-1.3.md` | Initial-state correctness (entity half of #475) | Phase 1 | WP-0.2 | 9 |
| `WP-1.4.md` | Secrets and privacy | Phase 1 | WP-0.2 | 3 |
| `WP-1.5.md` | Transient 401 during setup must retry, not die (incident 2026-09-02) | Phase 1 | WP-0.2 | 1 |
| `WP-2.1.md` | Connection correctness quick wins | Phase 2 | WP-0.2 | 16 |
| `WP-2.2.md` | Clean-close detection, auth failure handling, logging hygiene (#514) | Phase 2 | WP-2.1 | 8 |
| `WP-2.3.md` | In-place reconnect with availability | Phase 2 | WP-2.2 | 3 |
| `WP-3.1.md` | Setup / unload / reload lifecycle | Phase 3 | WP-1.1, WP-2.3 | 10 |
| `WP-3.2.md` | Multi-instance isolation (#491) | Phase 3 | WP-3.1 | 5 |
| `WP-3.3.md` | Device registry and identity | Phase 3 | WP-3.2 | 11 |
| `WP-3.4.md` | Config flow rewrite with reauth and unique id | Phase 3 | WP-3.3 | 5 |
| `WP-4.1.md` | Climate | Phase 4 | WP-1.1, WP-1.3 | 13 |
| `WP-4.2.md` | Cover | Phase 4 | WP-1.1, WP-1.3 | 9 |
| `WP-4.3.md` | Lights | Phase 4 | WP-1.1, WP-1.2, WP-1.3 | 12 |
| `WP-4.4.md` | Fan, alarm, media player | Phase 4 | WP-1.1, WP-1.3 | 10 |
| `WP-4.5.md` | Sensor, binary sensor, switch, select, number, button, scene | Phase 4 | WP-1.1, WP-1.3 | 15 |
| `WP-5.1.md` | `has_entity_name` and translation keys | Phase 5 | WP-3.4, WP-4.5 | 2 |
| `WP-5.2.md` | Repairs, availability polish, runtime_data | Phase 5 | WP-3.4 | 3 |
| `WP-5.3.md` | Lint ratchet and dead-code sweep | Phase 5 | WP-4.5 | 7 |
| `WP-5.4.md` | Documentation | Phase 5 | WP-3.4 | 3 |
| `WP-6.1.md` | `PresenceDetector` illumination/noise sub-sensors (#461) — pattern `fan.py:81-135`. | Phase 6 | WP-5.3 | 0 |
| `WP-6.2.md` | Message center → repairs (#515) — port the upstream PR. | Phase 6 | WP-5.3 | 0 |
| `WP-6.3.md` | `IntercomV2` (#466) — extend `switch.py:48`. | Phase 6 | WP-5.3 | 0 |
| `WP-6.4.md` | `InfoOnlyDigital` device-class inference from `details.text` and category (#402). | Phase 6 | WP-5.3 | 0 |
| `WP-6.5.md` | Meter family: `EnergyManager`, `EnergyManager2`, `PowerUnit`, `Wallbox` — generalise the Meter sub-state loop. | Phase 6 | WP-5.3 | 0 |
| `WP-6.6.md` | `InfoOnlyText`, `UpDownDigital`, `Tracker`; recursive `get_all` over `subControls`. | Phase 6 | WP-5.3 | 0 |
| `WP-6.7.md` | AudioZoneV2 sources/favourites/metadata/mute/on-off. | Phase 6 | WP-5.3 | 0 |
| `WP-6.8.md` | Alarm `ARM_NIGHT`/`ARM_VACATION`, arming delay surfaced (#323); Gate `SET_POSITION`; Jalousie auto/shade select; AcControl polish (#398). | Phase 6 | WP-5.3 | 0 |
| `WP-6.9.md` | Zeroconf discovery via `discover.py` if not done in WP-3.4. | Phase 6 | WP-5.3 | 0 |

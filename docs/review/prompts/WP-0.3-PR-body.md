# WP-0.3: Packaging, metadata, translations, docs baseline

**Branch:** `fix/wp-0.3-packaging-metadata` (from `master` @ WP-0.2 head)
**Findings:** TOOL-02, TOOL-03/CORE-25, TOOL-13, TOOL-14, CORE-23, CORE-24, TOOL-15.

## What this package does

Fixes the packaging/metadata so HACS serves an honest minimum-HA version, the
manifest describes the integration correctly (websocket → `local_push`, no dead
`httpx`, pinned reqs, `group` dependency), and the release process has a
version-guard so a tag can no longer ship a stale manifest version (the
0.9.23-ships-0.9.22 bug, TOOL-02). Also seeds CHANGELOG/CONTRIBUTING and a
feature-request template.

## File map

| File | Change | Finding |
|------|--------|---------|
| `custom_components/loxone/manifest.json` | `iot_class` `local_polling`→`local_push`; drop `httpx`; `websockets>=14,<16`; `pycryptodome>=3.20`; `dependencies:["group"]`; add `integration_type:"hub"` + `loggers`; `version` 0.9.22→0.9.23 (match the tag) | CORE-24, TOOL-02, TOOL-15 |
| `hacs.json` | `homeassistant` floor `2025.2.4`→`2026.7.0` (first version the code can import — `UnitOfRatio` is 2026.7.0) | CORE-25 / TOOL-03 |
| `__init__.py` | Delete the dead `REQUIREMENTS = ...` list (HA 0.x relic; HA reads `manifest.requirements`) | CORE-19 |
| `services.yaml` | literal `re\-synchronized` → `re-synchronized` | CORE-23 |
| `translations/en.json` + `de.json` | add `config.error`/`options.error` keys (`invalid_username_encoding`, `invalid_password_encoding`) matching the `SchemaFlowError` strings; drop dead `config.abort.single_instance_allowed`; `de` stays a superset of `en` | CORE-23 |
| `translations/cs.json` | add `config.error`/`options.error` (English strings — cs has no config flow translation yet; a full cs backfill is flagged as follow-up under WP-5.4) | CORE-23 |
| `README.md` | version floor `2024.1.0`→`2026.7.0`; logger snippet `...loxone.api`→`...loxone.pyloxone_api` | CORE-25, TOOL-13 |
| `CHANGELOG.md` (new) | seeded with `## Unreleased` | TOOL-14 |
| `.github/workflows/release.yaml` (new) | on tag push: assert `manifest.version == tag` (else fail), then create the GitHub release from the `Unreleased` block | TOOL-02 |
| `CONTRIBUTING.md` (new) | dev setup, local test/lint, commit style, release process | TOOL-14 |
| `.github/ISSUE_TEMPLATE/feature_request.yml` (new) | feature-request form (README invites feature requests but had no form) | TOOL-14 |
| `tests/test_contracts.py` | **flipped CORE-24 xfail → real passing test** (wp-0.0.2 coupling — httpx is gone so every manifest requirement is now imported) | CORE-24 |

## The WP-0.2 ↔ WP-0.3 coupling (the important bit)

WP-0.2 shipped `test_every_manifest_requirement_is_imported` as
`@pytest.mark.xfail(strict=True)` because `httpx` was in `requirements` but
never imported. This package **drops `httpx`**, which made that xfail
`XPASS` (→ strict xfail = hard red). The flip is part of this package: the
decorator is removed; the test is now a regular passing invariant (96 passed,
down from 4 xfails to 3). The three that remain are owned by later WPs:
CORE-07 (WP-1.7), PS-14 (WP-1.8), API-18 (WP-2.1).

## Three green commands (verbatim)

### `ruff check . --select F,E9,PLE,B,T20,S307,ASYNC,RUF006`
```
All checks passed!
```

### `ruff format --check .`
```
49 files already formatted
```

### `python -m pytest -q`
```
Required test coverage of 20% reached. Total coverage: 28.86%
96 passed, 1 deselected, 3 xfailed, 2 warnings
```

## Follow-ups (not changed here)
- `cs.json` full config-flow/localization backfill (owner VP-5.4).
- README option docs (`verify_ssl`, `generate_scenes`, `generate_scenes_delay`,
  `generate_lightcontroller_subcontrols`) — VP-5.4.
- The new `config.error` keys are added as translations but are not yet wired
  into the config-flow's `ShowForm/AbortStep` frames (no-op today); wiring
  them is a future VC/VC-flow refinement (VP-3.4).

# Changelog

All notable changes to the pyloxone integration. Format based on
[Keep a Changelog](https://keepachangelog.com).

Version numbers and release dates live in `manifest.json` and are cut
by the `release` GitHub Action on every tag push — no manual `1.0.x →
1.0.x+1` commits.

## Unreleased

### Added

- CI, lint and the offline test harness (WP-0.1/0.2)
- `CHANGELOG.md`, `CONTRIBUTING.md`, `release.yaml`, `ISSUE_TEMPLATE` forms

### Fixed

- `__init__.py` — a 401 during setup (e.g. a Miniserver inside its boot
  window after a firmware update, incident 2026-09-02) no longer ends the
  entry in `setup_error`: `return False` is gone, the branch now raises
  `ConfigEntryNotReady`, and after 5 consecutive auth failures spanning at
  least 5 minutes an ERROR points at the stored credentials (a WARNING is
  logged on every earlier attempt). `ConfigEntryAuthFailed`/re-auth lands
  in WP-3.4; every setup failure now closes the API handle, including the
  503 branch (CORE-09)

- `manifest.json` — `iot_class` corrected to `local_push`; dead `httpx`
  requirement removed; `websockets` ceiling to `<16`; `dependencies` pin
  to `group`; log list; version aligned to the `0.9.23` tag (TOOL-02,
  CORE-04)
- `hacs.json` — minimum HA version raised to `2026.7.0` to match the real
  first version where the integration can *import* (see CORE-25)
- `README.md` — version line fixed to `2026.7.0`; logger snippet swapped
  to `custom_components.loxone.pyloxone_api`; `custom_components.loxone.api`
  did not exist
- `services.yaml` and `en.json` — literal `re\-synchronized` escape removed
  (CORE-03)
- `config.abort.single_instance_allowed` and its lineage removed from
  `de.json`/`en.json` (dead stock HA key; the integration uses
  `data_exceeds_unique_id`-style aborts once WP-3.4 lands)
- `config.error` + `options.error` translation keys added (separate file)
  matching the real `SchemaFlowError` messages raised by
  `config_flow.validate_loxone_setup`
- `__init__.py` — `REQUIREMENTS` list deleted (a HA 0.x relic, HA>=2021
  reads the `requirements` key on the `manifest`)
- `__init__.py` — a transient 401 during setup (e.g. a Miniserver inside its
  boot window after a firmware update, incident 2026-09-02) no longer parks
  the entry in `setup_error` via `return False`: it now raises
  `ConfigEntryNotReady` and retries, escalating to an ERROR with
  credential-check guidance only after 5 consecutive auth failures spanning
  at least 5 minutes (`ConfigEntryAuthFailed`/reauth lands in WP-3.4);
  every setup failure now closes the API handle, fixing the connection
  leak on the 401/503 branches (CORE-09)

## 0.9.23

&mdash; (version number; release notes back-ported after first cut)

## 0.9.22 / earlier

&mdash; (earlier tags; no changelog was maintained back then)

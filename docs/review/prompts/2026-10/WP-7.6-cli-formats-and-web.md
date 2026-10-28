# WP-7.6: pyloxone-cli: pretty output, uuid filter and a local web view, with credential redaction

You work in a git worktree of `alxzndr/PyLoxone` (a maintained fork of
`JoDehli/PyLoxone`), on a new branch `feat/wp-7.6-cli` cut from `master`
(`e2123b6` or later). The remote `upstream` is `JoDehli/PyLoxone` and is
already fetched. Read `docs/review/2026-10-upstream-0.10.2-gaps.md` first.
This WP touches only `custom_components/loxone/pyloxone_api/__main__.py`,
`scripts/pyloxone-cli`, new tests and docs; it can run in parallel with the
others.

## Goal

The fork's stand-alone client (`scripts/pyloxone-cli`, 1.0.0) prints raw
state messages. Upstream 0.10.2 grew it into a useful debugging tool: a
`pretty` format with state names from the structure file, a `--uuid` filter
for one control and its sub-controls, and `--format web`, a read-only page
on `127.0.0.1` with the structure tree and live values. The maintainer uses
this kind of tool to run the live checks in
`docs/review/LIVE-MINISERVER-CHECKS.md`, so it is worth having, provided it
keeps the fork's credential rules.

## Upstream commits to read

```
git show 4230276   # pyloxone-cli with raw/pretty output and uuid filter
git show adc411b   # print command replies in full in pretty output
git show ecf4d65   # --format web with a live, server-rendered structure tree
```

The launcher `scripts/pyloxone-cli` in 4230276 is the fork's own (84efbb8);
leave it unless the new options need a change.

## Current fork code

- `custom_components/loxone/pyloxone_api/__main__.py` (52 lines): password
  from `LOXONE_PASSWORD` or `getpass`, never argv; `api.run(_print_state,
  callback=_print_message)`.
- `custom_components/loxone/pyloxone_api/connection.py:163-187, 1698`:
  auth replies (`getjwt`, `getkey2`, `getvisusalt`, ...) never reach the
  message callback; this is what makes printing command replies in full
  safe (adc411b relies on it too).
- `README.md:390-399` documents the CLI.

## Rules this port must keep

1. **Credentials never leave the connection.** The fork's NFC support
   never exposes `lastcode` or `lasttag` (CHANGELOG 0.10.x, WP-6.10). The
   upstream raw, pretty and web outputs print every state value, including
   those two. Redact them by state name (resolve state uuids to names
   from the structure file; `NfcCodeTouch` `lastcode`, `lasttag`; check
   whether `events` can carry a code and redact it too if it can) in
   **every** format, with no flag to turn redaction off. Test it.
2. The password is never accepted on the command line.
3. The web view binds `127.0.0.1` only, renders server-side with no
   JavaScript, escapes every name and value (`html.escape`), and is
   read-only (no command endpoint). Keep upstream's meta-refresh with
   pause.
4. `print` needs `# noqa: T201` (T20 is in the blocking ruff set,
   `scripts/lint`); `aiohttp` comes from Home Assistant and must not be
   added to `manifest.json`.

## Tests

The CLI does not run inside Home Assistant, so plain unit tests are right
here (no `hass` fixture), using a small synthetic structure dict in the
test module (never the private device file):

- port upstream's `tests/test_cli_formatters.py` and `tests/test_cli_web.py`
  cases to the fork's API;
- redaction: feeding a `lastcode` / `lasttag` value through each formatter
  and the web page never shows the value; the state name may appear;
- `--uuid` keeps the control, its states and its sub-controls only;
- the web page escapes a control named `<script>`;
- **prove the redaction test is not vacuous:** remove the redaction, see it
  fail, restore; record it in the commit message body.

## Documentation

- `CHANGELOG.md`, `## Unreleased`, prose voice; cite `upstream 4230276`,
  `adc411b`, `ecf4d65`; say that credentials stay redacted.
- `README.md` CLI section: the formats, `--uuid`, `--web-port`, and that
  the page is local-only.
- `docs/review/LIVE-MINISERVER-CHECKS.md` intro: mention the CLI as the
  read-only way to watch a control's states during a live check.

## Out of scope

Sending commands from the CLI or the web page, authentication on the web
page, packaging the CLI, changes to `connection.py`.

## Rules

- Do not push, tag, open a PR or post anything on GitHub. Do not contact
  the live Miniserver or Home Assistant (do not run the CLI against the
  maintainer's Miniserver; the maintainer does that).
- Small commits, conventional subjects (`feat(cli): ...`), bodies citing
  upstream hashes, ending with
  ```
  Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
  ```
- `ruff.toml` targets py313 on purpose; leave it. Keep `scripts/lint
  --check` clean.

## Verification

Run from the worktree root with the main checkout's venv:

```
V=/Users/alexandergeeraerts/github/PyLoxone/.venv
$V/bin/python -m pytest -q                          # HA 2026.8.1
PATH="$V/bin:$PATH" scripts/lint --check
$V/bin/python scripts/pyloxone-cli --help           # starts without Home Assistant
D=~/Library/Caches/pyloxone
[ -x $D/pytest-ha2026.9 ] && $D/pytest-ha2026.9 -q   # HA 2026.9.1, if present
[ -x $D/pytest-ha2026.11b ] && $D/pytest-ha2026.11b -q     # HA 2026.11.0b0, if present
```

Report: the formats added, how redaction works and its non-vacuity
evidence, and the exact command the maintainer can run for a live check.

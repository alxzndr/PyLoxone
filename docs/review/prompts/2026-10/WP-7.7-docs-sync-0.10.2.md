# WP-7.7: Docs: fork-vs-upstream for upstream 0.10.2, Gate stop live check

You work in a git worktree of `alxzndr/PyLoxone` (a maintained fork of
`JoDehli/PyLoxone`), on a new branch `docs/wp-7.7-upstream-0.10.2` cut from
`master` **after WP-7.1 to WP-7.6 have been merged** (or whichever of them
the maintainer chose to ship), so the comparison describes what the fork
really contains. The remote `upstream` is `JoDehli/PyLoxone` and is already
fetched. Documentation only (one docstring line in `cover.py` excepted, see
item 3): no behaviour change, no new tests.

## Goal

1. `docs/fork-vs-upstream.md` describes the fork against upstream 0.10.2,
   not 0.10.1.
2. The Gate `stop` command, which the code still marks VERIFY, gets a
   live-check entry; the maintainer has a Gate.
3. The "do not copy" findings of the 2026-10 review are recorded where the
   next person porting from upstream will look.

## Read first

- `docs/review/2026-10-upstream-0.10.2-gaps.md` (the per-commit table, the
  "who is right" table and the "do not copy" list)
- `git log --oneline 38016e3..upstream/master` and `git log --oneline
  e2123b6..master` (what the fork shipped since)
- `docs/fork-vs-upstream.md`, `docs/review/LIVE-MINISERVER-CHECKS.md`,
  `CHANGELOG.md` (voice and the `## Unreleased` section)
- `custom_components/loxone/cover.py:75-85` (`gate_stop_command`, VERIFY,
  PC-07) and `:287-290`
- `git show a802e1b` (upstream cites the Structure File: Gate has an
  unversioned `stop` command, "stops a moving gate")

## What to write

1. `docs/fork-vs-upstream.md`:
   - "Last updated" line and the version paragraph: upstream has released
     0.10.2 (2026-10-24); the fork continues at 1.0.x.
   - A short section "Upstream 0.10.2" that says, without marketing: most
     of 0.10.2 was already in the fork (several commits are ports from the
     fork: PS-04 smoke level, PS-19 select locks, the auth-reply filter,
     the CLI launcher), what the fork took from it (the WP-7.x changes that
     were merged, by name), where the fork deliberately differs (Window
     `stop` without the opposite-end fallback; temperature unit from
     `msInfo.tempUnit` for bare-degree formats; LightControllerV2 manual
     mode without a master; the alarm code option, if WP-7.5 shipped), and
     the "do not copy" list in one paragraph with a link to the gaps file.
   - Refresh any numbers you can measure (tests: `$V/bin/python -m pytest
     --co -q | tail -1`, with `V` as under Verification), and only those.
2. `docs/review/LIVE-MINISERVER-CHECKS.md`: add **#34 Gate `stop`**
   (Where: `gate_stop_command` in `cover.py`; The doubt: PC-07 replaced the
   opposite-direction heuristic with `stop`, never checked on hardware; the
   Structure File, per upstream a802e1b, lists `stop` for Gate; Test: on
   the maintainer's Gate, start opening from the Loxone app, press stop in
   Home Assistant while it moves, watch `position` and `active`; Pass: the
   gate stops between the end positions; Fail: the gate reverses or runs
   on). If WP-7.1 to 7.5 did not add #29-#33, leave those numbers free and
   say so at the top of your entry; do not renumber existing entries (#17
   occurs twice already; leave it).
3. Update the `gate_stop_command` docstring only if you need to point it
   at the new entry (one line; this is the only code file you may touch).
4. `CHANGELOG.md`: nothing, unless the maintainer asks for a docs entry.

## Facts and constraints

- The maintainer's real structure file is private: describe shapes ("the
  maintainer's Miniserver publishes both `autoActive` and `autoState`"),
  never names, uuids or values.
- Write in the existing documents' voice: plain sentences, no marketing,
  claims backed by a commit, a finding id or a live check.

## Rules

- Do not push, tag, open a PR or post anything on GitHub. Do not contact
  the live Miniserver or Home Assistant.
- One or two commits, `docs: ...` / `docs(live-checks): ...`, bodies citing
  the upstream hashes, ending with
  ```
  Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
  ```

## Verification

```
V=/Users/alexandergeeraerts/github/PyLoxone/.venv
$V/bin/python -m pytest -q tests/test_docs_readme.py
$V/bin/python -m pytest -q
PATH="$V/bin:$PATH" scripts/lint --check
```

Report: the sections changed and the live-check number you used.

# AGENTS.md

tinycmdr is an autonomous ops agent for self-hosted models: one Python file (`tinycmdr.py`) plus
its install, tests and tooling. This checkout is **both the source and a working install** of it on
some boxes - `python3 maintenance/where.py` says which, and you must run it before you assume.

The contract for developing here is `docs/development.md`. Read it. The short version:

## Answer "what is this box" with a command, never from memory

```bash
python3 maintenance/where.py --remote   # the trees, their roles, the running agent, GitHub NOW
python3 maintenance/where.py --check    # exit 1 when a declared role is violated
python3 tests/test_status.py            # what is known-open, anchored to commits/tags/files
venv/bin/python tests/run_all.py        # the gate: the same command CI runs
bash maintenance/pre-push.sh            # the cheap pre-push set (also installable as the hook)
```

If those disagree with any note, chat transcript or handoff you were given, they are right and the
document is stale. `STATUS.json` is the machine-readable open-work ledger; prose notes are evidence,
not authority.

## Invariants

- **The gate is `tests/run_all.py`.** A suite that cannot run exits `77` and counts as red. Never
  silence a suite to make the gate green.
- **The tree the bot runs from must have no uncommitted change to a tracked file** when you push or
  pull. `where.py --check` enforces it; `maintenance/pre-push.sh` runs it.
- **Nothing host-specific is committed**: no `.env`, `config.json`, session/log/state files, notes,
  per-host `tools/` or `skills/`, no host names, paths or tokens. `maintenance/leak-gate.py` decides
  this over the tree, the commits and every reachable blob.
- **Numbers in `docs/tinycmdr-what-it-is.md` are rendered from the tree** by
  `maintenance/measured-block.py`. Never hand-edit inside a `measured:` block.
- **A change to `tinycmdr.py` reaches the running bot only on restart**
  (`bash maintenance/restart-tinycmdr-macos.sh`, or the platform's script in `maintenance/`).
- **One way to do each thing.** If you reach for a new script, note or doc, check first whether
  `where.py`, `STATUS.json`, `run_all.py`, `measured-block.py` or `release.sh` already owns it.

## Flow

`git switch -c <topic>` -> edit -> `run_all.py` -> `pre-push.sh` -> push -> CI (macOS + Linux full
sweep, Windows subset) -> `bash maintenance/release.sh <notes-file>` from a clean, gated `main`.
Released numbers are never rebuilt; `CHANGELOG.md` is the long-form record, one section per release.

## If you were handed a list of findings

Each item becomes an entry in `STATUS.json` with a `state`, an `anchor` git can check (`{file: ...}`
or `{commit: ..., expect: tagged|untagged}`) and a `gate` saying what would close it, then
`python3 tests/test_status.py` must pass. A finding that cannot be anchored goes into the item's
`detail` as a measurement, not into a new prose file.

# tinycmdr changelog (newest first, through 1.0.0, tinycmdr-cli 1.0.14)

> **Renamed 2026-09-20: tinycmdr became tinycmdr.** Entries below were written while the project
> carried the old name, and they keep the name they were written with: a path, task name or
> env var quoted in a 2.5.x entry (`tinycmdr.py`, `tinycmdr_MM_TOKEN`, the "tinycmdr"
> scheduled task) is what that release actually used. Current naming is `tinycmdr.py`,
> `tinycmdr-cli.py`, `tinycmdr_MM_TOKEN`, and the task is `tinycmdr`.

## 1.0.0 - the project is tinycmdr, and this is the first public release (2026-09-20)

The harness has run a six-box fleet since early September, but nobody outside has
seen it, so the first public release starts its own numbering at 1.0.0 instead of
claiming two major versions of private history. Everything below this entry keeps
the number it shipped under.

What changed is the name. `tinycmdr` became `tinycmdr` everywhere: the agent file,
the console build, the installers, the scheduled task, the service unit, the
launchd label and the environment keys. A box that answered to the old name answers
to this one, and nothing about how it works changed.

- entry point `tinycmdr.py`, console build `tinycmdr-cli.py`
- `tinycmdr_MM_TOKEN` became `tinycmdr_MM_TOKEN`. A host's `.env` and its code move
  together, never separately: a restart in between would leave the agent without its
  Mattermost token.
- one agent per folder, unchanged: a second start refuses rather than double-answering
  every DM on one bot token.
- the CLI build scripts locate the tree from their own path instead of `~/<name>`,
  so moving the folder can no longer break a build.
- `tests/test_cost_guard.py` had 13 checks failing on a stub that never learned the
  `cancel=` keyword. Repaired; it is a gate again.



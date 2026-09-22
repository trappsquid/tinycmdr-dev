# tinycmdr changelog (newest first, through 1.0.0)

> **Renamed 2026-09-20: tinycmdr became Tinycmdr.** Entries below were written while the project
> carried the old name, and they keep the name they were written with: a path, task name or
> env var quoted in a 2.5.x entry (`tinycmdr.py`, `tinycmdr_MM_TOKEN`, the "tinycmdr"
> scheduled task) is what that release actually used. Current naming is `tinycmdr.py`,
> `tinycmdr-cli.py`, `TINYCMDR_MM_TOKEN`, and the task is `Tinycmdr`.

## 1.0.0 - the project is Tinycmdr, and this is the first public release (2026-09-20)

The harness has run a six-box fleet since early September, but nobody outside has
seen it, so the first public release starts its own numbering at 1.0.0 instead of
claiming two major versions of private history. Everything below this entry keeps
the number it shipped under.

What changed is the name. `tinycmdr` became `Tinycmdr` everywhere: the agent file,
the console build, the installers, the scheduled task, the service unit, the
launchd label and the environment keys. A box that answered to the old name answers
to this one, and nothing about how it works changed.

- entry point `tinycmdr.py`, console build `tinycmdr-cli.py`
- `tinycmdr_MM_TOKEN` became `TINYCMDR_MM_TOKEN`. A host's `.env` and its code move
  together, never separately: a restart in between would leave the agent without its
  Mattermost token.
- one agent per folder, unchanged: a second start refuses rather than double-answering
  every DM on one bot token.
- the CLI build scripts locate the tree from their own path instead of `~/<name>`,
  so moving the folder can no longer break a build.
- `tests/test_cost_guard.py` had 13 checks failing on a stub that never learned the
  `cancel=` keyword. Repaired; it is a gate again.

What the package ships has changed too, because the fleet was running numbers this
package never documented:

- the budgets the fleet proved out are now the shipped ones: `llm.max_context_tokens`
  24000 to 131072, `llm.max_turns` 40 to 100 (the code and the reference file disagreed),
  `llm.request_timeout` 600 to 1200, `agent.history_exchanges` 10 to 20,
  `agent.tool_output_max_chars` 6000 to 10000, `agent.notes_max_chars` 4000 to 8000,
  `agent.shell_timeout` 180 to 300. A context ceiling is a bound, not a spend: set too low
  it fails quietly, because the harness compacts and the model re-buys what it already read.
- `agent.tool_carry` is ON. It was held off for thin evidence and an untested failure mode;
  that failure mode now has a guard which re-stats the file an entry came from and says so
  in the payload, and the suite asserts both of its directions. With the carry off, the
  saving was invisible and the reader paid for it.
- `agent.event_log` and `agent.ask_user` are ON, and `ask_user` has a shipped key at all.
  The code had none, so it answered to nothing the reference file said.
- `config.example.json` carries the 21 hardened blocked patterns. The code shipped 21 and
  the reference file shipped 2, and the reference file is the one an installer copies.
- a fresh install on a machine with no domain ended with no scheduled task at all:
  the installer built the account as `$env:USERDOMAIN\$env:USERNAME`, which is
  `WORKGROUP\<user>` there, and that resolves to no account. It now resolves a real one,
  or fails with a message that says what it tried.
- the reference config is now checked against the code by VALUE, not by presence, for every
  shipped key. On its first run it named the two keys that had drifted.



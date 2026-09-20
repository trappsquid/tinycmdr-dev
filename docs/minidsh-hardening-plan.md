# MiniDSH-derived hardening plan (tinycmdr) — 2026-09-19

Source: https://github.com/earthwalker17/MiniDSH read 2026-09-19. Its thesis is "fewer concepts,
stronger invariants"; the value for us is 8 ideas, of which none were implemented on that date.

Ground rules that apply to every stage below (operator rules, all earned the hard way):

```
* state the batch before shipping it: what is in it, which hosts restart, which only get bytes
* never restart a host that has an active long run (its tasks.json `doing` item or recent
  tool calls in the log mean a run is in flight; a restart kills the turn)
* one version per BATCH, not per edit
* bytes on disk are never urgent: a host can carry the new file and keep its running process
* a change to a shipped default is scope, even a one-line one
* on anything that touches a running bot's behaviour: read, measure, report, then ask
```

## Stage 1 — git (necessary-now, no behaviour change)

`~/tinycmdr` is not a repository (`git rev-parse` fails; no `.git`), so every change so far is
only diffable against `.bak-<version>-<stamp>` siblings. That is the safety net the rest hangs
off, and it is what makes "real fix or band-aid" answerable with a diff instead of a story.

```
git init; .gitignore for logs/, sessions/, spill/, state.json, *.bak*, web-token.txt, .env,
  __pycache__/, dist/, tools/ (custom tools are per-host), notes.md, tasks.json, atlas.md,
  knowledge/ (per-host imports), skills/.imported-unused/
first commit: tinycmdr.py + tinycmdr-cli.py + maintenance/ + tests/ + docs/ + config.example.json
discipline: one commit per release batch, message "2.5.NN: <what changed>", the hash recorded
  in the release report
do NOT commit: any secret (web tokens, API keys, .env), any per-host state
```

## Stage 2 — a gated budget on the always-on payload (necessary-now, no behaviour change)

Rule from the article: "a convention that is not gated does not hold". Evidence: the 20 core
tool schemas had grown to 13,898 chars (~3,474 tokens) of prose with nobody watching; the
2026-09-19 diet took it to 13,249 and the *parameter* prose is still the bigger half.

```
add to a suite: fail when json.dumps(select_tool_schemas(None)) exceeds a budget, and when any
  single tool description exceeds a per-tool cap
budget: current value + headroom, the number written in the test with the date it was measured
note: the static block sits in the CACHED prefix (evidence: server checkpoints reuse 99.99% of a
  79k prompt), so this saves a per-run prefill, not a per-call cost. Do not oversell it.
```

## Stage 3 — startup line: what this host can actually enforce (small, needs operator's go)

The article's rule is that "no backend" is a supported, honestly reported state. We run with
`blocked_patterns` empty and no write fence, which is fine on his boxes, but it is implied
rather than written down. Closest existing thing: `_launch_warning`, which speaks about one
command shape (a server started as a child of the bot joins its cgroup).

```
one line at start: lane (Mattermost/web/cli), model route, blocked_patterns count, whether the
  process has a memory ceiling (and its size), spawn backend if any
```

## Stage 4 — the event log (the real job; needs scoping and the operator's go)

The substrate for everything we currently argue about from proxies.

```
why: edit outcomes are unmeasurable today (the log records "-> N chars", never OK/ERROR), so
  rework figures (18/287 on the manager box, 12/53 on .47) are proxies. Also buys: resume after a kill
  (durable-before-action), fork-at-event, replay-without-a-key tests, and a real audit view.
shape to scope first, then decide: event kinds (model call, tool call + outcome, edit with
  paths, approval/ask, operator steer, run start/end), one append-only JSONL per session next
  to sessions/, what derives from it (history, metrics, audit), and the per-turn cost.
constraint: "model-visible <=> logged" is checkable and worth keeping: what the model was shown
  must equal what the log derives.
do NOT start this without a scoping doc the operator has read.
```

## Deferred with reasons

```
session lease (their "a second process cannot resume a session another one holds"): we have a
  PROCESS guard only (tinycmdr.lock, one instance per box). A session lease matters once a
  resume exists, i.e. after stage 4.
tiny tool surface: find_tools already exists and core_tools is configurable (13 default names,
  a host may replace the list). Measure discovery calls before shrinking anything.
replay-from-log tests: blocked on stage 4.
skip: the TypeScript rewrite, plugin/kernel seams, 35 composition rows, JSON-RPC. Their own
  admission is that the project is an experiment in concept count, not a template for an ops
  harness that has to work on six hosts.
```

## How to invoke this

Ask for "the MiniDSH hardening plan" (or read this file). The scheduled job is named
`minidsh-hardening`; it wakes on 2026-09-20 10:00 local, does stages 1-2 (no behaviour change),
and stops to ask before stages 3-4.

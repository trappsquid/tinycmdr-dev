# The event log — scoping doc (stage 4 of the MiniDSH hardening plan)

Written 2026-09-19, after stages 1-2 (git, the gated schema budget) and stage 3 (the
startup capability line) went to the fleet. Stage 4 is the real job and the plan says it
does not start without a scoping doc the operator has read. This is that doc. It ends with
five questions; nothing is built until they are answered.

## What this is for

Two questions we answer from proxies today, and one invariant worth keeping.

1. **Did that action work?** An edit's outcome is unmeasurable: the log records a call and
   a size, never a verdict. So the rework figure (18 of 287 edits re-read the same path on
   the manager box) is a proxy built on call SHAPES, not on outcomes.
2. **What was the model actually shown?** Nothing on disk can reconstruct a payload, so
   resume-after-a-kill, fork-at-event and replay-without-a-key are all unavailable, and an
   operator asking "what did it do at 14:20" gets a story rather than a record.

## What today's log can and cannot answer (measured on the manager box, 2026-09-19)

```
tinycmdr.log          34,570 lines / 4.3 MB, of which 6,446 are tool-call lines
tool-call line        median 184 B, and CAPPED around 211 B: long arguments are cut
result line           median 183 B, and the whole of it is "-> N chars"
per-call verdict      0 lines carry one
"OK" in the log       11,900 occurrences - every one of them HTTP "200 OK", not an outcome
edits with no outcome edit_file 287 + write_file 267 = 554 on this host alone
sessions/             7 files, 125 KB total (the conversation as JSON, no outcomes)
```

So the log is a transcript of ACTIONS with the arguments cut off and the results reduced to
a size. It is good for "what did it call", useless for "did that work" and "what did the
model see".

## The shape to scope

One append-only JSONL per session, `sessions/<key>.events.jsonl`, next to the history it
describes.

```
run.start      run.end        status, steps, secs, tokens in/out, why it ended
model.call                    endpoint, model, prompt_tokens, cached_tokens, completion,
                              latency, finish_reason
model.empty_turn              the retry path, with what the retry did
model.error                   a 429, a dead stream, a refused connection
tool.call                     name, args_digest, args_len, scrubbed args
tool.result                   ok | err, exit code, bytes, digest, spill path when it spilled
edit                          path, bytes in/out, digest before and after, verifier verdict
ask / approval                the ask_user door: question, answer, waited seconds
steer                         operator text that arrived mid-run
note / task                   a write that changes the next prompt
compact / spill / transcript  what was cut, and where the full text went

every event:     ts, session, run_id, seq, kind, then the kind's own fields
every string:    passed through scrub() - the log already does this for tool output
```

Kinds are deliberately few. A kind earns its place by being something we would want to
count or replay, not by being something the code happens to do.

## What derives from it

- **metrics**: an edit that lands is `edit` + `tool.result ok`; rework is the same path
  edited twice with the first not ok. The proxy becomes a count.
- **audit**: one file to read for a session, in order, with outcomes.
- **history** (the risky one): the model-visible conversation itself. This is where the
  invariant lives and where a bug costs a conversation, so it comes last and behind a flag.
- **resume / fork / replay**: consequences of (history), not deliverables of this stage.

## The invariant, and the test that enforces it

**model-visible <=> logged.** Every message the model was shown has an event, and rebuilding
the history from the events alone reproduces the payload byte-for-byte. It is checkable, and
the shape of the check already exists: `tests/test_disclosure.py` stubs the POST, runs a real
turn and inspects what the request carried. The same stub compares the rebuilt payload
against the sent one, so a divergence fails a suite instead of being discovered by an
operator mid-conversation.

## Cost, measured so nobody has to guess

```
events per run     20-60 tool calls + model calls + lifecycle = ~40-120 events
bytes per event    150-400 B (the log's own median line is 183 B)
size per run       ~15-45 KB, a few percent of the conversation it describes
prompt cost        ZERO - it never enters a prompt, so the cached prefix is untouched
disk               sessions/ is 125 KB today; an event log 10x that is still under 2 MB
CPU                a JSON encode per event, microseconds
```

The cost that matters is not bytes, it is a write that could block the run loop.

## Three failure modes to design against

1. **A blocked write must never wedge a run.** The 2026-09-18 deadlock (a notes lock held
   while the same lock was taken again) froze a bot and the tool batch waited for ever. So:
   append-only, one file per session, a lock around the file handle only, and a failed write
   logs once and lets the run continue.
2. **Durable before an action that cannot be undone.** An edit or a shell command gets its
   event written and flushed BEFORE it runs, so a kill mid-action leaves a record of what was
   about to happen. Everything else may buffer.
3. **Tool calls run in BATCHES.** The carry store's race (two batched writes, one lost temp
   file) is the precedent: the writer must assume concurrent calls.

## Rollout, in order (each step separately agreeable)

1. **Shadow**: write the events, read nothing from them. Compare against `tinycmdr.log` for a
   day. This is the only step that wants to be fleet-wide, it changes no behaviour, and it can
   sit behind a config key that defaults to off until the operator turns it on.
2. **Derive the metrics and the audit view** from the events. Still no behaviour change.
3. **Only then** consider deriving history from the events: one host, behind a flag, with the
   payload-equality test as the gate.

## Not in this doc

- a **session lease** ("a second process cannot resume a session another one holds"): it is
  meaningless until a resume exists, i.e. after this stage.
- **fork and replay as features**: consequences, not deliverables.
- the TypeScript rewrite, plugin/kernel seams, the 35 composition rows, JSON-RPC: skipped,
  as the plan already says.

## Questions for the operator, before any code

1. **Redaction**: full arguments (a `.env` read or a token-bearing command lands in the file),
   or a digest plus scrubbed text? My default is scrubbed text plus the digest, so the file is
   readable and cannot leak.
2. **Retention**: keep the last N sessions per host like `spill_keep`, or keep everything until
   someone prunes it?
3. **Location**: `sessions/<key>.events.jsonl` as proposed, or one file per host under `logs/`
   (easier to grep across sessions, worse for a resume)?
4. **Scope of the first cut**: shadow-only, or shadow plus the derived metrics view in one
   batch?
5. **Drift already found by the stage-3 line** (the manager box's default model name is `cloud` and
   the Linux test box's is `deepseek-v4-flash`, both pointing at the LAN box, which is the name-collision
   trap; the MacBook carries 3 blocked patterns where every other host has 21): does that get
   its own small batch, or does it ride this one?

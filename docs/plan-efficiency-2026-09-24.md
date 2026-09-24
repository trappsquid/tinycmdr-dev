# Efficiency review - STAGED PLAN (start a fresh session here)

Operator's ask, 2026-09-23 night: review dormant prompt lines, clean dormant excess that makes
the agent re-read stale build data, and make "fix with 0 prompt bytes" the standing bias.
This file is SELF-CONTAINED: the old handoff chain (09-20 .. 09-23c) was deleted from the tree
(git history keeps it: `git log --oneline -- docs/handoff-*`) and its live content is folded in
below. Do not go looking for those files. Current state + hashes: `docs/handoff-2026-09-23d.md`.

## 0. The standing rule (effective now, every fix)

```
FIX WITH ZERO PROMPT BYTES.
1. A runtime fix (result text, tool behavior, gates) beats a prompt line. Tonight proved two
   real bugs fixed with +0 prompt bytes and +0 schema bytes (measured A/A).
2. A prompt line is PERMANENT PER-TURN RENT on every host, every turn. Admit one only with:
   (a) a measured miss it fixes, (b) a runtime alternative considered and rejected,
   (c) its token rent stated in the commit.
3. Same rent logic for tool schemas, carry, notes, atlas - anything that rides the payload.
4. Measure before/after with the same A/A staging (identical config/tools/skills):
   prompt-probe or build_system_prompt() + est_tokens + select_tool_schemas().
```

## 1. Measurements ledger (2026-09-23, same staging: real config + tools + skills)

```
system prompt        BASE aa48a81 (09-22)  17,822 ch / 5,241 est-tok
                     HEAD (07:59x)        20,972 ch / 6,168 est-tok   (+3,150 ch in 2 days)
tool schemas         8,490 ch (14 visible tools) / ~2,500 est-tok; 7 hidden tools cost 0
real turn floor      the Windows test box server-side prompt_tokens: 14.4-14.5K for a 0-step turn
payload growth       NONE with work: 9-step/125K-token run peaked at 16.7K ctx
ttft                 5.4-6.9s warm (server cards); 26.9s cold/contended observed once
est_tokens           len//4, undercounts code-dense text - trust server prompt_tokens
my-session overhead  skills: tinycmdr-build-release SKILL.md 99KB + fleet-access 52KB are
                     loaded per session (~40K tokens) - the biggest stale-read surface we own
```

## 2. Work items, ranked

```
necessary-now
  A PROMPT DORMANCY REVIEW. Audit every line of build_system_prompt() against the fleet logs:
    which route_hint / answer-the-miss / claim-clause lines have FIRED in the last 7 days
    (each mechanism logs itself - grep the hosts' logs for those strings). Cut or demote what
    never fired or fired below value; every keep/cut cites its firing count. Target: prompt
    back to <=17.8K ch (the 09-22 base) or below. He reviews the line diff (standing ask).
    Gate: A/A prompt measurement + full sweep + one drive round (WO11) green.
  B SKILL CURATION (the biggest win for OUR token burn). tinycmdr-build-release 99KB ->
    SKILL.md <=30KB with deep history/superseded states in references/ (lazy-loaded via
    skill_view(file_path)); fleet-access 52KB -> <=25KB same shape. Keep: triggers, current
    facts, standing lessons, doors. Move: war stories with their lesson already extracted,
    pre-rename history, superseded fleet states. Rule going forward: one lesson per line, and
    a skill section that has not been true for a week goes to references/ or is deleted.
  C THE 0-PROMPT-BYTES RULE AS CODE: the drive-method skill has it (added 2026-09-23); add a
    check to the release gate that prints prompt+schema chars with the build hash
    (maintenance/publicgate.py or the packager), so growth is visible per cut.

latent
  D TOOL SCHEMA PROFILE: 2.5K est-tok for 14 visible tools. agent.core_tools is the knob;
    define the lean profile (per audience) and the default visible set. No code expected.
  E CARRY CAP: ~2.5-3K tokens/turn of the 14.5K floor after 23 runs (10K chars). It buys
    no-refetch (measured) - tune tool_carry_chars down, not off. Measure saved-refetch value
    from `carry:` log lines first.
  F OPEN ITEMS FOLDED IN FROM THE OLD HANDOFF CHAIN (they lived in deleted files):
    - listener deafness (a bot alive-but-deaf 4h: the ws listener died, the process held).
      Candidate: listener/heartbeat liveness that exits the process so the supervisor
      relaunches. (measured 2026-09-23, the other Windows box)
    - full-repo scan class: the loop guard misses ~40 status-shaped shell calls; candidate
      "name the door" answer on the 3rd consecutive status-shaped shell call.
    - tools/later.py appeared in the manager box's provenance and is gone - one look at how.
  G TONIGHT'S OPEN ITEMS:
    - .20 runs llm.allow_cloud_fallback=false vs fleet standard true - OPERATOR'S CALL.
    - bot message pickup latency 12-79s (2 of 3 orders waited on the 60s catch-up sweep, not
      the websocket). Measure again before touching.
    - a run answered Get-Date from the 4-minute-old carry instead of calling the tool (model
      behavior; a fresh random-number call forced it). Prompt-level if it repeats.
    - doubled-batch root cause (model repetition vs MTP): the harness suppresses duplicates
      now; watch the "duplicate within one batch suppressed" log line.

optional
  H DOCS POLICY (already applied once): one handoff in the tree at a time - a new handoff
    deletes its predecessor on commit (git keeps them). Plan docs live until done, then go.
  I LOG ROTATION: tinycmdr.log grows forever on every host and is read by drive sessions
    (1.1MB on .20 after one day). Rotate by size, keep the tail; drives already read by
    offset. Same for logs/bot-stdout.log. spool of sessions/*.events.jsonl: age-out policy.
  J hermes-tmp: sweep logs and probe outputs deleted 2026-09-23; keep it that way after each
    session (scripts stay, outputs go). fleet-mig (360MB, incl. a 193MB video inside the
    the Windows test box capture) is the deliberate pre-migration archive - OPERATOR'S CALL to keep.
```

## 3. Cleanup already done (2026-09-23 night, 36.8MB / 80 items)

```
the manager box tree     __pycache__ 6.1MB, config.json.bak-tier x2, superseded docs chain
              (handoff 09-20/22/23/23b/23c, test-plan-09-21, minidsh scope, eval baseline)
the Windows test box     12 x .bak-push build backups 7.7MB, logs/wedge-dump-*, tmp/ probe files,
              maintenance/ drive remnants (mm_post_*, mm_discover, wo7-log) - the restart
              door (restart-tinycmdr.ps1) kept
hermes-tmp    old sweep logs, s13/s20/s47 logs, a pulled events copy, pkgprobe dist zips,
              regenerable probe builds, one-shot patch/test scripts from tonight
untouched     dist/, uploads/, spill/, sessions/, skills/ (the bot's), tests/, tools/,
              fleet-mig, all fleet hosts except .20
```

## 4. Done criteria for this review

```
- prompt <=17.8K ch with every surviving line citing a 7-day firing count (A)
- skills per item B's targets, load-test: a session that needs tinycmdr ops loads <=30KB
- the release gate prints prompt+schema chars (C)
- one green drive round (WO11) after the trims prove nothing regressed
- he signs off the prompt line diff (standing ask: he reviews prompt lines)
```

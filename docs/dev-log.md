# Development log for the tinycmdr tree

Engineering notes for work on this build. NOT the bot's memory: `notes.md`
is the file the bot re-reads in every prompt and it holds only its own
durable facts (cap `agent.notes_max_chars`). Appending long entries here
instead is the whole point of this file existing.


**2026-09-22: this log was renamed to tinycmdr by byte substitution.** Entries written
before that date describe the same tree and the same fleet under the names they had
then, and their file/path/env names were rewritten in place, so a command or a hash
quoted in an older entry may not be literally what was run or built at the time.
Treat the git history and `dist/` archives as the record of what each release shipped.
## moved out of notes.md 2026-09-17 (it is the bot's prompt memory, not our log)
- [2026-09-17 17:25] ITEM 7b SECOND SAMPLE + SHIPPED TO the manager box. Both samples agree on the metric the item exists for: runs 2+3 source text bought, all routes counted, off 25,191 -> on 5,082 (80% less) and off 13,059 -> on 2,908 (78% less). My declared proxy metric (read_file windows only) missed in BOTH samples - 2->2 and 1->2 - because the re-acquisition rides on shell/execute_code as much as on read_file; that was my error, declared twice, and the corrected metric is the one now recorded. Wall clock mixed (45% less, then 16% more): noise at n=2, not claimed. Payload clause holds with the 8k bound (carries measured 6,364 and 6,007 chars). Answers spot-checked by hand in both legs, correct on every fact checked. Added a per-run log line (carry: N chars ... run R) so the fleet's own logs become the next measurement, then pushed to the manager box ONLY (tree 99255dc40e8fea34; verified: 1 instance, health ok, new Mattermost connect 17:18:55) - the Windows test box and the Linux test box stay on the ledger/1e/item-6 build until that data lands. Also fixed the race the acceptance log exposed (WinError 5 then the plain-write fallback from two batched tool calls writing the store at once) with _CARRY_LOCK + a 48-thread test; suite 28 checks green, CLI regenerated (561e8d3a88f9732a), all 13 suites 0 FAILS, CLI 145 + 188 ledger.
- [2026-09-17 17:45] 7b PROOF EXPERIMENT DECLARED, BEFORE RUNNING IT. The operator asked for testing that actually proves the benefit; the shipped change has so far rested on 4 measured runs per leg and a hand spot check. Design: 8 PAIRED samples, each sample = one setup run + one measured run in its own session, the same two prompts in BOTH legs, legs differing only in agent.tool_carry, leg order alternating per sample so drift cancels, fresh process per run. 32 model runs total (16 per leg). PRIMARY: prompt tokens billed in the measured run, summed over every model call in it - the carried block rides in every call of a run, so if the carry costs more than it saves, this is where it shows; the experiment can refute the shipped change. SECONDARY: source text bought in the measured run, counted across ALL routes (read_file, shell, execute_code); tool calls; wall. GUARDRAIL: answer grade, automated against a 16-fact ground truth (the digest keys and their defaults, the three caller functions with their call lines, digest_output and the shapes table with their lines, and what raw does). A leg whose answers are worse FAILS whatever the token count says. DECLARED BAR: measured-run prompt tokens down >= 25% paired, source text bought down >= 50%, mean answer grade no worse by more than 0.10, with the sign test on paired differences - 8/8 or 7/8 pairs better is the bar for calling it proven. Anything less gets reported as unproven, and if tokens go UP the shipped change comes off the manager box.

- [2026-09-17 18:05] CAUSE OF "the bot seems much more stupid" FOUND, AND IT WAS MINE. I had been appending the engineering log to ~/tinycmdr/notes.md all day - which is not a dev log, it is the BOT'S PROMPT MEMORY, capped at 4,000 chars and re-read in every prompt. Two of my entries (1,504 and ~1,300 chars, written by a direct file append so the 1,200-char per-note clip never applied) took the entire budget, and the curator evicted 28 of the bot's own entries at 17:24:52: release URLs, build shas, the manager box tooling quirks, the fleet build identity. Its memory went from ~30 facts to my two, mid-shift. Fix: my entries moved here (docs/dev-log.md, created for this), and the bot's newest own entries restored out of notes-archive.md into notes.md - 6 entries, 3,699 chars, inside the 4,000 cap - verified through the harness's own _parse_notes/_render_notes so the next prompt carries its facts and none of mine. Rule recorded in the tinycmdr-build-release skill. Separately confirmed: item 7b's carry has still never fired live on the manager box (0 log lines; the run in flight started with an empty store), so it is not implicated in the behaviour the operator saw; my own proof experiment was loading .47 during exactly that window (bot streams at 18-29 tok/s vs ~90 solo), which I have stopped.

- [2026-09-17 18:20] MEMORY GUARD BUILT AND LIVE (item 7c), as the structural answer to "stop poisoning our bots". Four layers: provenance (notes-authored.json, hashes of entry TEXT so re-renders don't shift identity, bootstrapped from the file so existing memory is never mistaken for foreign); precedence (curate_notes evicts FOREIGN entries first whatever their age, so a flood cannot push the bot's facts out); notice (a WARNING naming the count and the oldest foreign entry, and where notes belong); lane marker (one line in every render saying what the file is, ~100 chars/turn). tests/test_notes_guard.py = 22 checks on both builds, including the precedence proof (tight cap keeps the bot's entries, none of the flood) and the invariant that no bot fact is archived while an outsider's entry stays resident. All 15 suites 0 FAILS, CLI 145 + 188 ledger. the manager box restarted onto eff369b8e42d4af5, memory restored (6 entries, 3,827 chars) and the sidecar seeded. The rule also went to memory: engineering notes go to docs/dev-log.md, never into a bot's prompt-surface files. Honest limit: a guard cannot stop another process writing the file - it guarantees the bot's facts win and that someone notices.

- [2026-09-17 19:05] RELEASED 2.5.8 + CLI 1.0.8 TO ALL SIX BOXES, and commissioned the site update. Release hash 8e08775c30f7ac3d (tinycmdr.py, VERSION 2.5.8); CLI 1.0.8 generated from the same source. Contents: ledger integrity (atomic state writes + salvage of a damaged ledger), the memory guard (7c), tool-write verification (1e), the broad-scan ceilings (6: 60 s per command, and the run budget ON at 120 s - this entry said 'off by default', corrected 2026-09-17 after the security-onion box checked the shipped defaults), the atlas listing rebuilt from disk, one sessions/ writer, and the carried tool results (7b) present but OFF by default - enabled per host, and the fleet manager keeps it on deliberately so the live evidence keeps accumulating. Mechanics that had to be right: the public gate REFUSED the first build (a comment of mine carried the host name the manager box, and a pre-existing CLI test used a private address - both now neutral); the FIRST clean-unpack run on Linux caught a platform-specific failure in my own cost-ceiling test (an exact integer where real elapsed seconds make it 119 vs 120) - exactly what the never-publish-before-a-cross-platform-unpack rule exists for, and it cost one rebuild instead of a version. Rollout: .20 + .9 files pushed over C$, .13/.47 over ssh with systemd restarts, Mac over scp + launchctl kickstart, the manager box by its own restart script; fleet-version-report shows all six at 8e08775c30f7ac3d 'in sync' and every /api/health answers {"ok": true, "version": "2.5.8"}. the other Windows box's restart script reported 'FAIL: no new supervisor reported a ready bot within 180s' while the bot was in fact up - the documented false alarm; verified by health, not the exit code. Archives: dist/ + Z:\VPS Admin (2.5.8 win/linux/macos + CLI 1.0.8 win/linux, each with a .sha256; current + 2 prior kept, older moved to .archive/). CHANGELOG carries 2.5.8 and CLI 1.0.8 entries. Publish: brief wo-258.txt + the five archives staged in the security-onion box's inbox, work order sent through Mattermost as the operator's account (mm_say.py) with the baseline wo-reply.json hash 32cdbc4b... recorded BEFORE sending, and a watcher armed. Fleet push was done FIRST so no restart interrupts the publish job.

- [2026-09-17 19:40] RUN BUDGETS RAISED FLEET-WIDE (operator's report: bots stop mid-job and he has to type "continue"). Evidence from the fleet manager's log, real sessions only: 17 runs ended 'step budget exhausted' or 'time budget exhausted - forcing final report', at the then-current caps (40 steps in the oldest, 60, 100, 101; and 35-41 minutes), plus two runs that burned the clock with 2 and 9 steps because the work was slow rather than numerous. The caps only bind when work is unfinished, so they are runway, not a target. State found: five hosts at max_steps 100 / max_minutes 35, the MacBook still on the OLD 40/10 - the worst case, and the one a stale config explains rather than the code default (which was already 100/35, and config.example.json matched). Applied to all six hosts via a scripted, backed-up config edit with a read-back: agent.max_steps 250, agent.max_minutes 75, agent.plan_max_steps 24 (was unset -> 12). Backups: config.json.bak-budgets-<stamp> on each host. Every bot was restarted so the config is loaded (windows: kill + scheduled task via maintenance/restart-258.ps1; .13/.47: systemctl restart; Mac: launchctl kickstart; the manager box: its own restart script) and every /api/health answers 2.5.8. Read-backs confirm 250/75/24 on all six. Also raised in the TREE so a rebuilt box inherits it: DEFAULT_CONFIG, config.example.json and the CLI's curated block. That is a build change and rides the NEXT release - no archive was rebuilt, so the published 2.5.8 / CLI 1.0.8 hashes stay valid (the tree is now 2.5.9-dev and the manager box's file hash differs from the fleet's released one; expected drift, the fleet runs the released build with the new config). test_plan.py had a hard-coded 20-step plan assertion that the raise exposed (it now offers MORE than the cap and asserts the cap), fixed and green on both builds; full battery re-run clean.

- [2026-09-17 20:45] PUBLISH OF 2.5.8 VERIFIED, AND A DEFECT IN MY OWN RELEASE NOTES. The security-onion box
  finished the work order (wo-258.txt) at 03:28 UTC and wrote its reply; its own run before that had died at
  'time budget exhausted (21 steps, 2173s)' - only 21 steps but 36 minutes, so the clock was spent on the slow
  local model, not on step count. It was resumed after the budget raise and switched to the cloud alias for
  this job. My own independent check of the result: both pages serve the new versions (2.5.8 / 1.0.8, 17 hits
  each) with the correct sha256 rendered, and the archive downloaded from the real href
  (/files/tinycmdr/tinycmdr-2.5.8-win-public.zip) is 449,716 B, sha256 5b1e2590... - byte-for-byte what was built.
  The share holds the current release with every sidecar validating under sha256sum -c, and superseded 2.5.6 /
  2.5.7 / cli 1.0.6 are in .archive/ under their own version numbers (kept, not deleted).
  THE CATCH: my CHANGELOG said the run-level scanning budget 'ships off'. The shipped defaults are
  agent.command_cost_guard true with agent.scan_budget_seconds 120, so a fresh install has it ON at 120 s. The
  artifact was right and my prose was wrong; the agent published the true behaviour and would not rebuild a
  released hash to hide the mismatch. Corrected in tree: CHANGELOG.md entry, the plan doc's 4j decision line
  (marked superseded, pointing at 4j.1), and the dev-log entry of 19:05. The PUBLISHED page is already right;
  the archive's own CHANGELOG.md still carries the wrong sentence and cannot be fixed without breaking its hash,
  so the next release's entry will carry the correction.
  ALSO CLOSED, found by a new check: config.example.json - the file the installers copy and the README calls the
  full reference - was missing agent.shell, agent.tool_carry and agent.tool_carry_chars, and did not document the
  scan ceilings at all. All five keys are now documented with the values the code ships, and tests/test_cli.py
  grew a check that the reference config parses and matches the code's defaults, which nothing covered before
  (the suites run against their own fixture, which is how I broke the file twice in a row without noticing).
  Legacy: nothing rebuilt, no version bump - the tree is 2.5.9-dev and these ride the next release.
- [2026-09-18 09:30] COMPLETION-ANNOUNCEMENT LOOP, AND THE COUNTER THAT MADE IT LOOK WORSE.
  Found live on the Windows test box during the payload-fix A/B job (the harness-analysis brief, run again on
  purpose on the patched build). The run never stopped working and never emitted a final answer:
  instead the model announced the same conclusion five times and then found one more thing to check.

    narration, in order (all in the operator's DM, 25 minutes apart end to end):
      08:58:41  "Fresh source pass is done; verifying the two claims I intend to make first item,
                 then writing it up."
      09:10:05  "Fresh source pass is complete. Two claims left to pin precisely before I write
                 the analysis."
      09:15:17  "Fresh source pass complete - and it already corrected three claims from my previous
                 notes. Two last checks (defaults, and whether the live build's own suites still
                 pass) before writing."
      09:17:29  "Fresh pass is complete; two claims from my prior notes were wrong. Final
                 verification: whether the eval harness's shared helpers exist anywhere on this box."
      09:23:48  "Fresh pass done - and it invalidated three claims from my own previous notes.
                 Writing the durable artifact, then the report."

  Each announcement was followed by another tool round, not by the report. At 09:26 the run was at
  24 model calls / ~65 tool calls / 59 minutes / peak context 118.5K, with no cap event (the 250-step
  and 75-minute caps had not fired), no duplicate-call refusals and no loop-guard nudge. So:

  * The loop guard cannot see this class. It keys on identical tool calls (same tool + same args +
    same output) and on repeated nudges; every call here was distinct - source reads, suite runs,
    web fetches, ledger writes. The spin is in the model's own "one more check" attractor, not in a
    repeated action, and no guard is keyed on "the model says it is finished".
  * Proposed fix (next release): a DELIVERY guard. Count the model's own completion announcements
    (narration matching done/complete/finished/"writing the report" while no answer has been emitted
    for K turns). On the Nth, append one explicit turn - "you have announced completion N times and
    no answer has been delivered; emit the final report now, no further tool calls" - and if the next
    turn still is not an answer, take the existing forced wrap-up path (the same one the budget uses,
    `_payload(state=False)` + `final_max_tokens`). The wrap-up machinery already exists; what is
    missing is a trigger that is not the budget.
  * Interaction with the new auto-continue, worth fixing in the same pass: `budget_continues()` now
    returns True whenever the plan has open steps, which is exactly the state this attractor leaves
    behind. If the cap fires while the model is announcing completion, the run gets up to two more
    75-minute segments instead of the wrap-up. Rule: do not continue a run that has announced
    completion K times without delivering, and do not continue one whose plan has not moved in the
    whole segment.

  SECOND DEFECT, and it is the one that made the loop look like 780 steps: `ProgressReporter.progress()`
  increments `self.steps` for EVERY callback, including the 4-second "generating" heartbeat that
  `_on_delta` fires while the model streams. `checkin_steps` is compared against that same mixed
  counter, so the check-in line printed "step 780" for a run that had made ~65 tool calls, and
  check-ins fire on heartbeat ticks (~every 2 minutes) rather than at 30 real steps. The operator
  (and I, for a minute) read that number as "it looped 780 times". Fix: count only real steps in that
  counter - or stop calling it a step - and keep the heartbeat out of it.

  Nothing here changes the A/B result: the payload placement fix and the repeat-read map both measured
  as designed (52% -> 81% median prefix reuse, re-prefilled tokens per call ~27.7k -> ~5.5k, first
  delta 60-231 s -> 7-67 s). This is a separate behaviour, found by the same run.
- [2026-09-18 09:55] THE WEDGE, WITH A STACK TRACE: A SELF-DEADLOCK IN THE NOTES GUARD, AMPLIFIED BY
  AN UNTIMED TOOL BATCH. Found on the Windows test box while the A/B job was running: output stopped at
  09:32:15 mid-run, the model server showed every slot idle, and the bot stayed alive (its 60 s
  listener poll kept going) but did nothing. Diagnostics, all read-only:

    CPU              34.5 s total for the whole process since the 08:25:59 start
    child processes  none (no python/powershell/cmd children) - not waiting on a subprocess
    sockets          NONE to a LAN address (so no model call is open); only the Mattermost poll
    files            the artifact WAS finished: docs/harness-gap-analysis-2026-09-18.md,
                     21,212 B, 306 lines, written 09:30:42
    harness          09:40:30 WARNING "stall: no output ... for 8 min" (the 8/20 min watchdog)

  `py-spy dump --pid 29056` (installed for this; dump saved at
  C:\tinycmdr\logs\wedge-dump-20260918-0950.txt) named it in one shot:

    Thread-17 (_worker)  -> wait (threading.py:355)
                            result -> _result_or_cancel -> result_iterator
                            run (tinycmdr.py:5902)          <- the tool batch
                            _handle (tinycmdr.py:7489)      <- the Mattermost run
    ThreadPoolExecutor-21_0 (the tool worker)
                         -> notes_authored (tinycmdr.py:2553)
                            record_authored_note (tinycmdr.py:2582)
                            tool_remember (tinycmdr.py:2694)
                            _exec_tool (tinycmdr.py:5444) -> work (tinycmdr.py:5897)

  ROOT CAUSE, deterministic: `_NOTES_AUTHORED_LOCK = threading.Lock()` (2521) is not reentrant, and
  `record_authored_note()` takes it (2581) and then calls `notes_authored()` (2582), which takes the
  SAME lock (2553) on its bootstrap path. The first `remember` in a process therefore self-deadlocks
  whenever the notes sidecar has not been loaded yet. On this box that was guaranteed:
  `notes-authored.json` does not exist and notes.md (1.9 KB) sits under the 4,000-char cap, so
  `curate_notes` - the other caller of `notes_authored()` (2626) - had never run. The worker holds
  the lock forever.

  AMPLIFIER, and the reason it is not one hung tool but a dead run:
  `list(ex.map(lambda p: work(*p), enumerate(tool_calls)))` (5904) waits with NO timeout, so the run
  thread blocks behind the hung worker for ever. The stall watchdog only warns and abandons; the
  session lock stays held, so every later message to that conversation hangs the same way. That is
  the "wedged run leaves the bot DEAF" class recorded on 2026-08-... (fleet-access, 2026-09-18
  entry) - the earlier suspicion (a CLI console-hold blocking on input()) was a different instance
  of the same symptom, and this is a cause that needs no external trigger at all.

  FIXES (both small, one release):
  1. `_NOTES_AUTHORED_LOCK = threading.RLock()`, or hoist `notes_authored()` out of the locked
     region in `record_authored_note()`. A guard whose whole job is to protect memory must not be
     able to freeze the bot that writes it.
  2. Time-box the tool batch: `as_completed(futures, timeout=shell_timeout + request_grace)` (and a
     per-future fallback result "ERROR: tool timed out after Ns"), so a hung tool becomes a tool
     result the model can react to instead of the end of the run. Every tool already has a shell
     timeout; the batch that waits on them has none.
  3. A regression test that calls `record_authored_note()` FIRST in a fresh interpreter - the
     current guard suites pass because they touch `notes_authored()` earlier and so never take the
     bootstrap path. Run it under a watchdog so a regression fails loudly instead of hanging CI.

  Nothing here changes the A/B numbers measured before the wedge (52% -> 81% median prefix reuse,
  ~27.7k -> ~5.5k re-prefilled tokens per call, first delta 60-231 s -> 7-67 s): the job produced
  its artifact and then died on its own notes guard.
- [2026-09-18 10:30] OVER-CAP TOOL RESULTS NOW SPILL INSTEAD OF SHREDDING (the analysis item, built).
  The item came from the harness's own analysis on the Windows test box, which proved it with markers: a
  30,045-char tool result lost ~20,100 middle characters to truncate_middle, and re-issuing the
  same call with `raw=true` lost the identical middle. Verified in source before building:
  truncate_middle (974) writes a "N chars omitted" marker and no pointer, and the four cap sites
  all call it AFTER digestion (shell 2203, execute_code 2247, read_file 2355, fetch_url 2445), so
  `raw` cannot reach it - it bypasses digestion, not the cap.

  Built: `cap_output(name, text, label, limit)` replaces truncate_middle at those four sites. Over
  the cap it writes the whole text to `spill/<stamp>-<tool>-<sha8>.txt`, rotates the folder
  (`spill_keep`, default 50, `spill/` added to _ATLAS_SKIP_DIRS) and returns head+tail plus a
  pointer that names both routes to the middle (`read_file` with offset/limit, `search_files`
  with path) and says plainly not to re-run the command. Config: `spill_output` (True),
  `spill_keep` (50), both documented in config.example.json because test_cli requires every
  shipped key to be there. It fails SOFT: no spill dir, full disk, anything - the write is
  wrapped and falls back to the old truncation, because a disk problem must never break a run.

  REFINEMENT FOUND WHILE TESTING, worth knowing before the next tool is added: for `read_file` the
  DIGEST fires first and the cap never binds. A 4,000-line / 97,481-char file came back as 1,175
  chars - digest_output matched a "log file" shape and kept the newest 40 lines - and that path
  HAS an escape hatch in its own header ("re-run the same command with raw=true"), which is why the
  cap's missing hatch only shows on routes where the digest does not match a shape. Both lossy
  steps are now recoverable, and the regression check for this item uses the route that lost data:
  `read_file` with `raw=true` over the cap must hand back a spill pointer whose file holds all
  4,000 lines.

  `tests/test_spill.py`: 14 checks - middle present on disk and byte-identical, small results
  untouched, `spill_output: false` restores truncation, an unwritable spill degrades to truncation
  without raising, rotation bounds the folder, and the real call site end to end. Green on both
  builds, and the whole battery re-run clean (spill, digest, stall 207, plan, verify, disclosure,
  atlas, checkin 84, cost_guard, notes_guard, tool_carry, eval_grading, webui; ledger 225 bot /
  188 CLI; test_cli 149). NOT pushed to the Windows test box yet on purpose: its third-pass analysis is
  reading that source, and a build that moves under it would make its line numbers worthless.
- [2026-09-18 10:45] THREE MORE FROM THE ANALYSIS, BUILT: CODE SEATBELT, TRANSCRIPT SINK, DELIVERY GUARD.

  1. THE SEATBELT COVERS CODE (its finding G3, the real half of it). `is_blocked()` had exactly one
     call site, in `tool_shell`, so every entry in blocked_patterns was one `execute_code` away from
     being walked around - and the system prompt admitted it. `tool_execute_code` now checks its own
     source text and refuses with the same message shape as shell. Both places that described the
     hole (the DEFAULT_CONFIG comment and the system-prompt sentence) now describe what is actually
     true instead: the check is on the TEXT, so code that assembles a command at runtime is
     invisible to it. A seatbelt, still not a boundary.
     FOUND WHILE TESTING, and it is the reason the test was worth writing: the pattern itself was
     broken. `rm\s+-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*\s+/(\s|$|\*)` required whitespace, end-of-line or
     `*` after the slash, so `os.system("rm -rf /")` - the exact shape code takes - did not match:
     the string ends on a quote. Both rm patterns now use a negative lookahead for a path character
     (`/(?![A-Za-z0-9_./~-])`), which means "the root itself, not a path under it": verified on
     `rm -rf /`, `rm -rf / --no-preserve-root` and `'rm -rf /'` (blocked) against `rm -rf /tmp/x`
     and `rm -rf ./build` (not blocked). NOTE: every host's config.json carries its OWN copy of
     blocked_patterns, so this improvement reaches fresh installs and the CLI; the five running
     hosts keep the old line until their configs are pushed.

  2. THE TRANSCRIPT SINK (its G2, the cheap half). Compaction shrinks and deletes and the session
     file holds the compacted version only, so the evicted middle was unrecoverable - and with
     auto-continue a job can now run three segments in ONE context, so this stops being theoretical.
     `_save_transcript(key, messages, reason)` appends one JSONL line per non-system message to
     `sessions/<key>.transcript.jsonl` BEFORE anything is cut, called from `_compact` and
     `_force_shrink` (both now take the session key). `run_state["compactions"]` counts them and
     `fmt_usage` prints "N compaction(s)". Config: `session_transcript` (True). The summarizer half
     of G2 is deliberately NOT built: it needs a run that actually reaches the budget before it can
     be judged.

  3. THE DELIVERY GUARD (my finding from the wedge, not its list). Detector `_COMPLETION_RX` matches
     an announcement of completion; the loop counts announcements that arrive WITH tool calls still
     queued. At `deliver_after_announcements` (3) the run gets one explicit turn demanding the
     report; two announcements later the wrap-up is forced, and the answer says "Delivery guard ...
     you kept announcing completion without reporting" instead of pretending a budget ran out. The
     auto-continue branch also refuses a further segment once the count is reached, because the
     announcement loop is exactly the state a bigger budget feeds. Tests in test_stall.py drive a
     scripted run that announces completion eight times with distinct calls: the nudge reaches the
     model, the guard fires, and no continuation is granted.

  Also fixed while in there: `ProgressReporter.progress()` counted the 4-second "generating"
  heartbeat as a step, so the check-in line printed "step 780" for a 65-call run and check-ins
  fired on heartbeats instead of at real steps.

  Suites: test_stall 214 (two new: the seatbelt, the delivery guard), test_transcript 9 checks (new
  file), and the whole battery re-run clean on both builds (spill, digest, plan, verify, disclosure,
  atlas, checkin 84, cost_guard, notes_guard, tool_carry, eval_grading, webui; ledger 225 bot /
  188 CLI; test_cli 149). Still not pushed to the Windows test box: pass 3 is reading that source.
- [2026-09-18 11:10] 2.5.9 FROZEN AND PUSHED TO ALL SIX HOSTS. No site publish - this tree is the
  baseline for the repo move, so the version is locked and nothing else changes until then.

  What 2.5.9 carries (CHANGELOG.md has the full entry): the payload-placement fix (prefix reuse
  52% -> 81% median, re-prefilled tokens per call ~27.7k -> ~5.5k, first delta 60-231 s -> 7-67 s),
  auto-continue segments, repeat-read file maps, the notes-guard RLock with its cold-interpreter
  regression test, the time-boxed tool batch, spill-before-truncate, the execute_code seatbelt with
  the widened rm patterns, the transcript sink + compaction tally, the delivery guard, and the
  heartbeat-as-step counter fix. CLI 1.0.9 rides along (bundled in the public archives).

  Also genericized six "the Windows test box" mentions in tinycmdr.py comments to "the Windows test box": the
  packager redacts those for archives, but a public repo would carry the source as-is and there is
  no reason for a hostname to be in a shipped comment.

  Fleet push, every host through its own door, hash-verified before restart:
    the manager box (local)     /restart via its web UI (token in tinycmdr/web-token.txt) -> @the manager boxbot
    the other Windows box    .9    UNC copy + /restart via web UI -> @the other Windows box
    the Windows test box  .20   UNC copy + /restart via web UI
    the LAN model box      .47   scp + sudo systemctl restart tinycmdr -> @a bot account
    the Linux test box  .13   scp + sudo systemctl restart tinycmdr -> @the Linux box
    MacBook    .3    scp + launchctl kickstart -k gui/<uid>/com.trapp.tinycmdr -> @fleetmac
  Proof: maintenance/fleet-version-report.ps1 - six hosts, VERSION 2.5.9, hash 3c5421ceda5c072c on
  every one, "in sync", watchdog column supervisor/launchd/systemd. /api/health answers 2.5.9 on all
  six, and every host logged a fresh "connected to Mattermost".

  The seatbelt's other half had to be pushed per host: every config.json carries its OWN copy of
  blocked_patterns, so the widened rm patterns were written into all six (backups left beside each
  config, values read back) - the manager box, .9, .20, .47, .13 and the Mac. The Mac's config was missing the
  `-fr` form entirely; it has both now.

  Artifacts (built, gated, not published):
    dist/            tinycmdr-2.5.9-{win,macos}-public.zip, -linux-public.tar.gz, -{win,linux}.zip (fleet kit)
                     tinycmdr-cli-1.0.9-{win-public.zip,linux-public.tar.gz}
    public gates     no secrets/ids anywhere, no forbidden filenames, 0 fleet hostnames in skills
    CLI packager     149 passed from a clean unpack, opening it created nothing
    Z:\VPS Admin    2.5.9 + 2.5.8 + 2.5.7 public archives and cli 1.0.9 + 1.0.8, each with a
                     .sha256 sidecar that validates under sha256sum -c; refreshed CHANGELOG and
                     config.example.json beside them
    snapshots/       tinycmdr.py.2.5.9-frozen-<stamp>.bak + a freeze manifest (641 source files with
                     sizes and hashes) so the repo move can prove nothing drifted
  GOTCHA worth keeping: a .sha256 sidecar written with CRLF makes `sha256sum -c` report "FAILED open
  or read" for a file that is perfectly fine (it looks for "name\r"). Write them LF - the five
  2.5.9 sidecars had to be rewritten, and then all thirteen on the share validate.


## ask_user: a run can stop and ask (2026-09-19, unreleased, the manager box only)

Every door into a running conversation was one-way: `dispatcher.steering` only arrives if
the operator types, and the confirm prompt is yes/no and only for `confirm_patterns`. A run
that reached a fork it could not decide could only guess or end its turn, so `ask_user` was
added: the one tool that BLOCKS the run on a question (default OFF, `agent.ask_user`).

The constraint that shaped it: Mattermost hands messages to the LISTENER thread, so the
question is posted and answered there while the WAIT happens on the run's own thread. Two
rows was the first shape and it deadlocked EVERY answer (the door waited on its own event
while `ask_operator` waited on a private one) - `tests/test_ask_user.py` now pins one row,
one wait, and the 12.0s-for-a-6s-config double wait that shape produced. Doors are
duck-typed (`opener`/`post`/`post_done`/`close_question`), so the web run, the dispatcher,
the CLI prompt and a scheduled job each plug in without a shared base class. A door with
nobody behind it (sub-agent, a job with no channel, `ask_user` off) must refuse immediately:
it is never a wait.

Regression that cost a round: the feature block first landed between
`# Scheduler (cron gateway equivalent)` and `def _schema(`, which is exactly the CLI's
`scheduler + tool cut` - the generated CLI then kept `CORE_TOOLS`'s reference to
`tool_ask_user` while the cut removed the function, so importing the CLI build died with
`NameError: name 'tool_ask_user' is not defined`. The block now sits after `def _schema(`
and before `CORE_TOOLS`. `tests/test_cli.py` is the guard here (149 passed): a bot-build
suite never sees this, because the cut only exists in the generated file. Any new block a
`CORE_TOOLS` entry references must live OUTSIDE every cut region.

Suites: test_ask_user (50) + test_stall 227 + test_stop_now 21 + test_ledger 225 +
test_cli 149 + disclosure/plan/transcript/webui_page/verify/digest/notes_guard all green.
`tests/test_cost_guard.py` reports 13 failures on a tree with NONE of this work in it: its
own monkeypatched `run_capture = lambda argv, timeout` no longer matches
`run_capture(argv, timeout, cwd, cancel)` - pre-existing drift, worth fixing separately.
`config.example.json` ships `ask_user: false` (documented, 92 agent keys); the manager box's live
config.json has it ON at 300s.

## 2026-09-19 — 2.5.16: ask_user options are NUMBERED, and free text is an answer

First real use of ask_user produced the complaint, so this round is the operator's:
"I need number selectors and a type your answer option instead of these where I have to
type verbatim." The old rendering listed options as inline code separated by middots, so
the only way to answer was to retype one exactly.

  * `_ask_format(question, options)` is the single renderer for every door - a numbered
    list (`**1.** the other Windows box`), no inline code, plus the line that says a number is a valid
    answer. Mattermost, the scheduler's channel post, the web page and the CLI all call
    it, so a door cannot drift into its own shape again.
  * `_ask_record_choice(row, text)` resolves an answer to its option. A bare number
    resolves; a number WITH a clause ("2 but not the MacBook") keeps the whole text as
    `"<option> - with this too: <clause>"`, because the clause is an instruction; a number
    outside the list or prose that merely starts with one resolves to nothing. The answer
    is still handed to the model verbatim either way - resolving only ADDS the reading, so
    the model is told which option the words started from and does not re-ask a decision it
    already has.
  * The echo on claim is now `Got it: <resolved answer>` instead of a bare "Got it", so a
    misfire is visible in the channel rather than only in the next turn's behaviour.
  * The CLI door takes a number, prints `?` on request, and resolves the same way.
  * Tool schema now says options are shown NUMBERED and the number is a valid answer, so
    the model writes options whose ORDER carries the decision.

Bug found while reading for this: `ask_door_factory` pointed the door's `post` at
`self.door_post`, which was NEVER DEFINED on the dispatcher. ask_operator catches that at
debug level, so the failure was invisible - the run parked with no activity recorded for
the channel, which is precisely the state the stall watchdog abandons runs for. `door_post`
now exists: it touches the channel's activity (asking IS progress) and returns True.
`tests/test_ask_user.py` pins it.

WARNING, cost a round: `python maintenance/build-cli-source.py` does NOT reproduce the
shipped `tinycmdr-cli.py`. The shipped CLI had drifted ahead of `maintenance/cli_blocks.py`
(present in the shipped file, absent from a fresh cut): `_folder_belongs_to_the_bot()`,
`_ShimConnectionError`/`_ShimTimeout` and the shim's `ConnectionError`/`Timeout` aliases,
`RotatingFileHandler(delay=True)`, the "atlas ships beside the agent" wording, plus its own
`VERSION = "1.0.9"` / `BUILD = "cli"` stamps that the regenerate overwrote with 2.5.16.
Regenerating took test_cli.py from 149 passed to 12+ failures (the CLI failed to create
`tools/`/`sessions/`, never wrote its log or atlas, exited 2 with no config). Restored the
working cut from `tinycmdr-cli.py.bak-numbered-20260919` (149 passed again). The new
helpers ARE reachable in the CLI build without a regen: the cut only drops the Mattermost
classes, the Scheduler and run_cli - the module-level helpers survive. DO NOT regenerate the
CLI until cli_blocks.py is reconciled with the shipped file.

Also: my own line-indexed edit of tinycmdr.py glued four statements together (a missing
newline in a replacement, then a regex that split code from a `_TOOL_PROBE` string literal).
The file was syntax-broken until each line was repaired and `py_compile` ran clean; nothing
was restarted while it was broken. Backup `tinycmdr.py.mangled-20260919` was DELETED after
the repair compiled, so re-read the file before indexing it by line again.

Suites on 2.5.16: test_ask_user 65 passed / 0 failed (15 new for the numbering, the parser
and door_post), test_cli 149 passed (shipped cut restored), version bumped 2.5.15 -> 2.5.16.


## Token map, measured from 10 days of logs (2026-09-19)

Where a bot's context actually goes, from `maintenance/fleet-metrics.py` over the two
logs plus the schema sizes in `tinycmdr.py`. Figures are the manager box (the box that does the
code work) unless noted.

```
tool schemas       13,898 chars = ~3,474 tokens on EVERY call (20 core tools, all live:
                   none is unused in 10 days, so "drop the dead tools" is not available)
  fattest          ask_user 1,538ch, skill 1,213, task 1,143, schedule 1,130, plan 989
static overhead    ~4.3K tokens for the CLI build (system prompt + 17 schemas); the bot
                   carries more, and its first-turn prompt measured 6,775 tokens
live trailing      ~50 tokens (notes + task ledger, kept off the cached prefix)
tool results       6.9 MB over 10 days = ~1.7M tokens
  shell            2,928 calls   2.31 MB (33.8%)
  read_file        1,287 calls   1.81 MB (26.4%)
  execute_code       733 calls   1.25 MB (18.2%)
  list_tools         615 calls   0.46 MB  (6.8%)  <- a list the model already has
  skill               72 calls   0.42 MB  (6.1%)
  fetch_url  (.20)   114 calls   0.57 MB (17.6% THERE, max 30,048 chars)
rework proxies     18 of 287 edits re-read/re-edited the same path; 411 places with an
                   identical call repeated 3+ times; 649 list_tools calls across 10 days
```

Ranked, cheapest and safest first:

1. **Schema prose diet.** Halving the descriptions (13,898 -> ~7,000 chars) saves ~1.7K
   tokens on every call with no function lost: the model needs parameters and a one-line
   when-to-use, not the essays. Visible immediately as a drop in the startup overhead line.
2. **Cap the result tail.** p95 is small (shell 3,442, execute_code 6,616) but a few huge
   results dominate the sum. The spill machinery already exists; a per-result ceiling of
   ~8K chars would cut roughly a third of result tokens.
3. **`list_tools` returns one line when unchanged** ("30 tools, same as your prompt").
   615 calls and 0.46 MB of pure repetition on the manager box.
4. **`read_file`: symbol reads**, or auto-digest of large source files. 1.81 MB over 1,287
   calls, mostly the same file rediscovered (this is the same hole `source_map_text` was
   built to paper over).
5. **Harder HTML digest for `fetch_url`** (the Windows test box: 17.6% of that host's result chars,
   max 30,048). Web pages are mostly boilerplate.
6. **File size is latent, not the win.** `tinycmdr.py` is 506 KB / 10,163 lines, but it
   is not in the prompt: shrinking it saves *discovery* reads, not schema tokens, and a
   split fights the CLI generator's single-file shape. Do the symbol view first.

What to measure before/after any of these: the static overhead line at startup, the
per-tool median/p95 result size, and the rework proxies from fleet-metrics.py.

## MiniDSH hardening, stages 1-2 (2026-09-19)

Git exists here now, so "is this a real fix or a band-aid" is answerable with a diff instead of a
story. The repo root is the install itself (`C:/Users/<user>\tinycmdr`), branch `main`,
repo-local identity `David Trapp <david@the manager box.local>`, and `core.autocrlf false` on purpose: the
working files are mixed (tinycmdr.py is pure CRLF, several suites are LF) and fleet "in sync" is
a sha256 comparison, so git must never normalise a line ending on checkout.

```
848aad3  2.5.19: initial import of the source tree (96 files)
4e04b31  2.5.19: gate the always-on tool-schema block
```

The import is byte-for-byte (index blob hash == working bytes for every staged file) and was
leak-scanned before committing: no value from this host's `.env` or `web-token.txt` appears in any
staged file, and every token-shaped string is a placeholder or the operator's Mattermost user id.
Ignored so a later `git add -A` cannot commit per-host state: logs, sessions, spill, `*.bak*`,
`*.pre-*`, config.json, jobs.json, inbox/, state.json, web-token.txt, .env, notes.md,
field-notes.md, tasks.json, atlas.md, knowledge/, skills/, tools/, dist/, `__pycache__/`, and
tests/eval-runs/ (run_eval writes there and the packager already excludes it, so it is output,
not source).

Stage 2 gated the number instead of trusting it. Always-on payload, measured 2026-09-19:
tinycmdr.py 13 tools / 7,133 chars / 1,783 est_tokens (fattest single schema ask_user 1,078);
tinycmdr-cli.py 10 tools / 5,408 chars / 1,352 est_tokens (fattest skill 838). The budget (7,600
chars) and the per-tool cap (1,200) live in `tests/test_disclosure.py` with the date beside them.
The same suite run in a temp tree with the constants tightened to 7,000/1,000 fails both checks
and exits 1, so the gate is live arithmetic rather than a comment. Suites after the change:
test_disclosure on both builds ok, test_cli 149/0, test_stall 232/0.

Scope kept honest: the static block sits in the cached prefix, so this is a per-run prefill saving,
not a per-call cost. The win is that the number cannot grow back unnoticed.

Compare like with like: the 13,898-char figure is the WHOLE registry; the 7,133 is the 13 tools
that are always visible (the always-on payload, which is what a run pays for before its first call).

## MiniDSH stage 3 shipped: the startup capability line (2026-09-19, 2.5.20 / cli 1.0.11)

One line at start, per lane, saying what the process can actually enforce:

```
capabilities: lane mattermost · model main -> http://a LAN address:8081/v1 · blocked_patterns 21 ·
memory ceiling none this process can see (no cgroup on Windows) · spawn backend CREATE_NO_WINDOW
```

Logged by `run_bot` (before the mmpy_bot import, so a missing dependency still reports) and by
`run_web_mode`; printed by the console lane in both builds (`run_cli` here, `cli_banner()` in
`maintenance/cli_blocks.py`). It never enters a prompt, so the cached prefix is untouched and
there is no config key to add. Gated by 9 checks in `test_checkin.py` and the banner check in
`test_cli.py`.

	the manager box + the other Windows box + the Linux test box + MacBook   bytes pushed, hash-verified, restarted by their own doors
	the Windows test box, the LAN model box                       HOLD: a run was in flight on both (the LAN model box's was reading
	                                       tinycmdr.py itself), so bytes follow when the runs end
	back-up notices                        armed on all four BEFORE the restart, and all four bots
	                                       posted "Back up - tinycmdr v2.5.20" in the operator's DMs
	suites                                  checkin 113/0 (9 new), cli 150/0, disclosure both builds,
	                                       stall 232/0, ledger 225/0 and 188/0+11 skipped

What the line found on its first outing, which is the whole point of stage 3:

```
the manager box          model cloud              -> a LAN address:8081/v1   the DEFAULT NAME is the cloud
                                                               fallback's alias, on a box whose
                                                               primary is the LAN model
the Linux test box     model deepseek-v4-flash  -> a LAN address:8081/v1   the name-collision trap the fleet
                                                               standard exists to prevent
MacBook       blocked_patterns 3       vs 21 everywhere else   per-host config drift, invisible
                                                               until now
```

None of it was written down anywhere. Two of the three are the documented trap where a
synthetic catalogue entry named after `llm.model` shadows a real cloud entry when the LAN box
is unreachable.

Stage 4 (the event log) is scoped in `minidsh-event-log-scope.md`: what today's log cannot
answer (0 per-call verdicts, arguments cut at ~211 B, 554 edits with no outcome on this host),
the event kinds, the model-visible <=> logged invariant and its test, measured cost, three
failure modes, and a shadow-first rollout. Waiting on five operator decisions.

---

## 2026-09-20 - renamed the project from tinycmdr to Tinycmdr

Scope was decided with the operator: publication surface and runtime identifiers
both change, and the fleet's own boxes migrate afterwards in one deliberate pass
rather than box-by-box (the old rule: one version per BATCH, restarts are the
operator's call).

Method, chosen so the change is auditable rather than plausible:

* Frozen the tracked tree first (`snapshots/rename-freeze-<stamp>/`: a copy of all
  98 tracked files plus a sha256 manifest), so every byte the rename changes can be
  accounted for afterwards.
* The rename itself is a pure BYTE substitution of exactly three case variants
  (`tinycmdr`, `tinycmdr`, `tinycmdr` -> `tinycmdr`, `Tinycmdr`, `TINYCMDR`), 1,081
  occurrences in 86 files. No regex, no decoding, no line-ending handling: a CRLF
  file stays CRLF, which is why git still shows a readable diff.
* 19 tracked files renamed (`tinycmdr.py`, `tinycmdr-cli.py`, `tinycmdr-supervise.py`,
  `tinycmdr-service.vbs`, the installers, the restart scripts, four docs).
* `CHANGELOG.md` and `docs/dev-log.md` keep their historical entries. The
  changelog's TITLE was updated and a note added; falsifying past entries to say
  `tinycmdr.py` about a release that shipped `tinycmdr.py` would be a lie.

Two pieces of transition machinery, both temporary and both with a stated
removal condition:

* `tinycmdr.py` is now a compatibility launcher that runs `tinycmdr.py` and
  translates the pre-rename environment names. It exists because a supervisor that
  was already running when the rename landed holds the old path in memory and
  relaunches by that name. Dev-tree only: never published.
* `_take_legacy_lock()` in `tinycmdr.py` holds the OLD lock file name too while
  that launcher is present. Two agents on one Mattermost bot token double-answer
  every DM, and the lock file name changed in this rename, so without it a
  pre-rename instance and a post-rename instance could both start. Inert on any
  host that has no pre-rename launcher, i.e. every host after migration.

Not done in this pass, deliberately:

* The the manager box folder name (`C:/Users/<user>\tinycmdr`) is unchanged: the
  running bot holds its log inside it, so Windows refuses the rename. It moves
  with the restart.
* The other five hosts keep both their old code and their old names until the
  migration pass. Rule for the window: the tree's fleet scripts describe the
  POST-migration state, so do not run them against a host that has not migrated.
* A host's `.env` keys must be renamed AT THE SAME TIME as its code. On the manager box the
  new key was added alongside the old one, so a restart at any moment stays safe;
  the old key is dropped once the new code is confirmed running.

## 2026-09-20 (evening) - 1.0.0: the defaults a reader inherits, a slimmer download, and an
## installer that asks

What changed after the rename entry, in the order it happened.

**The shipped defaults were the fleet's problem, not the reader's.** A fresh install was getting
24000 tokens of context against this fleet's 200000, a 40-turn ceiling against 100, and a
two-pattern seatbelt against the 21 the code had carried for weeks. `tool_carry` was OFF with a
comment saying its stale-text failure mode was the least tested thing in the build; that failure
mode now has a guard (`_carry_stale` re-stats the file an entry came from and says so in the
payload) and a test that asserts both directions, so the carry ships ON. `ask_user` had no shipped
key at all, so it answered to nothing the reference file said. Everything raised ships together:
131072 context, 100 turns, 1200s timeout, 20 exchanges, 10000-char tool output, 8000-char notes,
300s shell timeout, 21 blocked patterns, event log and ask_user on, cloud fallback off.

**Nothing may document a value the code does not ship.** `test_cli`'s reference check named six
keys, which is how a 2-pattern seatbelt and a 40-turn ceiling sat in `config.example.json`; it now
compares every shipped key by VALUE, with an explicit allowlist for the per-deployment ones. The
console packager derives its reference config from the file it just built and refuses to write one
that disagrees. And because dropping `tests/` from the download left the installer's `$required`
list demanding it (every install died with "package is missing tests"), the build now refuses when
a file the installer requires is not in the staged package. Each of those gates was broken on
purpose to see it fire.

**The download is runtime only.** 49 files and 550 KB became 19 files and 365 KB: `tests/`,
`CHANGELOG.md`, `MANIFEST.txt` and `launch-tinycmdr.sh` are out (the suites belong where the code
is edited; the hashes print in the build log instead). `field-notes.md` stays, because the CODE
reads it: it matches an entry's regex against a failed tool result and appends the fix to output
the model is already reading.

**A chat account is optional, on all three platforms.** The harness has three doors - a Mattermost
bot, `--cli`, and the local page at 127.0.0.1:8787, which is dispatched before the token check - so
requiring a bot token made two of them unreachable. Windows now installs without one and hands over
the local doors; with `-EnableWeb` the task serves the page under the same supervisor instead.
That also removed a trap: the old installer registered the CHAT task with no token, which exits at
once (tinycmdr.py refuses to start without a token, deliberately) while the supervisor respawned it
every few seconds forever. Linux and macOS stopped dying without a token and run the page instead.
`tinycmdr-supervise.py` passes its own extra argv to the child, which is what makes `--web` reach
the agent at all.

**The installer asks.** It could not: the `.cmd` wrapper always passes `-NoPause`, and the single
prompt was gated on `-not $NoPause`, so the one way most people start this was the one way that
could not ask anything. A real console decides now (`-NonInteractive` and a redirected stdin mean
"do not ask", which is what a fleet push needs). It asks which doors to set up (any combination,
because chat and the page are one process and `--cli` takes no lock), the model endpoint, model id
and key when the endpoint is not local, and the page token. Then a summary and a yes. An existing
install is a question ("replace its app files?"), not a `-Force` error.

**The page's token is handed over.** The installer prints a ready link
(`http://127.0.0.1:8787/?token=...`); the page takes the token from the link, remembers it, and
re-asks when the server says 401, naming `web-token.txt`. Before this, the prompt read "leave empty
if loopback" while an install WITH a token looked broken: an empty answer was accepted, and every
message came back "could not send: unauthorized".

**Probe discipline, learned the hard way.** `-VerifyOnly` never reaches the package-integrity
check, so only a real install into a temporary `-InstallDir` proves a package. A probe install must
pass its own `-TaskName`: one of mine registered `Tinycmdr`, and a cleanup that deletes folders but
not tasks leaves a task pointing at nothing.

State of the work, open items and the evidence: `docs/handoff-2026-09-20.md`.

## 2026-09-20 (night) - the page every browser refused to run, and the three gates that
## could not see it

The operator ran the installer on the Windows test box, took the link it printed, and got a page that
could not send: Send did nothing, Enter just added a newline, and the token stayed in the
address bar. Every one of those symptoms is the same fact. The page's own `<script>` was a
SyntaxError, so no line of it ever ran.

**The cause: three string literals in the Python source.** `askToken()` (added earlier the
same day) prints a two-paragraph message, written as `'...accepted.\n\nIt is in...'` in the
JS - and the Python literal held a SINGLE backslash, so Python did the escape and the browser
received a REAL newline inside a JS string literal. An unterminated string literal is a parse
error for the whole `<script>`: no handlers wired (Enter falls through to a newline), no
`versionCheck()` (the version chip stays empty), no `history.replaceState()` (the token stays
in the URL), no poll loop. One character class, three files, every shape of 1.0.0 - the public
zips, the fleet kit and the Mac package all carried it.

**Why every green light was green.**

* `/api/health` answering `{"ok": true, "version": "1.0.0"}` is the SERVER, and the server was
  fine. So were the page's 401/200 auth checks, which are HTTP too.
* `tests/test_webui_page.py` ran the real page script in Node - but it sliced the page out of
  the RAW TEXT of `tinycmdr.py` between the triple quotes. Raw text still reads as valid JS
  (`\n` is a legal escape); only the evaluated string has the newline. The suite graded a page
  that never existed, so it was green on a page no browser could run.
* `tests/test_webui_browser.py` drives real Edge and presses Enter - the one suite that
  catches this - and it FAILS on that tree (13 checks). It was not run after the page changed;
  the "all suites pass" line in the handoff was true of the tree the suites were last run on,
  which was one commit behind the page work.

**What now stops it.**

* `test_webui_page.py` renders the page the way the server does (`ast.literal_eval` on the
  WEB_PAGE assignment, then the version substitution) and refuses loudly if WEB_PAGE is not a
  plain literal.
* Its DOM shim was missing `location` and `history`, which the page had started to touch; the
  shim called a healthy page dead. Both exist now, the shim takes `query` / `no_token` from a
  scenario, and it reports the auth header of every call, the prompts asked and the address
  the page replaced. Two scenarios grade the handover the installer prints: a link that carries
  the token (used for every call, no prompt, address bar scrubbed to `/`) and nothing to go on
  (asks once, in words that still read as paragraphs).
* Both directions were run: 61 checks green on the fixed page, and on the broken bytes the
  suite fails at `page script threw at load: Invalid or unexpected token`.
* The installer's printed link is escaped (`[uri]::EscapeDataString`) because "type your own
  password" invites `&`, `#`, `+`, `%` and spaces, all of which mean something else in a query
  string; an unescaped paste handed over the WRONG token and answered 401 at somebody who was
  told there was nothing to type.

**The evidence, in the order it was collected.** The rendered page script passes
`node --check`; the built `tinycmdr-1.0.0-win-public.zip` was installed into a throwaway folder
from the package itself, its page driven in headless Edge - version chip `1.0.0`, the message
typed, Enter consumed, a run started from the browser, no JS exceptions - and the same bytes
(`sha256 86061c50a58bbb3f`) are what the Windows test box serves after the file push and the supervisor's
relaunch. All 22 suites in `tests/` pass, `test_cli` at 168 checks.

**The same lane had a second defect, and the page work is what exposed it: readiness.** The
supervisor's readiness test was "the lock is held and Mattermost answers". A page-only install
has no chat account, so that test can never pass, and readiness is not a report - it is the
input to failure counting. the Windows test box's own `logs/supervisor.log`, from the install the operator
ran: `WARN bot pid 48864 is up but not ready after 90s (lock held=False, mattermost=None)` at
17:57, the same WARN at 18:23 on the replacement, and `bot exited: code=4294967295 uptime=1488s
ready=False (consecutive failures 1)` - a bot that had served for 25 minutes booked as a FAILED
START, with the growing backoff and the eventual "tinycmdr keeps failing" announcement that
follows. The gate waits for the doors the child was actually asked to serve now: a chat install
keeps the old lock+Mattermost test, a `--web` child is ready when the page answers
(`/api/health`, no token), and an install with both waits for both. `waiting_on()` names what it
is waiting for in the WARN, so the message stops reading like a mystery. Proof, in his log:
`18:28:02 INFO bot ready in 0s (pid 46072)`, status `last_ready_s: 0`, `consecutive_failures: 0`.
Graded by `tests/test_supervise_ready.py` against real children in three lanes: page-only comes
up ready, a chat lane pointed at a dead server does NOT, and a page+chat install is not excused
by its page.

**The rule this leaves behind.** A test that reads a file is not a test of what that file
becomes. Grade the rendered artifact: the served page, the built zip, the string the browser
gets. And when a suite's own shim lacks something the page touches, that is not a page bug -
but a suite that calls a dead page healthy is worse than no suite.

## 2026-09-20 (night, second pass) - the page stops looking stalled, and remembers how a run ended

He ran the first end-to-end install on the Windows test box and said the streaming "felt a little different
than what I would feel in mattermost". Measured instead of argued: one stubbed stream (reasoning
phase, a tool call, a streamed answer) driven through both lanes - a recording dispatcher for chat,
a real `WebRun`, then a REAL page in headless Edge with the DOM sampled every 250ms. The plumbing
is shared and complete; three things were not.

**A run's own line is in the transcript now, and it survives a reload.** In chat the status post is
created when the run starts and edited into `✅ Done - N step(s) in Ts · model X · usage` when it
ends. On the page that line was the HEADER only, and the header goes back to `idle` on reload - so
the transcript held no record of how a run ended, ever (`sessions\web.web.jsonl` on his install:
every run stored as `[you, final]`). `WebDestination.line("status")` draws the line where the run
starts and edits it in place. The Done text lands BEFORE the run is marked done: otherwise a client
that stops polling the moment it sees `done:true` keeps the pre-Done text, and the live view and the
disk copy disagree (`🔧 Working…` against `✅ Done — ...` - which is exactly what the suite's own
comparison caught). The header is left reading `done in 12s, 3 tool calls`.

**The model's reasoning streams - on the page only.** `_stream_chat` always accumulated
`reasoning_content` and only ever forwarded `content`, so there was nothing to show through the
whole think, and the heartbeat's `chars` counter counts the ANSWER's text: it read `0 chars` for
3-13 seconds (from this fleet's own log: `first delta 3.0s ... 12.7s`). Reasoning now reaches the
page as one dim line that grows, tail-capped at `REASONING_LINE_MAX` (2000 chars) - the newest
reasoning is what says "alive, and here is what it is chewing on", and the whole monologue is not
something to scroll back through. `Destination.shows_reasoning` is a lane preference like
`merge_tools`: chat keeps it OFF (an edit per second on a phone for text the model never addressed
to the operator), and the suite asserts the difference on purpose.

**The heartbeat reports what the model is doing instead of inventing a speed.** It read
`35.8 tok/s, 220 chars` where the rate was `deltas / elapsed` - a CHUNK rate, not tokens/s. Against
llama.cpp one chunk is one token, which is why it looked right on the fleet and shipped; against a
server that batches its deltas it was simply a wrong number. `stream_heartbeat()` now says
`waiting for the first token` / `thinking · 4,210 chars` / `writing · 3,120 chars`.

Two defects were found by the work itself, both in the path the new line touches: the Done line
landing after `done` flipped (live view vs disk, above), and the suite's HTTP collector using a
CURSOR while ignoring the payload's `updates` list - the exact client the page's `since=0` polling
exists to avoid, and it kept the stale text for ever.

Evidence: 22 suites green (`test_cli` 168 checks), the browser suite driving real Edge and pressing
Enter, and the page's own JS unchanged by all of this - every change is server-side, so no browser
can be holding a stale client. CLI regenerated from the tree (`tinycmdr-cli.py`, +111 lines) with its
five suites re-run against it; every 1.0.0 shape rebuilt (fleet, public win/linux/macos, cli
win/linux); Z: refreshed with new sha256s; the Windows test box pushed and relaunched (`bot ready in 0s`, the
page it serves byte-identical to the rendered package at `86061c50a58bbb3f`).

## 2026-09-20 (night, third pass) - one install, one config.json, one .env - the doors are mediums

**The console build was a second install.** `INSTALL-WINDOWS.cmd` copied a fixed list into
`C:\tinycmdr`, and `cli/tinycmdr-cli.py` was not in it (nor on Linux or macOS), so the console build
lived in the download folder with a README telling the reader to create a `config.json` - a step the
interactive install had just done for them. Three files described the same settings and none of them
knew about the others.

It is now one folder and one set of files, because every path in both builds resolves from
`BASE_DIR` (the folder the file sits in): `tinycmdr-cli.py` is staged at the package ROOT beside
`tinycmdr.py` (no more `cli/`, no more folder README) and installed FLAT beside it on all three
platforms, so a session, the page and the bot read the same `config.json`, the same `.env`, the same
`notes.md`, `tasks.json`, `sessions/` and `tools/`. The wizard and the README hand over ONE console
command (`python tinycmdr-cli.py`), which is also the only one of the three local doors that needs
nothing but Python itself.

The refusal that made this impossible is gone. The generated build used to exit 3 when `tinycmdr.py`
and `tinycmdr.lock` sat next to it ("this folder belongs to a running Mattermost bot ... copy this
file into a folder of its own"), which is exactly the folder the installer now puts it in. The rule
it defended - one set of notes per folder - is the rule we now WANT. It is replaced by a test that
says the opposite: the console starts in the bot's folder and reads that folder's `config.json`
(suite names it a config the run can only have read, points it at a dead port, and asserts the run
reports that endpoint).

**The page token is a secret, so it lives in .env.** `web-token.txt` was a human-readable copy of a
value that also sat in `config.json`, and only the Windows installer wrote it - the page's 401 prompt
named a file that does not exist on Linux or macOS. Now: `TINYCMDR_WEB_TOKEN` is a real env override
(`env_map`), all three installers write it to `.env` (one secrets file per install, mode 600), the
installer clears `web.token` in `config.json` and DELETES a stale `web-token.txt` from an older
install, and the prompt names `.env`. Linux and macOS installs also hand over the ready link now,
which only Windows did before.

**No provider is named anywhere a reader can see.** `DEEPSEEK_API_KEY` was the placeholder in
`.env.example`, the Windows and macOS installers' key handling, a `(llama.cpp, vLLM, Ollama,
DeepSeek, ...)` hint in the Linux installer, and four code comments. The `.env.example` entry is now
`MY_PROVIDER_API_KEY` with the rule beside it (the variable is whatever your fallback entry's
`api_key_env` says), the installers preserve EVERY key the host already had instead of one named
key (and say which keys they refused to copy from a shared secrets file, without naming a provider),
the macOS installer's default endpoint is the usual local llama.cpp shape rather than a named cloud
API, and the comments keep their lesson with the vendor's name removed.

**Two generation stages, and the second one is not optional.** `build-cli-source.py` writes
`tinycmdr-cli.py`; `build-cli-fix.py` then applies ~40 refusals-if-they-do-not-match edits, including
the console-holding helpers and every provider-name removal. Regenerating with the first stage alone
produced a build that FAILED 64 checks in `test_cli` (no console hold, no build marker, an atlas
shim gone) - the file was plausible, imported cleanly, and was missing a whole inserted block. Worth
knowing before the next regeneration: run both, in order, and let the suite grade the result.

Evidence: 22 suites green (0 failures), the CLI's own suite at 167 checks against the regenerated
build, the CLI packages rebuilt (their gate re-runs `test_cli` on a clean unpack: 167/0). A real
install from the rebuilt public zip, non-interactive into a throwaway folder, graded 11 checks:
`tinycmdr-cli.py` installed flat beside `tinycmdr.py`; `.env` carries `TINYCMDR_WEB_TOKEN` while
`config.json` holds no token and no `web-token.txt` exists; the console build reads THAT
`config.json` (endpoint `127.0.0.1:9` appeared in its own error) and THAT `.env`
(`TINYCMDR_MODEL=env-model-from-dotenv` reached the run); and the page answers 401 with no token,
401 with a wrong token, 404 with the `.env` token. the Windows test box's live install migrated in place: its
existing token moved into `.env` unchanged (so the link he already has still works), `config.json`
cleared, `web-token.txt` deleted, restarted at 20:25 (`bot ready in 0s`), and
`python tinycmdr-cli.py --version` in `C:\tinycmdr` returns rc=0 where the old build refused.

## The endpoint's window is the truth, and a run cannot end on a no-answer note (2026-09-21)

the LAN model box's overnight run died at 08:06 and that conversation sat dead for four hours until the operator
posted again. The box it talks to had been restarted serving **131,072 tokens per request** (the
`-np 2` MTP arms on .47), while the host still said `llm.max_context_tokens: 200000`. Nothing ever
compacted, the payload grew to 126,261 tokens, and the next turn had 4,808 tokens of room to answer
in. The server's own log says it plainly:

```
slot operator(): new prompt, n_ctx_slot = 131072, task.n_tokens = 126261
slot release:    stop processing: n_tokens = 131071, truncated = 1
```

The harness read that as "the server is clamping output below the requested cap", retried at the
65,536 ceiling (4,759 tokens, the identical wall), fired its empty-answer retry, and then posted its
own warning as the run's answer: the run ended there. `max_tokens` could never have moved that wall,
and nothing continues a run that ended on a diagnostic.

- `_detect_window()` asks the endpoint what it serves (vLLM `max_model_len`, llama.cpp `meta.n_ctx` on
  `/v1/models`, `/props` at the root) and `_context_budget()` clamps a configured number by it:
  `min(configured, served - REPLY_HEADROOM - max_tokens)`, with a warning naming both numbers. `auto`
  keeps its old meaning. Measured on the manager box's own config against the live box: 200000 -> 107688.
- A `finish_reason=length` with no answer whose prompt + generated reached that window raises
  `ContextOverflow` instead of escalating the cap, so the run loop's existing shrink-and-re-ask path
  takes it. The misleading "the server is clamping" line is not written in that case.
- A model that returned nothing on a CUT turn (status `truncated`) is asked again once, in the same
  task, bounded by `NO_ANSWER_CONTINUES = 1` plus the run's step and wall budgets, and the channel is
  told. A model that ends its own turn with nothing twice (`finish_reason=stop`) is still reported
  rather than asked a third time: that was a deliberate call and it stands.

The two chat-facing warnings were reworded in the same pass, for the public build: they carried an
operator note ("Don't set llm.no_think on the LAN boxes - they're meant to think") and sent the reader
after `max_tokens` for a failure that was the window. Both are two short reader-facing lines now, and
the detail lives in the log.

Evidence: `tests/test_ledger.py` gained the budget-clamp and the window-full-cut checks (231 checks),
`tests/test_checkin.py` the cut-turn re-ask and its bound (117), and all 22 suites are green plus the
CLI legs (`TINYCMDR_TEST_APP`/`TINYCMDR_SRC=tinycmdr-cli.py`). Both new gates were seen red before green:
with the window stub cleared the cut test took the clamp path, and the budget test returned 200000.

### Fleet push: 1.0.0 + 135222206675e8cb (2026-09-21, after the window fix)

Pushed `tinycmdr.py` to every migrated host and restarted each one through its own door, with the
back-up notice armed first so the restart is visible in that bot's own channel. Nothing here is a new
version: the fix rides the 1.0.0 bytes the fleet already carried.

```
the manager box      own web door /restart      notice -> <id>   06:59:33  pid 636
the other Windows box   child killed, supervisor   notice -> <id>   06:56:58  pid 6116
the Windows test box child killed, supervisor   (page lane, no chat channel to notice)  06:57:20  pid 50312
MacBook   launchctl kickstart -k     notice -> <id>    06:58:35  pid 26721
the Linux test box systemctl restart tinycmdr  notice -> <id>    13:58:14
```

Proof: five hosts read `1.0.0  135222206675e8cb  in sync` with a watchdog column in
`maintenance/fleet-version-report.ps1`; each bot logged a fresh `connected to Mattermost as @<bot>`
and (chat lanes) `posted startup notice to <id>`. The Mac printed the new clamp at startup
(`llm.max_context_tokens is 110000 but ... serves 131072 ... using 107688`), which is the fix running.

the LAN model box is NOT in this push and is the one host still on the old name and 2.5.23 (`~/tinycmdr`,
`tinycmdr.service`, `tinycmdr_MM_TOKEN`). Bytes alone do nothing there: its unit runs `tinycmdr.py`
and the new build is `tinycmdr.py`, so it needs the rename migration (a stop/start, which the operator
has claimed). The report says `no build at /home/<user>/tinycmdr/tinycmdr.py` for it, which is the
honest answer, not drift.

### The walk prep: artifacts rebuilt on the fixed tree, and what the reader path found (2026-09-21 morning)

The tree moved after the 1.0.0-rc tag (the endpoint-window fix and the reworded no-answer warnings),
so last night's artifacts could no longer represent what ships: a walk over them would have graded
bytes he had already rejected. All five share artifacts were rebuilt from HEAD, re-gated and
republished, and the plan's hash block was updated in both copies:

```
tinycmdr-1.0.0-win-public.zip           5d83f60513d14fae   (18 files, 366 KB)
tinycmdr-1.0.0-linux-public.tar.gz      ae1a9e6709d35935
tinycmdr-1.0.0-macos-public.zip         63c2274a70b55837
tinycmdr-cli-1.0.0-win-public.zip       db32c17fd5f965c1   (test_cli 167/0 on a clean unpack)
tinycmdr-cli-1.0.0-linux-public.tar.gz  ecda2b087eaef122
```

Sidecars rewritten in the house shape (`<hash>  <name>`, LF) and verified on the share with
`sha256sum -c`; the packagers' own gates (public gate, syntax floor, atlas layout, clean unpack)
all passed on the rebuilt bytes. The install probe was re-run for the same reason and came back
11/11 on the new zip (`hermes-tmp/wininstall-unified-probe.py`).

The other two arms, from the SHIPPED archives rather than the tree:

- **Linux, on python 3.10.12 (the floor the download promises):** the tarball extracts to 18 files,
  `install/install-tinycmdr.sh` is executable, `tinycmdr-cli.py --version` reports
  `tinycmdr 1.0.0 (cli build, python 3.10.12, ...)` rc=0, its no-config start prints the copy steps
  and exits 2, and a file-list snapshot before/after shows opening it creates NOTHING.
- **macOS:** the zip verifies against the share sidecar and extracts to 18 files; the installer
  refuses a stock Mac's `/usr/bin/python3` (3.9.6) with `*** python 3.9 is not supported (need 3.10,
  3.11 or 3.12)`, rc=1, creating nothing. The message does not say how to get one (that is the first
  wall a macOS reader meets, since a stock Mac ships only 3.9) - raised as an open item.

the Windows test box was put back to a clean machine state for his walk: task `Tinycmdr` unregistered, the
pythonw pair killed, `C:\tinycmdr` removed (state copied to
`hermes-tmp/release/tinycmdr-the Windows test box-preclean-20260921-0707`, 32 files incl. .env), and its
Downloads refreshed with the rebuilt zip + sidecar (hashes equal to the share's). `C:\tinycmdr`
still cannot be removed: a process holds the directory, zero items inside.

**Three findings from the reader path itself (the walk's step 6 is what surfaced them):**

1. **A `-Force` redo RESETS `config.json`.** Measured on a probe install with markers written by
   hand: `llm.model=marker-model`, `llm.base_url=http://127.0.0.1:9/v1`, `allowed_users=['marker-user']`,
   `agent.bot_name=marker-bot` all came back as `main`, `http://127.0.0.1:8081/v1`, `[]`, `the manager box`
   after one `-Force` run. The installer copies `config.example.json` over the live file first
   (install-tinycmdr.ps1:629) and then fills in only what it was given or asked. An empty allowlist is
   a bot that starts, aborts and looks dead - the Windows test box's own log shows exactly that abort twice on
   2026-09-20 19:45. The .env side is careful by design (the bot token is explicitly kept "so a
   -Force redo keeps it"); config.json has no such care.
2. **A redo generates a NEW page token**, so the ready link the reader saved stops working (the page
   then 401s and re-asks, naming .env, so it is recoverable but surprising). Same code path: with
   `-EnableWeb` and no typed choice, `$webToken` is a fresh random value every run
   (install-tinycmdr.ps1:634-637).
3. **`-Uninstall` removes the task by NAME, not by install.** Uninstalling the throwaway probe
   install on the manager box removed the manager box's OWN `Tinycmdr` task (the probe had used `-SkipTask`, so it never
   registered one). The bot kept running, untracked by the scheduler. Recovered: the running pair's
   tokens were read first to learn the run level it must be put back with (both `High`/elevated), the
   task was re-registered with the installer's own recipe plus `-RunLevel Highest`, the untracked
   supervisor and child were stopped in that order (supervisor first, or it respawns the child into a
   lock the new supervisor then waits on), the task was started, and the bot posted its own
   `posted startup notice to <id> (downtime 7s)`. The new pair reads `High`
   again, the task shows `Running | Highest | S4U`. This is the probe trap already in the skill,
   sharpened: the *uninstall* path is not scoped either.

### The macOS installer fetches its own python, and the two transcript lanes got a presentation pass (2026-09-21)

Both came out of the walk. The installer first, because a stock Mac has only
`/usr/bin/python3` 3.9.6 and that is the first wall a reader meets:

- `--install-python` (mirroring the Windows installer's `-InstallPython`) and an
  interactive OFFER when no 3.10-3.12 is found on a real terminal. Nothing is ever
  fetched from a pipe: `-NonInteractive`/redirected stdin keeps the refusal, which
  now names the switch as well as `brew`/python.org.
- The route is uv (already part of this fleet's toolkit, and no password): the uv
  bootstrap if uv is absent, then `uv python install --no-bin 3.12`. The interpreter
  lands in `<install>/.python`, uv in `<install>/.tools`, so uninstalling the folder
  takes both away; `--no-bin` keeps shims out of `~/.local/bin`; only uv's own
  download cache touches `$HOME`.
- Measured on the Mac: 943 ms for CPython 3.12.14, 71 MB, and a venv built on it
  pip-installs mmpy_bot 2.2.1 and runs the shipped console build (rc=0) [corrected 2026-09-22:
  this entry said 2.34.2, a version mmpy_bot has never released. The number is almost
  certainly a misread of the same pip line, which does install requests 2.34.2; re-measured
  2026-09-22 as mmpy_bot 2.2.1 + mattermostautodriver 2.3.0]. Proven in
  four lanes: piped (refusal, nothing downloaded), `--install-python` with uv
  present, a bare machine with faked HOME (uv bootstrap into the install dir), and
  the offer answered `y` on a pty.
- Two traps cost a round each and are worth remembering: `fetch_python`'s progress
  lines went to STDOUT and `PY="$(fetch_python)"` captured the chatter, so the
  installer reported "could not run" on an interpreter that ran fine (progress now
  goes to stderr, stdout is only the path); and a one-line edit made with Python's
  `write_text` on Windows turned the LF shell script into CRLF, which dies on macOS
  at `set -euo pipefail\r`. Scratch edits of a shipped shell script are BYTES.

Then the operator's look at the two transcript lanes ("the reasoning stream is not
helpful"; "the cli version is abysmally bad at presentation ... only white and green
colored text which shows up as different things including the answer"):

- **The reasoning stream is off on the page** (`WebDestination.shows_reasoning =
  False`). The machinery stays; a lane that wants it sets the flag. The page shows
  what the model SAYS and what it RUNS. The "working" indicator after a send is
  untouched (the run's own line plus the rail's pulsing dot).
- **The page's tones are cards**: a 3px accent bar, a tint, and a ▸ glyph on a call,
  all in CSS and `::before` so a line's `textContent` is still exactly what the model
  or the tool said - which is what the page suite grades. The answer (`.final`) is
  the brightest card on the page. Done and failed calls carry the reporter's own mark
  plus their tint, so no line wears two.
- **The console announces the answer**: a dim rule, then the text in bold bright
  white (`answer_block()` in `cli_blocks.py`), for the interactive loop and `--once`
  alike; the prompt is bold cyan, so `you>` stops looking like the agent's output.
- **The console's tones read as importance**: narration and the run's own lines are
  quiet, a call is cyan and its result green (bold red for a failure), and the Done
  line is dim green rather than the same green as everything else. The lane also
  stops showing chat idioms literally: backticks around a tool name, and the 🔧 on a
  line that now carries its own ✔/✘.

Evidence: 22 suites green plus the CLI legs, `test_cli` 167/0 against the regenerated
console build; a computed-style read-back through headless Edge against an instance
started from this tree (the live bot serves the code it loaded at its last restart, so
a styling change cannot be seen through it) showing each tone's bar, tint and colour;
and pictures of both lanes (`hermes-tmp/page-look.png`, `hermes-tmp/cli-look.html`).

### The console got a screen (2026-09-21, afternoon)

The operator's verdict on the presentation pass was "not worth shipping in its
condition ... we need to completely shift to a TUI method for the CLI instance",
naming Hermes' own CLI as the reference. So the console build draws now:

- **The stack is the one Hermes uses**: rich for the boxed banner, one card per tone
  and Markdown for an answer, prompt_toolkit for putting it on the terminal. Both
  are imported lazily and only when `tui_wanted()` says a real console is there, and
  both are OPTIONAL in `requirements.txt` - the console build's "dependencies: none"
  promise survives: without them, with a pipe, or with `TINYCMDR_PLAIN=1`, every line
  prints plainly, exactly as before.
- **The lane stays one lane**: `CliDestination(screen=...)` hands the same text to a
  screen instead of painting an ANSI line. A call is a cyan "call" card, a result is
  green, a failure red, the answer is the accent card with Markdown, and the run's
  done line goes to the status line rather than wearing the answer's card - in the
  CLI lane a `final` UPDATE is the done line, not an answer.
- **What reaches the terminal is an ANSI string**, never rich markup and never raw
  ESC bytes through a proxy (prompt_toolkit sanitizes those into visible `[1;33m`
  garbage - the trap Hermes' own comment warns about).

Evidence: `tests/test_tui.py` (22 checks: the pipe/TINYCMDR_PLAIN refusals, each
tone's card and colour, the Markdown answer, the done-line/vs-answer split, the
plain path untouched with no screen, real SGR in the output with no markup leak, the
status throttle, the narration passthrough, the SVG record), 23 suites green, the
CLI legs green, `test_cli` 167/0 against the regenerated build, and
`hermes-tmp/tui-look-real.py` - the picture drawn by the screen's own SVG record
driven through the real RunReporter, so what it shows is what a terminal shows.

Still open: the bot build's own `--cli` lane (tinycmdr.py's older console) does not
build a screen yet, and the input line is still the plain one - prompt_toolkit
owning stdin is what buys history, editing and a status line that updates in place
instead of on a timer.

### The console's input line, and the bot's own --cli (2026-09-21)

Both follow-ups from the screen:

- **`tinycmdr.py --cli` draws the same screen as the console build now**: a TuiScreen
  when there is a terminal, the banner through it, each call as a card, the answer as
  the answer card, and the run's usage line to the status line. Proven in a pty: the
  banner panel and the `you> ` prompt appear where the old plain header was.
- **prompt_toolkit owns the prompt while the console is idle**: editing, in-session
  history (a history FILE would add a file to a folder whose rule is that opening the
  build creates nothing but the log's first line - up-arrow within the session is the
  part that matters), Ctrl-C doing what SIGINT does, Ctrl-D quitting, and a bottom
  toolbar carrying the run's own line and the keys. The screen's `status_line()` is
  handed to that toolbar (`on_status`) instead of printing, so it keeps itself current
  instead of ticking on a timer.
- Mid-run the reads stay PLAIN on purpose: the run's own questions (confirm, ask)
  answer through stdin too, and two owners of a terminal in raw mode is how a typed
  answer lands in the wrong place. So during a run the status still prints, throttled.

Verified by driving the real console in a pty: banner, `you> ` and the toolbar
render; `/help` answers and the prompt returns; Ctrl-D exits clean; `TINYCMDR_PLAIN=1`
still prints the old plain lines; `python tinycmdr.py --cli` draws the banner.

### Debloat pass one (2026-09-21): what left the tree, and where it went

The audit's cuts, applied. Three plan docs whose work shipped
(`tinycmdr-cli-plan.md`, `tinycmdr-harness-scaffolding-plan.md`,
`minidsh-hardening-plan.md`), eight consumed one-shot maintenance scripts
(`_apply_askuser*.py`, `_extracted_web_js.js`, `ab-console-flash.py`,
`watch-console-flash.ps1`, `win-open-diag.ps1`, `ht-enable.ps1`,
`bios-settings-dump.ps1`) and the untracked `.archive/` (2.1 MB of pre-1.0 files)
are gone; every one of them was copied to `Z:\VPS Admin\tinycmdr-historical\`
first, so the citations elsewhere in this log still have a destination.

Also fixed: `listy()` was called twice in the bot build's `--cli` ask path and
defined nowhere - the first ask_user question with options would have raised
NameError. It renders the options the way the console lane does now
(`" / ".join(...)`).

### Debloat pass two: one console, both builds (2026-09-21)

The audit's biggest cut, applied. tinycmdr.py had a console of its own and
build-cli-source.py replaced it with the CLI build's copy, so every console change
landed twice - this week's banner, cards, keys and input line all did.

- The console now lives in tinycmdr.py's shared region (the reader and its steering,
  the slash verbs, the reporter wiring, the screen and its prompt_toolkit session).
  Both builds run THAT code: `tinycmdr.py --cli` and tinycmdr-cli.py are one console.
- build-cli-source.py no longer imports or splices NEW_CLI, and the 660-line NEW_CLI
  string is gone from cli_blocks.py (1125 -> 465 lines). Its cut of the Mattermost
  layer used to end at `def run_cli(`; with the console now sitting above that name,
  the cut swallowed the console's own helpers and the fix step then failed its
  "banner names the build" check. The cut ends at a marker comment above the console,
  and that check is a presence assertion instead of a rewrite: the console derives
  its title, appending "(cli build)" when the build defines BUILD.
- The bot build gains the slash verbs and the steering reader it never had; the tree
  loses a whole second console (net -116 lines across the two files). An unused
  definition is how the two copies drifted apart in the first place.

Evidence: build clean, one `def run_cli` per file, `tinycmdr-cli.py --version` names
the build, test_cli 167/0 and test_tui 25/0 against BOTH builds, 23 suites green, and
both consoles driven in a pty (banner, `you> `, toolbar, `/help`, Ctrl-D).

### The Telegram lane (2026-09-21, the fourth door)

Asked for the cost, then told to build it. Landed as one batch:

- **TelegramDestination** - the same reporting vocabulary, drawn for Telegram: ONE
  growing message per run (a line per tool call would be a wall of notifications and
  it collides with the API's edit rate of about one per second per chat), the answer
  posted on its own so the chatter never buries it, and questions asked with inline
  buttons as well as by text.
- **TelegramClient** - the Bot API over the requests the bot lane already ships: long
  polling, so no webhook, no certificate, no inbound port and NO new dependency. HTML
  parse mode with three characters escaped (MarkdownV2 needs eighteen and one of them
  is '-'), `backticks` rendered as <code>, and a 4096-character split that cuts on
  paragraphs, then lines, then words, and never drops a word.
- **TelegramPoller / run_telegram()** - one worker per chat (a chat's second task
  queues behind its first; two chats never wait on each other), DM only, and the
  allowed list is a GATE: a stranger's message is logged and ignored, never answered.
  /help, /new, /usage and /stop work; anything else is a task.
- **The unified backend is the point**: same Agent, same notes/tasks/atlas/skills,
  same sessions corpus as the chat and page lanes. Each chat gets its own conversation
  named telegram-<chat id>, resumable from any other door.
- Config: telegram.{token,allowed_users} plus TINYCMDR_TG_TOKEN in .env beside the
  Mattermost key. The validator refuses to start when a token is set with an empty
  allowed_users, and Mattermost's own complaints now apply only when Mattermost is the
  door. `tinycmdr.py --telegram` runs the lane by hand; a Telegram-only install takes it
  in the default branch of main(). Tokens are never shared between lanes: different
  clouds, different accounts, one bot per host - the rule the Mattermost lane lives by.

Evidence: tests/test_telegram.py, 34 checks (escaping and injection, the split ceiling,
one message instead of many, the throttle, the fold-away count, the answer as its own
reply, buttons and typed answers landing in the slot a question reads, the gate against
strangers and groups, the verbs, the per-chat conversation name), plus the full suite.

Still open before this is a shipped door: the installers' lane wizard wants a fourth
choice, README and .env.example want the door named, and a live end-to-end test needs a
bot token that only the operator can create in BotFather.

## The endpoint gate covers tools, a reconnect gap makes it refuse, spills get an index, and the experiment ledger (2026-09-21)

The four items left staged on 2026-09-21 (handoff: `Z:\VPS Admin\ATTAGOS-1.0.0-STAGED-2026-09-21.md`),
all inside 1.0.0: no version bump, nothing published, the fleet gets the bytes.

- **The endpoint gate now covers TOOLS.** `agent.endpoint_tools` (default
  `["inferctl", "llamasrv", "serve_", "llama", "vllm"]`) marks a custom tool whose NAME or
  DESCRIPTION carries one of those markers as `endpoint_touching`, and `_exec_tool` routes it
  through `endpoint_gate()` - the same confirmation a matching shell command gets. It is a
  config LIST so no box is hard-coded. Why: `_endpoint_self_harm()` only ever read shell text,
  so a tool that moved the model endpoint walked straight past it.
- **A fresh reconnect gap makes that gate REFUSE instead of asking.** `_catch_up_once()` calls
  `note_steering_gap()` for every recovered post - a recovery is what "this lane really does
  lose messages" looks like (the LAN model boxbot: 7 recoveries on 2026-09-12) - and for the next 10
  minutes an endpoint-touching shell command or tool is refused outright, naming the gap. An
  unanswered confirmation the run never sees is worse than a refusal: the run reads the silence
  as consent, which is the failure the guard exists for.
- **The spill index.** `cap_output()` records each spill as ONE bounded prompt line
  (`- spill#7 shell  spill/<file>  (24000 chars, 3m ago, starts: ...)`; the oldest lines drop
  off, their files stay in `spill/`) in the trailing block, and `read_file {"path": "spill#7"}`
  resolves the id to the absolute file. Before this, the pointer in the capped result was the
  only trace a spill had ever existed.
- **The experiment ledger, always-on.** A core tool `experiment` plus `experiments.jsonl`:
  append-only, one JSON object per line, on the the LAN model boxbot field set (id, date, agent, status,
  question, keys, preregistration, engine, binary+commit, model+quant+file, exact_config, host,
  gpus, slots, per_slot_ctx, fill_depth, control_config, control_mean, reps, interleave, result,
  drift_check, contamination_check, verdict, artifacts, body, supersedes, superseded_by,
  next_trigger). The prompt carries the INDEX only (id, date, status, question, keys, verdict
  and a bounded body); an arm whose keys AND exact_config match a line already in the ledger is
  REFUSED with that line's verdict cited, and re-running is allowed only by passing
  `supersedes=<id>` - the old line is never edited, a marker line is appended, so the supersede
  chain is visible on disk. That is the "MTP = wash" shape: a retraction cannot silently
  contradict the line it retracts.

**The always-on schema budget moved, on the record:** `tests/test_disclosure.py`'s
`SCHEMA_BUDGET` went 7,600 -> 8,900 (measured 8,392 chars over 14 always-visible tools, the
experiment schema at 1,170 of the 1,200 per-tool cap). The new ceiling is that measurement plus
~6% headroom, not room to grow.

### Two defects the batch exposed, both fixed before the push

- **`tool_write_file` wore `@serialized_by_path` TWICE** (as committed in `457f71d` with finding
  4), so one thread took the same non-reentrant `Lock` twice: every `write_file` call deadlocked
  the run, silently and for ever. Found with `faulthandler.dump_traceback_later` after three
  suites (test_ledger, test_stall, test_verify) hung with 0 CPU - the stack named the wrapper
  frame. `tool_edit_file` had no decorator at all, so the lock finding 4 promised was half
  missing and half fatal. Now one decorator each, and `_path_lock()` hands out an `RLock` so a
  repeated decoration can never wedge a run again.
- **`tool_remember` still carried the clipping branch** that finding 2 removed, and
  `tests/test_ledger.py` still asserted the OLD contract (a 5,000-char note clipped at a
  200-char cap). The dead branch is gone and the check is rewritten to the new contract
  (`test_remember_refuses_rather_than_clipping`: refused, the limit named, nothing stored, and a
  note inside the limit stored whole).
- Two texts that still promised `ask_user` would hand a TIMEOUT back to the model (the tool's
  own schema and the system prompt) now say an unanswered question stops the run.

Evidence: the full suite green - 28 suites, 0 red, `test_cli` 167/0 against the regenerated CLI
build, `test_ledger` 233/0; new `tests/test_endpoint_gate.py` (25 checks) and
`tests/test_experiment.py` (27), `tests/test_spill.py` extended to 22 with the index and the
by-id read-back. Then one six-box push (bytes to all six; restart where the box was idle), with a
process check per host rather than a file hash alone.

### Batch A + the Telegram door: an outside code review, triaged and half landed (2026-09-22)

An independent review of the published 1.0.0 package arrived with 13 findings. Re-measured
against the tree the package was cut from (the zip's `tinycmdr.py` is byte-identical to the
tree's, sha `61a358e3...`, so the review is reading current code; its "14,562 lines" is wrong,
it is 14,104). Verdicts, and what was done about each:

- **F2, newline corruption - CONFIRMED and FIXED.** Windows text mode turned every `\n` into
  `os.linesep`, so `edit_file` on a CRLF file wrote `\r\r\n` per line (measured on this box,
  not reasoned) and its own `.bak` was doubled the same way; `write_file` rewrote LF-only
  payloads as CRLF, which bash then refuses. `newline=""` at every site whose caller has
  already chosen a convention (`atomic_write_text`'s temp write and its plain-write fallback,
  `tool_write_file`'s write and append branches), `write_bytes(raw_bytes)` for the edit
  backup, and a WARNING when an LF-only `.cmd`/`.bat`/`.ps1`/`.vbs` is written - the fix would
  otherwise have traded one silent fault for another. Gate: `tests/test_newlines.py`
  (19 checks, byte-level, both builds, falsified on the pre-fix build: 14 FAIL).
- **F5, the token estimate - CONFIRMED and FIXED.** `est_tokens` was `len//4` for everything.
  Content-aware now: wide scripts 1.3, non-ASCII 2.6, punctuation-dense code/JSON 3.0, a long
  body 3.4, prose 4.0. Measured on this build's own source (the file the model re-reads most):
  3.40 chars/token where the old estimate said 4.00 - a 15% undercount on the commonest
  sample, more on JSON. Gate: `tests/test_tokens.py`, offline properties plus an opt-in
  `TINYCMDR_TEST_TOKENIZE_URL` comparison that sends nothing by default.
- **F6, the window cache - CONFIRMED and FIXED.** `_endpoint_window`/`_context_budget` were
  cached for the life of the process, so a box restarted into a smaller `n_ctx` could never be
  noticed by a running agent - the 2026-09-21 incident made permanent. Both now carry
  `WINDOW_TTL = 300.0`. A cache set from outside the getter (a scenario stub, a future
  per-endpoint probe) is adopted with a fresh stamp instead of being discarded: the first
  version broke `tests/test_ledger.py`'s `_window_stub`, which is how the adoption case was
  found. Gate: `test_a_restarted_endpoint_is_noticed_after_the_ttl`.
- **F7, the repeat guards - CONFIRMED and FIXED.** The dedupe map keyed on the raw argument
  STRING while the loop guard keyed on `json.dumps(args, sort_keys=True)`, so whitespace
  defeated the refusal and still fed the loop counter; and a mutation cleared `executed` but
  never `repeated`, while the prompt promises "any write or edit clears it". One `_call_sig()`
  for both, and a mutation clears both maps. Gate:
  `test_a_write_clears_BOTH_repeat_guards` (read, read, write, read, read - without the clear
  the last two trip `loop_stop_repeats` and force a report mid-task).
- **F11, the banner - CONFIRMED and FIXED.** It counted `REGISTRY.openai_schemas()`, so a real
  install read "34 tool schemas" while its requests carried 14. It now counts
  `select_tool_schemas(None)` and names the hidden set separately: 14 visible, 7 hidden, 21 in
  the registry. Gates added in `tests/test_disclosure.py`.
- **F3, the Telegram door - CONFIRMED (documentation), KEPT per the operator.** The lane
  shipped with no mention in README.md or config.example.json, and its token could live in
  config.json, contradicting the package's own "secrets never in config.json" rule. Now:
  `.env`-only (`TINYCMDR_TG_TOKEN`; a config token is ignored and the log says so), a function
  `both_doors_note()` so "Mattermost wins" is a startup WARNING instead of silence, a
  documented `telegram` section in the reference config, and a README section ("Which door to
  use") that says what each of the four doors is for. Gate: `tests/test_telegram.py` (+8 checks).
- **A7, the check that hid it.** `tests/test_cli.py`'s reference-config check skipped a
  section missing from `config.example.json` entirely, so a whole undocumented lane was silent
  while one missing key inside an existing section was loud. A missing section is now named.
- **F9, small items:** stale "carried in the system prompt" comment (notes moved to the
  trailing block), the `"your-mattermost-username"` placeholder and its validator text (the
  check compares user IDS), `dump_payload` now says operator messages are NOT scrubbed and
  writes byte-stable dumps, `llm_secs` no longer calls `time.time()` twice, and `_chat`'s
  endpoint loop no longer shadows its own `model` parameter (renamed `ep_model`, by AST node
  position - a regex also rewrote the string "routing model %s to %s", which is exactly why the
  rename asserts the loop's string constants are unchanged).

Not fixed, deliberately: the review's F1 ("two builds have drifted") is wrong as a fork -
`tinycmdr-cli.py` is GENERATED, and re-running `maintenance/build-cli-source.py` +
`build-cli-fix.py` against the current `tinycmdr.py` reproduces it byte-for-byte. Its security
claim is aimed at the wrong key: the CLI's `_secret_values` cut is deliberate and documented,
while NEITHER build scrubs `llm.api_key`, the key a hosted primary keeps in config.json. That
real hole is queued. F8 (guard pile-up) and F10 (an approval path for blocked commands) are
policy changes waiting on the operator's word; F4 (fetch_url streaming + an address guard),
F12 (an `tinycmdr <verb>` surface) and the lane-parity test are queued behind them. The
fallback endpoints' windows are still not probed on failover - one `_detect_window` per switch
is the obvious shape, deferred because the fleet runs `allow_cloud_fallback=false` and this
touches the failover path.

Also found while in here: the CLI bundled INSIDE `dist/tinycmdr-1.0.0-win-public.zip` is one
commit stale (one comment line plus ~84 stray-CR blank lines), so the shapes need rebuilding
before anything is published.

### Three tiers for command policy, one secret sweep, a reproducible console build (2026-09-22)

The second half of the outside review's findings, all operator-approved. Batches: C1 (command
policy), B2 (secrets), B3 (the console build), plus the two items the operator picked from the
F4 discussion (a bounded fetch, and a prompt line about untrusted text).

- **C1, the three tiers.** `blocked_patterns` keeps only the unrecoverable (disks, partitions,
  filesystems, shadow copies, the firmware wipes, a fork bomb, an encoded-command blob). The
  reversible-but-destructive recursive deletes - `rd /s`, `rmdir /s`, `del /s|/q`,
  `remove-item -recurse` - moved to a SHIPPED, non-empty `confirm_patterns` (with
  `confirm_without_door`, default `decline`). Why the move rather than a bigger block: an
  absolute refusal did not reduce risk, it relocated it - the model's remaining route was the
  same command assembled at runtime inside `execute_code`, the one path a regex cannot see,
  which this file already admitted. So the confirm tier now covers `execute_code`'s SOURCE text
  through the same `endpoint_gate()`, one `_confirm_hit()` decides what needs a yes for both
  tools, and the question quotes the LINE that matched rather than the first line of the
  program. Both block refusals now name the out-of-band path (run it by hand, or take the
  pattern out of config.json) instead of inviting a safer-but-identical command.
  Gates: 16 checks in `tests/test_endpoint_gate.py` (a shape must ask, a disk wipe must still
  block, a no-human lane declines, `allow` is what flips it, code source asks, `execute_code`
  names the matching line), and `test_stall`'s seatbelt check now reads the tier that owns it.
- **B2, one secret sweep.** The review's finding was aimed at the console build's
  `_secret_values` cut ("only environment variables"). Two things were true: that cut was
  deliberate, AND neither build scrubbed the PRIMARY endpoint's `llm.api_key` - the key a
  hosted primary keeps in config.json and the one in use on every call, while the fallbacks'
  keys were covered. Both builds now sweep it. Then, checking whether the cut was safe at all:
  it was not, because every installer puts the console build FLAT beside `tinycmdr.py`, so both
  builds read the SAME config.json - a chat token pasted there was redacted by the bot build
  and written straight through by the console build. The cut is gone; `_secret_values()` is now
  identical in both builds (`tests/test_scrub.py`, 7 checks, run against both).
  That change exposed a wrong-region edit in the fix stage: `build-cli-fix.py` anchored on
  `lines.index('    for fb in CONFIG["llm"].get("fallbacks", []):')`, and with the sweep back
  the FIRST occurrence is in `_secret_values`, so the catalog step silently deleted the secret
  sweep instead of the catalog's failover loop. The anchor is scoped to `def model_catalog(`
  now. A generator that anchors on a bare first match is a landmine for anyone who moves a line.
- **B3, the console build is reproducible.** `tinycmdr-cli.py` is a build artifact of
  `tinycmdr.py` (two generator steps plus a hand-written cut list), and shipping the artifact
  without the generator left a reader staring at a 9,900-line near-twin with no explanation and
  no way to verify it - which is exactly what the review read as "two builds have drifted". The
  three generator files now ship in the package (SHIP, ~55 KB), the README says the file is
  generated and how, and `tests/test_cli.py::test_the_console_build_is_a_fresh_generation`
  regenerates them in a scratch tree and requires byte-identity with the committed file.
  Re-checked by hand the same day: the pair reproduces the committed bytes exactly.
- **F4, without any blocking.** The operator's call, after the mechanism was explained: no
  address filtering, because on a box with a shell a fetch-address restriction is a speed bump
  and not a boundary (`tool_shell` reaches loopback with no gate at all), and the metadata
  address is not even present on physical LAN boxes. What went in instead: `fetch_url` reads a
  BOUNDED number of bytes and closes (the old path materialised the whole response and trimmed
  afterwards - the same failure class as the four OOM kills the file-read cap fixed), and one
  line in the system prompt says tool output, fetched pages and runbooks are DATA, never
  instructions - report what a source said, do not obey it. That is the fix for the
  injected-instruction path itself, at ~45 tokens of the cache-stable prefix, and it covers
  every address and every future tool.

Suites after the batch: 31 green, 0 red (`test_cli` 170, `test_stall` 244, `test_endpoint_gate`
with the new C1 checks, `test_scrub` 7 on both builds). Nothing published, no version bump, and
the fleet still carries the previous bytes on disk.

### `tinycmdr <verb>`: a management door, and what it refuses to do (2026-09-22)

D1 of the review, done before the public release because it is the one finding that changes what
a READER does on day two. The installers used to leave no command behind: changing the model
meant editing `config.json`, the token meant editing `.env`, and the restart meant finding the
right helper for the OS. None of those are hard, and all of them are where a stray quote makes
the bot deaf with nothing on screen.

```
tinycmdr status | doctor | model [use NAME] | logs [n] | restart | token [set NAME] | run | help
```

- **No verb runs the agent or spends a token.** `status` and `doctor` ask the endpoint for
  METADATA (`/v1/models`, `/props`); `model` lists what the install can route to and `model use`
  writes the default through the SAME `atomic_write_text` and `set_global_model` the chat verb
  uses. Both failing verbs exit non-zero with the reason on stderr, so a script can act on it.
- **`restart` calls this host's own shipped helper** (`maintenance/restart-tinycmdr.{ps1,sh,}`)
  rather than re-implementing the kill-and-launch dance - that dance is where two bots on one
  token came from. On Windows it refuses and prints the `-Verb RunAs` line when the shell is not
  elevated; on Linux it prints the `sudo` line; a missing helper is reported as "this install was
  not built by the installer" rather than as a permissions problem.
- **`token` never prints a value.** It reports which keys are set and in which file, and
  `token set NAME` reads the value from stdin (getpass when a terminal is attached), writes
  `.env` atomically with mode 600, and replaces an existing line instead of appending a second.
- **Two ~20-line shims, not a second build.** `tinycmdr.cmd` (Windows, CRLF) and `tinycmdr` (POSIX,
  LF) run `tinycmdr.py` from the folder they sit in; the POSIX one resolves a symlink chain, so a
  hand-made `/usr/local/bin/tinycmdr` still finds the install. The installers put them in place:
  Windows adds the install folder to the **user** PATH (never the machine PATH, and never during a
  `-SkipTask` probe) with a `-NoPath` escape; Linux and macOS write a two-line wrapper into
  `/usr/local/bin`, so removing one file is the whole undo. Both POSIX installers gained
  `--no-path`.
- The verbs live in the region the console build cuts, so the console build is unchanged: a
  console session has its own slash commands, and these verbs are about a SERVICE.

Two bugs the gate caught in my own code, both worth the note:

- `run_capture` returns FOUR values `(rc, out, err, timed_out)` and the restart verb unpacked
  three - and my first stub returned three too, so the suite agreed with a call that would have
  raised `ValueError` on a real box. A test double that models the wrong interface grades
  nothing; the stub now returns four and the timeout reads as a failure.
- `str(workdir) in helper` failed on Windows because `tempfile.mkdtemp` handed back the 8.3 SHORT
  form of the temp path (`DAVIDT~1`) while the module resolved the long one. Compare paths with
  `os.path.samefile`, never as strings.

Gate: `tests/test_verbs.py` (32 checks) - help/unknown verbs, status and doctor exit codes against
a dead and a live-stub endpoint, the catalog refusal, the config write, the log tail with a key
redacted, and `token` naming keys without printing them. Verified by hand as well: `tinycmdr.cmd
help` through PowerShell, the POSIX shim's interpreter selection with a fake python on PATH, and
`bash -n` / the PowerShell parser on the two installers. Not executed on purpose: `restart` (it
would restart the live bot on this box) - the helper invocation is graded with `run_capture`
stubbed and the argv asserted to point INSIDE the install.

### The audit's remaining findings, closed by measurement, and the pin a fresh install needed (2026-09-22)

Batch A, B2, B3, B4 and D1 landed the review's findings; F8, F13 and section 4 were left
"optional / not started", and F6's fallback probe deferred. Before building any of them each was
counted in this fleet's own logs (`tinycmdr.log`, 2026-09-09 to 09-20, plus `tinycmdr.log` since;
test-session tags excluded, or every count is a suite's own traffic). Five of the six are dead on
contact:

```
F8 nudge batching            22 real loop-guard nudges ever; 3 turns of thousands had a pile-up
                             (2-3 nudges). ~120 tokens on 3 turns. DEAD.
F8 delivery guard            2 "completion announced" lines ever, both at count 1 of the
                             threshold 3 - it has never wrapped a run early. DEAD.
F8 config-gate a heuristic   no misfire in the log; the guard's one stop (2026-09-10, `shell`
                             identical 6x) was correct. OPTIONAL.
section 4 wall clock         auto-continue has never fired (0 occurrences of "continuing the
                             same task"). ~225 min is an unreached upper bound. LATENT.
section 4 "steer at recall"  already there: the prompt says "Use search_sessions to recall how
                             past issues were solved". DONE.
F13 lane parity              no defect; the missing piece is the enforcement test (nothing in
                             tests/ mentions drive_run). A guard, not a fix. OPTIONAL.
```

Same rule as 2026-09-17 - measure the failure class on the fleet's own log before building its
fix, and expect most plan items to die. It held again.

**What the measurement found instead: a reader's install is a third party's resolution.**
`mmpy_bot` has exactly one release (2.2.1), and its `httpx<0.28.0` is the ONLY thing keeping
`mattermostautodriver` 11.x off a fresh box - 11.11.0 wants `httpx~=0.28.1`, so pip backtracks to
2.3.0 and a reader happens to get the stack the fleet runs. That is a constraint living in someone
else's metadata, one loosening away from handing a reader a client three release lines ahead of
this build's, on the very package the the LAN model boxbot answers blame for the 277 + 171 `WSMessageTypeError`
reconnects in our logs. `requirements.txt` now names both bounds
(`mmpy_bot>=2.2.1,<3`, `mattermostautodriver>=2.3,<3`); measured on a clean venv, all five declared
deps resolve and import (mmpy_bot 2.2.1 + mattermostautodriver 2.3.0).

**Two corrections, to claims rather than behaviour:**

- `install/install-tinycmdr-macos.sh` and this log both said a venv built on the fetched interpreter
  "pip-installs mmpy_bot 2.34.2". No such mmpy_bot release has ever existed; the number is almost
  certainly a misread of the pip line that installs `requests 2.34.2`. Both say 2.2.1 now, and this
  log's entry keeps its correction visible instead of being quietly rewritten. A shipped comment
  that names a version has to be re-measurable.
- The staged 1.0.0 shapes in `dist/` predated the last three commits, the console build bundled in
  them with them. Rebuilt from this tree; hashes in the state doc.

**The rebuild is what publishing owes, and the first run of it refused - on a real blocker.** The
batch that shipped the console-build generator added `maintenance/build-cli-{source,fix}.py` and
`cli_blocks.py` to `SHIP`, and never to `ALLOWED_MAINTENANCE`, the audit's allowlist for that same
folder. Two lists, one file, and every build since refused with "host-specific maintenance script"
- which nobody saw, because the batches were landed and verified without ever cutting a package.
`ALLOWED_MAINTENANCE` now carries all three, with the pair named in a comment above it. Anything
that ships a NEW file out of `maintenance/` has to touch both lists and cut the package in the same
batch.

**One artifact to know about, measured not guessed.** The public packager's scrub rewrites a file
it touches in TEXT mode, so universal newlines read the generator's `\r\r\n` insertions as two
line breaks and write them back as an extra blank line each. In the shipped public console build
that is 84 extra blank lines - all at line ends, 83 of them in comments and blank lines, one inside
the `_console_closes_with_us` docstring - and 2,130 of 2,131 string constants byte-identical to the
repo's, so the build behaves identically. What it does cost: the SHIPPED console build is not
byte-identical to the one the repo tests, so a "regenerate it and compare" check against the
archive fails on whitespace. Fix if it is worth a rebuild: have `sanitize`/`redact_public` read
bytes, decode, replace and `write_bytes`, so a scrub preserves the file's own newlines - the same
rule batch A applied to the app's write path.

Suites after the batch: 32/32 green on the bot build, the console-build legs that honour
`TINYCMDR_SRC` green, the CLI packages' clean-unpack gate 170/0 (`test_cli`), and a real install
from the new public zip passes all 12 unified-install checks. Nothing published, no version bump,
and the fleet still carries the previous bytes.

### Auto-detect was built, tested, and unused - now it is the default (2026-09-22)

The operator's ask: "I want auto detect enabled, I thought we built that." We had, and no host was
using it. `_context_budget()` has accepted `"auto"` (also 0, blank, null) since 2.5.19 - it asks the
endpoint what it serves per request (`/v1/models`, then `/props`) and uses that - and
`tests/test_ledger.py` already proved the window MOVES under it (262,144 to 131,072 across one
restart). What no host did was ask: every box carried a hand-set NUMBER, and a number is used as a
claim about a box, never a question to it.

- The shipped default is `"auto"` now, in all three places that ship one: `DEFAULT_CONFIG` in
  `tinycmdr.py`, `config.example.json`, and the console build's curated block in `cli_blocks.py`
  (which said 500000, a ceiling that happened to behave like auto). Each carries the reasoning: a
  number written for one box becomes the wrong number the moment that box is restarted with a
  different slot count, and the run then dies mid-think with no answer (2026-09-21). An endpoint
  that reports nothing leaves auto at a conservative 8000 and the log says so.
- This host's own `config.json` said 200000 against a box now serving 160000 per request, so every
  run clamped and warned. Set to `"auto"` (backup `config.json.bak-contextauto-<stamp>` beside it,
  values read back). It lands on the next start - config is read at import.
- A hand-edited value can no longer kill a run. `int("AUTO")` and `int("12k")` raised ValueError out
  of the budget path; casing and spacing are not syntax, 0 is documented as auto, and anything else
  that is not a number is NAMED in the log and read as auto. Falsified against the pre-change build
  (`TINYCMDR_SRC=tinycmdr.py.bak-preauto-20260922 python tests/test_ledger.py`): `ValueError: invalid
  literal for int() with base 10: 'AUTO'`, one check red. Three checks now pin it (casing, 0, a
  non-number); `test_ledger` is 238 checks, 0 failed.
- **The gate caught the default change, which is what it is for.** `test_tuning_defaults_are_the_
  agreed_ones` adds `llm["max_context_tokens"] + llm["max_tokens"] + 4000` for the HOST config;
  with "auto" there is nothing to add up, and it raised TypeError. It now asserts the auto case by
  itself - "the budget follows the endpoint, not a number a restart can invalidate" - and keeps the
  arithmetic for a configured number.
- **What the fleet holds today, read off each host:** the manager box auto; the other Windows box .9 200000; the Windows test box .20
  131072; the Linux test box .13 200000; the LAN model box 200000 (its build is 2.5.23, which understands "auto" but uses
  a NUMBER as-is, with no clamp); MacBook 110000. .47 serves 160000 per request, so the three
  200000 hosts are running a budget over their own box's window - the 2026-09-21 failure class,
  live, until their configs say auto. That is one config edit per host plus a restart, and it is
  the operator's call, not ours.

### The packager no longer rewrites a file it only meant to scrub (2026-09-22)

`sanitize()` and `redact_public()` read a file, replaced strings, and wrote it back through text
mode: universal newlines expanded the generator's `\r\r\n` insertions into an extra blank line
each, and a CRLF file came out LF. `read_bytes()` / `decode("utf-8", "surrogateescape")` /
`write_bytes()` fixes the class - a scrub changes the strings it names and nothing else, newlines
included. Proof, measured on the rebuilt shapes: the public zip's console build now differs from
the tree by exactly ONE line (the scrub that renames the bot) with CRLF intact, where it carried 84
spurious blank lines before; the fleet kit's copy is byte-identical to the tree again.

All seven 1.0.0 shapes were rebuilt with both changes (auto default + byte-safe scrub), sidecars
regenerated, the five public shapes staged to Z: with the copies verified against the source, and
the real-install probe green again (12/12). Hashes in the state doc.

### The six-box push and the restart, at the operator's word (2026-09-22)

Sent 2026-09-22 on "Send to the fleet and restart all". Files per host: `tinycmdr.py`,
`tinycmdr-cli.py`, `tinycmdr-supervise.py` (the last one had drifted: the other Windows box, the Linux test box and the
MacBook were a version behind at `c10acb75`, the Windows test box already had the current one), plus
`llm.max_context_tokens: "auto"` in each host's `config.json`. Every host got its own
`.bak-push1.0.0-20260922-*` per file and a `config.json.bak-contextauto-*`, and each write was
read back before the restart.

Order, per the operator's standing rules: survey first (in-flight work, clocks, notice channel),
then ARM THE BACK-UP NOTICE, then bytes, then the host's own door, then a per-host process proof.
Nobody was mid-run: the most recent real tool call anywhere was 2.5 hours old (the other Windows box), and the
two "doing" items in the LAN model box's ledger date from 2026-09-21.

```
host            build (tinycmdr.py)  restarted              lane proof
the manager box       .46  265860bc5e81a34d    child 17336 @11:52:06  @the manager boxbot, notice (downtime 4s), health 200
the other Windows box    .9   265860bc5e81a34d    pid 7828    @11:51:28  @the other Windows box, notice (149s), health 200
the Windows test box  .20  265860bc5e81a34d    pid 58600   @11:51:44  web lane, health 200 on loopback
MacBook    .3   265860bc5e81a34d    pid 4206    @11:50:32  @mac, notice (43s), health 200
the Linux test box  .13  265860bc5e81a34d    MainPID 2632629 @18:50:17  @sotinycmdr, notice (38s)
the LAN model box      .47  265860bc5e81a34d    MainPID 2657878 @18:50:24  @a bot account, notice (36s)
```

`fleet-version-report.ps1` then read all six as `1.0.0 / 265860bc5e81a34d / in sync`, each with
its watchdog (supervisor on the manager box/the other Windows box/the Windows test box, systemd on .13/.47, launchd on the Mac).
Every channel heard its own bot's `posted startup notice to <id> (downtime Ns)` because the notice
was armed before the stop. the manager box's own restart went through its web UI `/restart` (this shell is not
elevated, so it cannot kill the task-owned process) and the supervisor relaunched the child on exit
75. `tinycmdr doctor` afterwards: endpoint `160.0K per request`, instance running, no problems.

**Two mechanics worth keeping, both cost a round here.** `scp` to the Unix hosts is the reliable
route but each file must be hash-checked ON the host after the transfer (three transfers reported
`Connection closed` and still landed; one landed as nothing). And on Windows the applier cannot be
started as `"C:\...\python.exe" script.py` over ssh: the quoted path with a space is split by the
remote `cmd`, so put the invocation in a `.cmd` in the install dir and run that by path.

**Three leftovers found, none blocking, all reported to the operator:** the LAN model box keeps the legacy
`tinycmdr_MM_TOKEN` beside its `TINYCMDR_MM_TOKEN` (harmless; the migration's de-duplication step)
and a whole stale `~/tinycmdr` tree at 2.5.23 with its own config, `.env` and log; the LAN model box's
`llm.model` is `cloud`, so its default route is DeepSeek rather than the LAN box the fleet standard
names; and the manager box's `doctor` warns that `llm.api_key` is set in `config.json`, which is not where a
key belongs.

### the Windows test box re-staged for a fresh reader-path evaluation (2026-09-22)

The operator had blown that box away before the push, so what it received that morning was moot.
Re-staged the way the walk wants it: the previous install snapshotted to the manager box
(`hermes-tmp/release/tinycmdr-the Windows test box-preclean-20260922-115859`, 43 files including its `.env`),
the scheduled task `Tinycmdr` unregistered, the supervisor and its child stopped, `C:\tinycmdr`
removed, and the CURRENT public zip plus its sidecar placed in `C:/Users/<user>\Downloads`
and hash-verified ON the box (`db613f52...`, 23 entries, `INSTALL-WINDOWS.cmd` present) after
finding the 2026-09-21 build staged there instead.

Nothing was installed on purpose: the wizard is his to walk. Two leftovers on that box, both
harmless and both noted for whoever looks next: `C:\tinycmdr` (empty, held by a stale handle, so
it refuses deletion while a session holds it) and `the Windows test box-secrets.env` in Downloads from
2026-09-20, which the walk can feed to the installer's `-SecretsFile`. The fleet report now reads
the Windows test box as unreachable: a clean box has no build to compare, and the other five stay in sync at
`265860bc`.

### Every install folder tidied, and the corrections staged for later (2026-09-22)

The operator's ask, in one message: hold code changes until he has evaluated 1.0.0 on the Windows test box,
stage the corrections for later, clean the stale files out of each bot's install folder ("I was
seeing dozens of .bak files and other unnecessary things"), and blow away the old tinycmdr installs.

What was removed, per host (state and host tooling untouched):

```
the Linux test box   100 paths  (76 backups incl. the whole tinycmdr.py.pre-1.9.x archaeology, tinycmdr.log,
                        tests/ docs/ inbox/ snapshots/ __pycache__/, atlas tooling, the stray
                        "Z:\nope\<bad>|path" directory a bad-path test created)
the LAN model box         7 paths  + the whole stale ~/tinycmdr tree (189 MB, tarred to the manager box first)
MacBook      87 paths  (59 backups, repo tooling copied loose at the top level, tests/ docs/
                        __pycache__/, 22 pre-rename artifacts)
the other Windows box     114 paths  (on Windows, same rules; its maintenance/ host scripts - tinycmdr-24x7.ps1,
                        the codebase-memory tool generators - were KEPT, and the operator's own
                        reports/ platform-tools/ tmp/ benchmark logs were left alone)
the manager box        108 paths  (95 top-level backups, 82 tinycmdr* files, 3 __pycache__ dirs; the
                        pre-rename tinycmdr.log copied to hermes-tmp/release/ first as the only
                        copy of that era's evidence) plus ~/tinycmdr/hermes-tmp (1.36 GB of agent
                        scratch and Mattermost dumps) moved out of the tree, and two dead
                        web-token.txt files (0 code hits) deleted
the Windows test box     n/a      clean box for the fresh-install evaluation
```

**The reveal worth keeping:** every migrated host still had only `restart-tinycmdr.*` in
`maintenance/`, the pre-rename helpers the `tinycmdr restart` verb does NOT call (it looks for
`restart-tinycmdr.ps1`, `-macos.sh`, `.sh`). The pre-rename copies were dead weight, so they went and
the correct helper was pushed to each host instead - so the verb works there now rather than
reporting "this install was not built by the installer".

**The trap that cost a round:** a `.sh` written on Windows carries CRLF whatever writes it
(`write_file`, python's `write_text` in text mode), and Linux bash refuses it with
`set: -: invalid option` / `syntax error near unexpected token '$'do''`. Normalise the script to
LF and re-check before piping it to a host; the local dry run had "worked" only because the write
that made it CRLF had not happened yet.

**Staged for later, at his instruction:** `docs/deferred-corrections.md` now holds the endpoint
editor he asked for (an `tinycmdr model add/remove/edit` verb group, prompted and validated so
nobody hand-edits JSON - his own 27-failed-start incident is why), plus F6's failover window probe,
F5's live tokenizer comparison, F13's lane-parity test, the vendored-driver question, the cosmetic
items, and the three host-level decisions waiting on him.

Left deliberate, reported, not deleted: `C:\tinycmdr` on the Windows test box (empty, a stale handle refuses
deletion until that box logs off or reboots) and the operator's own artifacts on the other Windows box.

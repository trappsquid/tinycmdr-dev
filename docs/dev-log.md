# Development log for the tinycmdr tree

Engineering notes for work on this build. NOT the bot's memory: `notes.md`
is the file the bot re-reads in every prompt and it holds only its own
durable facts (cap `agent.notes_max_chars`). Appending long entries here
instead is the whole point of this file existing.

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

## 2026-09-20 - renamed the project from tinycmdr to tinycmdr

Scope was decided with the operator: publication surface and runtime identifiers
both change, and the fleet's own boxes migrate afterwards in one deliberate pass
rather than box-by-box (the old rule: one version per BATCH, restarts are the
operator's call).

Method, chosen so the change is auditable rather than plausible:

* Frozen the tracked tree first (`snapshots/rename-freeze-<stamp>/`: a copy of all
  98 tracked files plus a sha256 manifest), so every byte the rename changes can be
  accounted for afterwards.
* The rename itself is a pure BYTE substitution of exactly three case variants
  (`tinycmdr`, `tinycmdr`, `tinycmdr` -> `tinycmdr`, `tinycmdr`, `tinycmdr`), 1,081
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

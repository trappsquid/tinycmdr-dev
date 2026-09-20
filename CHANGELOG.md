# tinycmdr changelog (newest first, through 2.5.9, tinycmdr-cli 1.0.9)

## 2.5.9 - the harness stops re-reading its own prompt, and a run cannot freeze on a note (2026-09-18)

A day of measuring the harness against its own traffic, plus one self-inflicted freeze found by
stack trace. The two headline fixes are the wall clock and the deadlock; the rest came out of the
same runs.

- **Every turn was re-reading the entire conversation.** The volatile state block (clock, notes,
  plan, ledger) was inserted before the last *user* message — which mid-run is the operator's task at
  the TOP of the history, so each tool round shifted the whole prompt and the server re-prefilled all
  of it. Measured on the LAN box: prompts growing 57k → 67k tokens with the reusable prefix pinned at
  6,993 (11% reused), 193-219 s of prefill per call. The block now trails the payload, and the
  request stays last only for a turn that is waiting on an answer (the 2026-09-10 turn-hijack rule).
  Same box, same job: prefix reuse 52% → 81% median, re-prefilled tokens per call ~27.7k → ~5.5k,
  first delta 60-231 s → 7-67 s, and a long job that used to die at the wall clock now finishes
  inside it (57 minutes, 33 model calls, 60 tool calls, 98% of the wall clock actually generating).
- **The notes guard could freeze the bot for ever, on the first `remember` after a start.**
  `record_authored_note()` took a plain `threading.Lock` and then called `notes_authored()`, which
  takes the same lock on its bootstrap path: a self-deadlock, and the tool batch that waited on it
  waited for ever. Found by `py-spy dump` on a frozen bot (no CPU, no socket to the model, listener
  still polling, session lock held). The lock is reentrant, and the regression test calls
  `record_authored_note()` FIRST in a cold interpreter under a timeout — checked against a copy of
  the pre-fix file, where it hangs.
- **One hung tool ended the run for the life of the process.** The batch waited through
  `list(ex.map(...))` inside a `with ThreadPoolExecutor(...)`, so even a timeout could not have
  helped: the executor's exit joins the hung worker. The batch is now time-boxed
  (`as_completed(timeout=shell_timeout + request_grace + 30)`) with `shutdown(wait=False)`, and an
  unfinished call is answered explicitly as a failure the model can react to.
- **An over-cap tool result was shredded, not kept.** `truncate_middle` dropped the middle and
  `raw=true` could not recover it (raw bypasses digestion, not the cap): measured with markers, a
  30,045-char result lost ~20,100 middle characters. Over-cap results now spill the full text to
  `spill/` (rotated, `spill_keep`) and hand back both ends plus a pointer that names `read_file
  offset/limit` and `search_files`. It fails soft — no spill dir, full disk, anything — falling back
  to the old truncation, because a disk problem must never break a run.
- **A run could spin announcing completion and never deliver.** No repeat-based guard can see it:
  every call was distinct. The harness now counts completion announcements that arrive with tool
  calls still queued, demands the report at `deliver_after_announcements` (3), and forces the wrap-up
  two announcements later — and auto-continue refuses such a run another segment, because a bigger
  budget is exactly what that state feeds.
- **A cap is a checkpoint, not the end of the job.** With plan steps still open, a step or wall-clock
  cap opens a fresh segment on the same task (`auto_continue`, up to `auto_continue_max`), plan,
  ledger and carried results intact, and tells the operator once. Sub-agents never continue.
- **Reading the same file again was a whole round trip.** The second and fourth read of a path in one
  run now carries the file's index — every class and def with its line number — so the model goes to
  a region instead of buying the file again. Counted by path, including paths buried inside
  `execute_code` source.
- **The safety seatbelt had one call site, and the pattern was wrong for code.** `is_blocked` was
  called only by `tool_shell`, so every `blocked_patterns` entry was one `execute_code` away; both
  places that admitted this now describe what is true (the check is on the text, not a boundary).
  The `rm -rf` patterns also required whitespace or `*` after the slash, so `os.system("rm -rf /")`
  did not match — they now look ahead for a path character instead.
- **Compaction kept nothing of what it destroyed.** Before anything is cut, the full text goes to
  `sessions/<key>.transcript.jsonl`, and the compaction count rides the usage line. The summarizer
  is deliberately not built yet: it needs a run that actually reaches the budget before it can be
  judged.
- **Smaller: the check-in line counted stream heartbeats as steps** ("step 780" on a 65-call run, and
  check-ins firing on heartbeats instead of at real steps), and the CLI/reference config gained the
  keys for the above (`auto_continue`, `auto_continue_max`, `deliver_after_announcements`,
  `session_transcript`, `spill_output`, `spill_keep`).

Not published to the tech site on purpose: this tree is frozen as the baseline for the repo move.


## 2.5.8 - the work state survives a crash, and nothing but the bot writes its memory (2026-09-17)

Two silent failures, both found on the fleet manager during a day of harness work, plus the
verification and cost work that came out of the same measurements.

- **The task ledger could be lost without a word.** `tasks.json` was found holding a complete JSON
  document with a duplicated fragment glued on, so every load raised "Extra data", the bot logged
  "starting a fresh ledger" and **20 items were invisible** - the file looked fine to anyone opening
  it. Every state file (`tasks.json`, `tasks.md`, `jobs.json`, global state, sessions, `config.json`)
  is now written atomically: sibling temp file, flush, `os.replace`. A ledger that is already damaged
  is salvaged instead: the longest valid JSON at the head is recovered and the damaged original is
  kept as `tasks.json.damaged-<stamp>`.
- **A bot's memory could be overwritten by anything that can write a file.** `notes.md` is not a log:
  it is the memory the agent re-reads in every prompt, capped at 4,000 chars, and the curator evicts
  the oldest entries into `notes-archive.md` when it overflows. Two ~1.3 kB entries appended by
  another process took the whole budget, and the curator - doing exactly its job - **evicted 28 of
  the bot's own facts** to make room. The bot lost its fleet knowledge mid-shift. Four fixes:
  `notes-authored.json` records hashes of the entries the bot wrote (bootstrapped from the file on
  first use, so existing memory is never mistaken for foreign); the curator now evicts **foreign
  entries first**, whatever their age, so a flood cannot push the bot's facts out; it logs a warning
  naming the count and the oldest foreign entry; and every render carries one line saying what the
  file is, so the next writer reads it before appending. Guarded by `tests/test_notes_guard.py`.
- **A written tool was accepted without ever being loaded.** A `tools/<name>.py` that the agent wrote
  was reported as done if it looked complete, even when the loader would have refused it. The
  verification of a write now runs the install's own tool loader over the new file: imports,
  `NAME`/`DESCRIPTION`/`SCHEMA`/`run` present, `NAME` matching the file name, `SCHEMA` an object, and
  the two `config.json` startup refusals. Measured on the graded set: 15/16 before, 16/16 after, and
  a purpose-built task (a deliberately broken custom tool) discriminates the two builds.
- **A broad filesystem scan could run for minutes unanswered.** Commands that walk a whole root are
  now classified and bounded: a per-command ceiling (60 s by default) applies, and a per-run budget
  over the seconds actually spent scanning across `shell` and `execute_code` ships **on at 120 s**
  (`agent.command_cost_guard: true` with `agent.scan_budget_seconds: 120`; set the former false, or
  the latter 0, to lift it). Measured: the single worst command in the graded set fell from 608 s to
  60 s; the run-level budget did not fire in two samples, so what it buys is a finite worst case, not
  a faster run. Corrected 2026-09-17: an earlier draft of this entry said the run budget ships off.
  It does not; the security-onion box caught that against the shipped defaults, and the pages it
  published carry the true behaviour.
- **A generated file could never announce itself.** The machine atlas listing trusted the file's own
  contents, so a file written a moment after the atlas draft - and every host whose atlas already
  existed - never showed it. The layout is now rebuilt from what is on disk.
- **Carried tool results: in the build, off by default.** A session's earlier tool results can ride
  into the next run of the same session, bounded, age-stamped and marked when a file changed since.
  Measured on the fleet manager: the later runs of a session re-bought **80% and 78% less** source
  text (two independent samples), but that is four measured runs per leg with a mixed wall clock, and
  the failure mode that matters - answering from stale carried text - is the least tested thing here.
  It ships off; a host that wants it sets `agent.tool_carry` in its own config.

## 2.5.7 - a dropped-in runbook now arrives with the tools this box actually has (2026-09-15)

Asked as a question about the site's own wording ("why are we claiming Hermes skill folders drop in
if things like this still happen"), after a framework desktop runbook was dropped into an install
whose `tools/` folder was empty and the agent spent the session reading instructions for a program it
did not have. Two defects, both silent, both fixed here:

- **A skill read said nothing about tools.** `skill read` handed the body over as if every step in it
  were runnable on this box. Every read now ends with the list of tools the install actually has and
  the rule that belongs with it: a step naming a tool that is not in that list was written for
  another build, so say so rather than hand-running it. It rides with the first page and with a
  section read, never with a continuation page.
- **An unknown tool answered like a hidden one.** Every unknown name came back as "You have tools not
  in your list; find_tools can reveal them", but `find_tools` searches *hidden* tools, which are
  tools the box has. For a name nothing here answers to, that told the model to go looking for a tool
  that never existed. The cases are now separate: hidden stays a reveal, absent says it is absent,
  names `list_tools`, says the runbook that named it is written for another build, and points at
  `create_tool` as the way to have that capability here.

Measured on the fleet manager's own install, which runs this build: **15 of the 45 runbooks in
`skills/` name at least one tool this build does not have** (the desktop one names `computer_use` and
the browser tools; the PDF and PowerPoint ones name a vision tool). No suite had ever read a skill, so
nothing caught it and the site's battery row for skill drop-in was a manual check of indexing.
`tests/test_ledger.py` gains `test_skill_read_names_the_tool_surface`, `tests/test_disclosure.py`
gains three checks for the absent-tool case, and both run against this build and the console build.

The docs now carry the half the old wording left out: `README.md`'s "Adding skills (drag and drop)",
the `skills/README.md` written into the download, and the console build's README.

## tinycmdr-cli 1.0.8 - the console build carries the ledger and memory fixes (2026-09-17)

Generated from the 2.5.8 source, so it inherits everything above that applies to a
single-operator console: atomic state writes with ledger salvage, the memory guard on
`notes.md`, the tool-write verification, and the broad-scan ceiling. The carried tool results
stay off here too (`agent.tool_carry: false`); an operator who wants them turns them on in
`config.json`. Opening this build still creates nothing: the memory guard writes only when the
agent does, and the one place that creates `sessions/` is still the first save.

## tinycmdr-cli 1.0.6 - a runbook's tools do not travel with it (2026-09-15)

The console build is generated from this one, so it carries both changes above: every skill read ends
with the tool surface of the install that read it, and an unknown tool no longer answers like a hidden
The shipped README's runbook section gains the paragraph that says which half of "drop-in" is
true. Those two code changes and the version line are the whole difference from 1.0.5: the archive's
own suite still reports 138 passed from a clean unpack, and opening it still creates nothing.


## tinycmdr-cli 1.0.5 - the launch path belongs to the machine, so the README says so (2026-09-15)

Reported from a locked-down workstation: the file was started from a prompt, printed nothing, and
then cmd/PowerShell windows multiplied until the machine had to be rebooted. The captured text
settles what happened: an editor's own startup log came out in that console and no `tinycmdr ...`
line appeared at all, so Python never ran the file - the machine's `.py` handler took it and
something in that chain started window after window. Nothing in this build can do that: it
allocates no console, starts no second copy of itself, and hands no file to another program, and
the one process it can start (the shell tool's child) is created with CREATE_NO_WINDOW. So what
moves here is what an operator can see and check, not what the agent does:

- **The README says to name the interpreter**, `python tinycmdr.py`, and why: on a managed machine
  `tinycmdr.py` on its own resolves to whatever owns `.py`, which may be an editor, a script host
  or a wrapper. It also states the first line the build prints - the build, the interpreter and the
  folder - as the proof that Python started it, and what a silent start means.
- **A troubleshooting section** for the three shapes of this failure: no output at all; a window
  that appears on its own, with the two commands that show which program owns `.py` and the event
  log query for the script that was looping; and the window that flashes and closes when the file
  is double-clicked.
- **`tests/test_cli.py` gains `test_it_can_never_open_a_window`**: it refuses CREATE_NEW_CONSOLE,
  DETACHED_PROCESS, AllocConsole, FreeConsole, ShellExecute, os.startfile, os.system(, shell=True,
  conhost or wt.exe anywhere in the shipped file, and checks that every child the agent starts is
  created with the hidden-process flags. A window, or a second copy of the agent, has to come from
  a deliberate change with that test updated - not from a side effect.

The file is otherwise byte-identical to 1.0.4, version string excepted: a fresh cut of the same
source was diffed against it before this release was cut, and a same-name re-cut was refused, as
always.

## 2.5.6 - the web page draws what the server says, in the order it says it (2026-09-15)

Reported from the operator's browser: a steering message landed at the TOP of the transcript,
bumping an older message away forever, and the model's text, thinking and tool lines came out
above and below each other. Three defects shipped in 2.5.4/2.5.5, and the ones that survived
survived because nothing tested the page:

- **The page keyed its DOM nodes by the bare line index.** Every run numbers its lines from
  zero, so run 2's first line overwrote run 1's first node - at the top of the log. A line
  that grew in place (streamed narration becoming the answer) was never re-read either, since
  the page only ever asked for lines at or after the highest index it had seen, so the answer
  never arrived and the truncated snapshot stayed on screen.
- **A reloaded page or a second tab had no run id, so it posted its message** and the server
  turned that into a steer of the run already going: the operator's own message appeared a
  second time while the first was pushed away.
- **A mid-run narration was painted as the answer.** The agent flags EVERY turn's narration
  as final; the page read that as "the run is over" and converted the streaming line into the
  answer bubble, which is why the page appeared to talk above and below the tool output.

Now: the transcript is a pure function of the server's ordered line list. Every line carries a
stable `uid`; the page reconciles the DOM against it (grows in place, never re-created, never
re-ordered, DOM touched only when a line is actually out of place), and it polls from zero
every time, so no cursor can lose a line that changed behind it. The server never emits a
mid-run answer - `finish()` promotes the run's last text to the answer exactly once, at the
end. A page that just loaded asks `/api/live` and re-attaches instead of guessing. The page is
also stamped with `{{VERSION}}` and served `no-store`, so a stale client announces itself and
offers a Reload instead of quietly running yesterday's code against today's server.

Stop is reachable mid-run from the keyboard (`/stop`) and from the header, the Send button
stays visible while the agent works (on a phone it was hidden, so a mid-run steer was
impossible), and the transcript survives a reload.

Tested at three levels, because "the tests pass" was demonstrably not enough:
`tests/test_webui.py` (81 checks) drives the HTTP endpoints; `tests/test_webui_page.py` (28
checks) runs the page's real script in Node against a DOM shim and a fake server that mirrors
WebRun's line semantics, including a mid-run reload; `tests/test_webui_browser.py` (77 checks)
drives real Edge against the real server and asserts the browser's DOM equals the server's
ordered lines for a simple run, a mid-run steer, a mid-run reload and a mid-run stop. Each
defect above has a mutation test that fails when it is reintroduced (mid-run final: 6 failing
checks; no `/api/live`: 4; no `no-store`: 1; no answer promotion: 3; the published 2.5.4 page
against the page suite: 11).

## 2.5.5 - a model key belongs to the host, not to the package (2026-09-15)

Three installers could put ONE DeepSeek key on every host they touched, and a redo could
replace the key a bot already had:

- `install-tinycmdr.ps1` copied `.env.example` over the host's `.env`, then wrote
  `DEEPSEEK_API_KEY` from `install/fleet-secrets.env`.
- `install-tinycmdr.sh` took the same value on a fresh install.
- `install-tinycmdr-macos.sh` appended every `KEY=VALUE` line from `--secrets-file`.

The cost shows up on the provider's dashboard, not on the box: three hosts in this fleet
carried one key while the per-bot keys they had been issued sat unused since the copy, and
one host's `.env` backup from that evening kept its own key only as a commented line. Search
keys are genuinely fleet-wide and still deploy from the secrets file; a model key is not,
because per-host usage tracking is the whole point of one key per bot.

Now, in all three installers: a `DEEPSEEK_API_KEY` in a shared file is ignored with a line
saying so, and the key a host already has survives a redo. Verified with the `.env` block run
against throwaway files in both cases (host has its own key / host has none), six logic
checks over the PowerShell section, `Parser::ParseFile` on that installer, `bash -n` on both
shell installers, and the suites run from a clean unpack of each published archive.

## 2.5.4 - the local web page stops repeating itself (2026-09-14)

Reported from the Windows test box: a message appeared twice, and the answer painted hundreds of
near-identical lines. Two separate defects in the page's line buffer.

The browser echoed the message into the transcript and the server echoed it as well, so
every message posted from the page showed up twice - the reader's own copy and the run's
copy, both locally added.

The repeated answer was the narration callbacks. They hand over snapshots of the model's
text as it streams (the Mattermost view edits one post as it grows), and the web layer
appended one buffer line per snapshot. Measured on the manager box before the fix: a 5-second run
painted 192 lines, of which 111 were the same sentence over and over. A snapshot that
repeats the previous line is now a no-op, one that extends it replaces it in place
(keeping the line index, so the page repaints the line it already drew), and only a
genuinely different text appends. The same run now paints two lines: the message and the
answer. `say` and `final` count as one streamed text, because the final flag only flips on
the last chunk of that text.

Third defect found while testing: a message sent while a run was already going (a tab that
had reloaded and lost its run id, or a second tab) was refused with nothing but `busy` and
dropped. It is now queued into the running run as a steer, and echoed into that run's
buffer so the sender sees it.

Suite: `python tests/test_webui.py` - narration growth, the repeated-fragment no-op, the
say-to-final handoff, and the single message echo are checked at the unit level, and the
page's index-keyed renderer is asserted, because a parallel edit once reverted half of this
patch while the suite stayed green.

Second defect, found while proving this archive: on Windows, the shipped suite failed one
check from a clean unpack. `tests/test_cli.py` ran the console build as a subprocess with
`text=True`, which decodes the child's output with the console's own codec - cp1252 on
Windows - and that build prints a warning sign (U+26A0) on the infra-failure path. The
reader thread raised UnicodeDecodeError, `stdout` came back `None`, and a run that behaved
correctly was reported as a failure to whoever unpacked the download. The six subprocess
calls in that suite now decode as UTF-8 with `errors="replace"`, so the suite reports what
happened on every platform.

## 2.5.3 - the bundled console build catches up (2026-09-14)

The bot build's own code is unchanged. The version moved because this archive carries two
agents, and the second one - `cli/tinycmdr-cli.py`, for a terminal with no chat server -
was still the 1.0.3 copy: it wrote a config.json for you on first run and drafted an
atlas.md on every run. The console build has stopped doing both, and a published artifact
that still does them is a defect, so it gets a new version rather than a note. What this
archive bundles now is the console build the other page documents (tinycmdr-cli 1.0.4):
opening it creates nothing and checks nothing, and its machine atlas is a document the
reader can edit.

That atlas is not bundled here, on purpose: its layout section names the files of the
folder it sits in, and in this archive the console build lives in `cli/`. A map that names
the wrong files is worse than no map, so this archive ships none and links to the page that
explains it.

Verified from a clean unpack of both archives, not from the source tree: Windows and Linux
(Ubuntu, python 3.10.12) each ran the shipped suites - stall 195, ledger 208, checkin 84,
cli 120 (2 skips for repo-only files, printed and counted separately), atlas green - and
the bundled `cli/tinycmdr-cli.py` is byte-identical to the console archive's `tinycmdr.py`.

## tinycmdr-cli 1.0.4 - nothing is created at startup (2026-09-14)

The console build touched the folder before it knew whether it could start, and it
asked for a config by writing one. Both are gone. The rule the operator set is the
rule now: opening it creates nothing and checks nothing.

```
on open, before                      now
tinycmdr.log   (handler at import)   nothing - the file is created with its first line
sessions/      (Agent() at import)   nothing - created on the first save
tools/         (ToolRegistry)        nothing - created with the first tool written
config.json    written by a three-   the steps printed (copy/rename config.example.json,
               question wizard        fill three llm fields), then exit 2, nothing written
atlas.md       draft on every run    shipped with the build, one per platform, and never
                                      regenerated: a document the reader can edit
```

Verified on the artifact, not on the source tree: with a fresh folder, `--version`,
`--help`, a start with no config.json, and an interactive open with a valid config
all leave the folder byte-for-byte as it was (snapshot before and after). The
packager now asserts that on the clean unpack of the published zip, so the claim
travels with the archive. One real request still records what it did:
tinycmdr.log and sessions/cli.json appear at that point, which is the point of them.

The system prompt stopped pointing at update channels. Windows Update,
PSWindowsUpdate, winget and DISM-as-an-update-path, plus the line "when the OS
update channel offers a stable update, take it", are out; on a segregated network
that guidance produced exactly the wrong behaviour, which is what the operator saw
as the window narrating checks and updates. It now says the network is closed,
nothing is fetched or installed, and a missing piece is reported rather than chased.

The narration example lost its "Checking what holds the file lock" wording for the
same reason: "check" as the model's first verb invited a pile of checking lines.

The atlas came back, differently. It was going to be cut, because the harness wrote
a draft of it on every run; instead it is now the one document the archive ships.
`maintenance/atlas-cli-win.md` and `maintenance/atlas-cli-linux.md` are written into
each archive as `atlas.md` - the Windows zip carries the PowerShell one, the tarball
the bash/systemd one - so the reader gets a map of the machine they are on, with the
native commands for that OS, without the agent creating anything. Nothing regenerates
it: edit it and the edit stays, delete it and the block is simply empty. What ships
is checked, not hoped: the packager refuses if a name in the atlas's `## layout`
section is not a file the package contains (unless it is one the agent creates later),
and the clean-unpack proof asserts each archive carries its own platform's atlas. The
rendered header no longer says the facts came "from the harness", because now they
came from the folder beside it.

Tests: `tests/test_cli.py` 133 checks green (was 90). Its wizard test is replaced by
two that fail on the old behaviour - one measures the folder before and after every
way of opening it, one reads the printed steps and the exit code - and this pass adds
two for the atlas: the shipped file reaches the request and nothing recreates it, and
the sources name only files the package has. `test_ledger.py` 173 pass / 0 fail /
11 skipped against this build, `test_plan`, `test_digest`, `test_verify` and
`test_disclosure` green with `tinycmdr_TEST_APP=tinycmdr-cli.py`, and `test_atlas.py`
green against both builds (it asserted the old header wording, which is the one check
that had to move). `test_stall.py` and `test_checkin.py` fail against this build
exactly as they did before (chat layer, cut by design).

Linux arm, run from a clean unpack of the tarball on Ubuntu 3.10.12 rather than
trusted: `--version` and `--help` fine, a start with no config.json printed the steps,
exited 2 and left the folder identical, and the suite gave 117 passed / 0 failed /
4 skipped. That run found two real defects in the suite itself, both fixed here: two
checks read repo-only files (`tinycmdr.py` beside it, `maintenance/`) and raised
FileNotFoundError instead of skipping, which is exactly what a stranger unpacking an
archive would have hit.

Version numbering: 1.9.x ran to 1.9.32; v2.0.0 begins the 2.0.x scheme (2026-09-13).

## 2.5.2 - the shipped suites tell the truth on Linux

All of this came from running the 2.5.1 archive's own test files on a Linux box, which is a platform
the package ships an installer for and which had never been used to run them.

`tests/test_digest.py` failed there, and the fault was mine: the check added with the elevation work
asserted that a Windows-scoped field note fires, and the note library is scope-filtered, so on POSIX
that entry cannot fire at all. It now asserts the note only where it can fire, and asserts the POSIX
counterpart in its place (a sudo password prompt gets its note), so POSIX has coverage of the same
feature rather than a red line.

`tests/test_cli.py` failed "powershell mode still works" on a Linux host that HAS PowerShell. The app
chooses PowerShell on Windows and bash everywhere else whatever `agent.shell` says (`["powershell",
...] if IS_WINDOWS else ["bash", "-c", command]`), so the expectation was Windows-only. The suite now
follows the app: the Windows switch is tested on Windows, and on POSIX the same function checks that
the setting is ignored and that the interpreter really is bash.

`tests/test_stall.py` failed five checks on a host without the chat driver, because
validate_startup_config() reports the missing mmpy_bot before it reads a single config value, so none
of those messages can be produced there. Those two suites report a skip with that reason instead. A
skip is printed, counted separately and never counted as a pass, because a suite that could not test
its subject must not claim it did.

The README's verify section now says to install requirements.txt first, and what a skip means.

Measured, not assumed. Windows: every suite green (digest, cli 94, stall 195, webui, ledger 210,
checkin 84, atlas, plan, verify, disclosure). Linux with the bot's own venv: stall 195, cli 93 plus a
skip, digest and webui green. Linux with a bare interpreter that has no mmpy_bot: 0 failures, 2 skips,
exit 0.

## 2.5.1 - the shipped suite finds its file, and a hung-up client stops looking like a crash

Found by running a clean unpack of 2.5.0 rather than the working tree, which is the only place
this shows up.

`tests/test_cli.py` resolved the console build at the package root. That is where it lives in the
repo, and NOT where the archive puts it (`cli/`), so from an unpacked download the suite died on a
missing file instead of testing anything. It now looks in both places, reports one skip for a check
that needs a file the package never ships (the packager source itself), and prints one sentence and
stops when the console build is absent instead of raising. All three layouts are verified: working
repo 94 passed, package layout 93 passed plus that skip, no console build present gives a clean skip.

It also turned up a real defect in the server, and not the one I first blamed. The suite run from
that unpack printed `ConnectionAbortedError` tracebacks. They come from the handler writing a reply
into a socket the caller has already closed, which is ordinary traffic: a browser tab shut mid-poll,
or a script that stops reading once it has what it wanted. The stock server answers that with a full
traceback, so the agent's own log fills with what look like crashes. The local web server now logs
one line and keeps serving. Proven with a probe that opens a request and closes the socket before
the reply is written: 4 requests produced 4 tracebacks before the change and 0 after. Two smaller
things came out of the same investigation. The handler drains a request body before answering early
(refusing a POST while its body is still arriving is what makes Windows reset the connection instead
of delivering the 401, so a refusal could look like a crash to the caller), and the suite retries a
torn-down socket once instead of reporting a flaky environment as a defect.

The changelog's own title line still read "v1.9.4 to v1.9.13" over a 2.5.0 body inside every
archive. It now reads "newest first, through 2.5.1", which does not need touching again.

## 2.5.0 - no chat server required

Driver: the operator's question. What can we ship so nobody has to stand up a Mattermost
server to try this? Three answers, in build order.

**The local page shows the work while it happens.** The web UI was one synchronous request:
it answered when the run ended, so a browser sat empty while the agent worked, which made it
worse than the console rather than better. It is now a live view of the run.

```
POST /api/run        starts the run on its own thread, answers at once with a run id
GET  /api/events     the lines so far; the page polls every 700ms while a run is going
POST /api/steer      hands a message to the agent mid-run, like typing in chat
POST /api/stop       ends the run after the current step (the normal stop path)
POST /api/chat       unchanged and synchronous, so /status, scripts and the fleet tooling work
python --web         serves that page and nothing else: no bot account, no chat server, no token
```

Every line carries a kind (you, say, tool, tool_done, tool_fail, final, error) and a time
stamp, so the page can render a tool call as it starts and the same tool when it finishes,
with a failure marked as a failure. The page itself is two panes with the input pinned to the
bottom, which is the thing the console cannot do. A run already in flight refuses a second one
on the same session instead of interleaving two histories.

**The console build ships inside the bot package.** `cli/tinycmdr-cli.py` plus `cli/README.txt`
in every archive, so one download covers both ways of using it: a terminal, or Mattermost. The
folder README says which file to run and which to leave alone.

**The README no longer implies a server is required.** It opens with the three local paths
(`--cli`, `--once`, `--web`) and says outright that Mattermost is optional, which it always
was: the console path runs before any chat config is validated.

Three fixes that the work turned up. `tests/test_stall.py` had been failing for a while and
shipped that way: its three model stubs predated the `session_key` argument on `_chat`, so every
scripted turn died on `unexpected keyword argument`. It passes now (195 checks), which matters
because it is one of the suites the README tells a stranger to run. `MANIFEST.txt` was listing
the files before writing itself, so its tally was one short of what actually shipped. And the web UI's own config comment read
"there is usually no reason to turn it on", which stopped being true the moment the page became
the no-server way in.

## 2.4.1 - shell rights, and caps that fit real work

Both from an evening of the operator using the console build for real.

**The elevation mystery.** Launched from an ordinary terminal on an account that IS an
administrator, the console build hit `Access is denied` reading event logs and a root WMI class,
and reported it as "permission elevation is denied for this shell". Nothing in the harness was
denying anything: UAC gives an administrator two tokens, and a process started from a normal
console gets the filtered one. The bot build never meets this because its Scheduled Task runs
with highest privileges.

```
is_elevated()        the process token on Windows (IsUserAnAdmin), euid on POSIX
shell_rights_line()  one line in the trailing block, on the first turn of a run and again
                     when a failure looks like a rights problem: elevated or not, and what
                     that means for admin-only work, including "do not retry it"
field notes          two NARROW entries (a Windows elevation phrase, a sudo password prompt).
                     A bare "Access is denied" is deliberately NOT a signature: it also means
                     a locked file or an ACL, and a wrong hint costs a small model more than
                     none, which is this library's own recorded lesson
console build        prints one line before an interactive session when the console is not
                     elevated, so the operator knows before starting rather than mid-task
config               shell_facts (True)
```

**The caps.** `max_steps` 40, `max_minutes` 10 and `max_turns` 40 were arbitrary and cut
troubleshooting sessions short, which is the work this build is pointed at. Now 100 tool calls,
35 minutes and 100 turns everywhere: the code defaults, the fleet's configs, the bot example
and the console build, so one number means one thing. The console build's `config.example.json`
also stopped hiding them: it documented only `llm`, so nothing in the package said how long a
task may run. It now carries the budgets with their defaults and what each one does.

## 2.4.0 - the harness hands over the map (item 2b, the machine atlas)

The measured failure this answers: on the graded task set the most common tool error is a
GUESSED PATH (asked for `data/report.csv` when the file was `data/2026/report.csv`; assumed
fixtures lived under `tools/`), and the model never says which box it thinks it is on. The
harness knows both, so it stops hoping the model will look.

```
atlas.md      a per-host file: `## host` (os, shell the shell tool uses, install path,
              scratch, log, web port, model endpoint), `## layout` (where things are on
              that box, including a bounded two-level listing of the install), `## notes`
              (hand-written facts, the curated half)
attached      in the TRAILING state block, on the FIRST turn of a run, and again after any
              tool failure that reads like a wrong path (no such file, cannot find path,
              command not found...). Not in the system prompt, which is prefix-cached, and
              not re-sent every turn, because on the other 11 turns of a run it is
              geography nobody asked for
generated     on the host, never shipped: a fresh install writes a DRAFT at its first run
              (python facts only, no shell probes) and its `## notes` survive regeneration
config        atlas_enabled (True), atlas_file (atlas.md), atlas_max_chars (2400;
              it bounds the listing, not the host facts or the curated notes)
```

Cost: about 400 tokens, once per run, plus another 400 only when a path failure asks for it.
Fixed overhead per call is unchanged.

Tests: `tests/test_atlas.py`, 34 checks. Two of them drive a real turn against a stubbed
model and read the REQUEST BODY, because "it is in the first request" and "it comes back
after a wrong-path failure" are claims about the payload and nothing else can prove them.
Two real bugs came out of writing them: the parser rejected the bullet form the generator
writes, and layout lines (`name  purpose`, no colon) were being dropped entirely.

Measured, full set: 15/16 both legs, so no change in success. Measured again three times each
way on the four path-touching tasks: 12/12 both legs, wrong-path tool errors **0 with the
atlas, 4 without**, steps 36 against 46, prompt tokens 181,162 against 193,072. The atlas
prevents the wrong path rather than fixing it after the fact, which is why it attaches on the
first turn and not only after a failure.

## 2.3.0, sha256 e1127b09e739e700 - the harness holds the plan and the runway (2026-09-13)

The model's wasted work clusters in the MIDDLE of long runs — re-reading what it read,
re-verifying what it just verified — and it has no sense of runway: the first time it
learns it is near the cap is the wrap-up message. Both are harness problems, so the
harness now owns them.

```
plan tool      set / doing / done / blocked / drop / note / show / clear, one step per
               line on `set`, a one-line evidence note on `done`
re-sent        every turn, inside the trailing state block (which is re-read anyway, so
               it costs its own length and nothing else): the plan with statuses, plus
               "Run so far: 12 of 40 tool calls used (30%), about 28 left"
drift nudge    after plan_drift_after (8) tool calls with no step moving, the harness says
               so and quotes the current step
at the cap     the forced wrap-up now names the plan steps that are still open, by text
persistence    the plan survives across runs in a session, so a run that lands on the
               budget hands its open steps to the next message instead of losing them
```

Config: `plan_enabled` (true), `plan_drift_after` (8), `plan_max_steps` (12). Setting
`plan_enabled` false removes the tool AND its standing instruction, which is how the eval
compares the same build with and without it (see `docs/tinycmdr-harness-scaffolding-plan.md`).

Honest tuning, measured after the feature was built: offered the plan tool in sixteen
graded runs with a standing instruction, the model called it ZERO times, and it still did
not touch it in three further runs where the harness had already parsed the steps out of
the request. So the design changed to match the evidence — the tool is no longer in the
always-visible set (it stays in the registry, one find_tools call away) and the standing
instruction is gone (the trailing block says the same thing, contextually, only when a
plan exists). What remains costs about 25-40 tokens on a multi-part run and nothing on any
other run, and the parts that need no cooperation are the ones kept: the derived checklist,
the position line, the drift nudge, and the wrap-up naming open steps by text.

Tests: `tests/test_plan.py`, 33 checks, two of which drive a real turn against a stubbed
model and inspect the REQUEST BODY — that the plan and the runway are re-sent, that the
drift nudge reaches the model, and that the wrap-up names the open steps. That suite also
grew a build selector and is now run against BOTH builds; pointing it at the generated CLI
for the first time is what exposed the config drift recorded at the end of this entry.
While adding the session plumbing, `test_ledger`'s structural check ("every tool-calling
`_chat` call goes through `_payload`") was rewritten as an AST check: it had been counting a
literal string in the source, so a reformatted call failed a check whose invariant was intact.

### Generated build: the config block had drifted 31 keys behind (found while shipping 2.3.0)

`tinycmdr-cli.py` is generated, and its `DEFAULT_CONFIG` is a CURATED copy of the real agent
defaults, kept by hand in `maintenance/cli_blocks.py`. Nothing compared the two, so the copy
stopped growing: at 2.3.0 it was 31 agent keys behind, meaning the enterprise build ran the
2.1.0-2.3.0 features (digestion, verification, field notes, tool disclosure, the plan) on
nothing but their code fallbacks, and a direct `CONFIG["agent"][key]` lookup raised KeyError.
It surfaced the moment the new suites were pointed at that build:

```
tinycmdr_TEST_APP=tinycmdr-cli.py python tests/test_plan.py        KeyError: 'plan_max_steps'
tinycmdr_TEST_APP=tinycmdr-cli.py python tests/test_digest.py      1 check failed
tinycmdr_TEST_APP=tinycmdr-cli.py python tests/test_verify.py      1 check failed
tinycmdr_TEST_APP=tinycmdr-cli.py python tests/test_disclosure.py  5 checks failed
```

Every one of those suites had only ever targeted `tinycmdr.py`. Three fixes, all in the build
or the suites rather than in the generated file:

```
build-cli-source.py  after every cut, copy in the keys from the real defaults that THIS
                     build's code can actually read (a key whose code was cut is
                     decoration, so the chat-only stall_/checkin_/catch_up/vision families
                     stay out). Refuses the build if the curated block defines a key the app
                     no longer has. 21 -> 36 keys, and the build log lists what it inherited.
tests/test_cli.py    new guard comparing the generated config against tinycmdr.py's defaults,
                     so the next default added is covered without a hand-kept list. 94 checks.
tests/test_disclosure.py  was hardcoded to "schedule", a tool this build cuts, so 5 checks
                     failed there for the wrong reason; it now takes its example tool from the
                     build's own registry and passes on both.
```

Every suite now runs against both builds, green on both.

Fixed overhead is now 4,306 tokens (13 visible tools of 21), from 5,491 before 2.2.0.

## 2.2.1, sha256 2f967d1d43939927 - do not work around a hidden tool (2026-09-13)

The first live probes of disclosure found the gap that tests could not: five probes on the
running bot, all five answered correctly, but one of them **worked around** a hidden tool
instead of revealing it. Asked about the notes budget, the bot said "the built-in `notes`
tool isn't in my callable set here, so I reproduced what it would print", then read the
tool's source and re-implemented its logic with execute_code: 41.8s and a pile of tokens
for something one call would have done. The door was open the whole time (calling a hidden
tool works and reveals it); the model just treated the visible list as the truth.

Another probe showed discovery working as designed — asked what we did about Plex posters,
it called `search_sessions` (hidden) without being told to.

So the standing instruction now says it out loud: if a task needs something you would
expect an agent to have, call find_tools FIRST, and do not work around a hidden tool by
re-implementing it, reading its source, or hand-rolling the equivalent command. The line
carries the measured cost of ignoring it, because a rule with a number behind it is the
only kind this build keeps.

Suites green after the change: 31 + 53 + 28 + 34 + 84 + 92 + 208, CLI regenerated and
green. Prompt cost of the added sentence: ~40 tokens, against the 1,537 this release
removed.

## 2.2.0, sha256 6c34e90c59d28eda - the tool list is short on purpose (2026-09-13)

The request used to carry every tool schema on the machine: 2,847 tokens of the 5,242
of fixed overhead per call, on a box whose own log shows 88% of ~2,760 real calls were
five primitives. Now the payload carries twelve tools and the rest is revealed on
demand, which measures as 5,491 -> 3,954 tokens of fixed overhead (**-1,537 per call**).

```
visible always     shell, execute_code, read_file, write_file, edit_file (the five that
                   are 88% of real calls), skill, task, remember (the doors the standing
                   instructions name), web_search, fetch_url, list_tools, find_tools
hidden until used  schedule, create_tool, search_files, search_sessions, notes,
                   delegate_task, and every custom tool on the box
```

Two ways in, because hiding a tool is only safe if nothing becomes unreachable:

```
find_tools("schedule a job")   returns the match WITH its arguments and reveals it for
                               the rest of the session; all=true reveals everything
calling it anyway              honoured, executed, and then kept in the list, with a line
                               in the result saying so. A refusal would cost a step and
                               teach the model to distrust what it knows about this box.
```

The human-facing half matters as much: the system prompt now says the short list is
deliberate and that "never claim a capability is missing without checking" is the rule,
and an unknown tool name comes back with the closest real matches attached.

`tool_disclosure: false` sends the whole registry again, and `core_tools` overrides the
visible list, so the tuning knob and the rollback are both config, not code.

Tests: `tests/test_disclosure.py`, 31 checks offline, including a stubbed turn that
inspects the REQUEST BODY (the visible set is what was sent, a hidden tool is absent, and
a revealed one rides in the next request). Full suites after this change: 31 + 53 + 28 +
34 + 84 + 92 + 208, all green, CLI regenerated and green.

What is NOT proven yet, stated plainly: that a model under a real multi-step task finds a
hidden tool it needs instead of giving up. The eval's 16 tasks run both legs (on and off)
for that, but the tasks were written before disclosure existed, so a pass is weak evidence
and a regression would be strong evidence. Live probes on the the manager box bot are the next check.

## 2.1.0, sha256 98d3aca71f4ef811 - the harness shapes what the model reads (2026-09-13)

Three features that work on the TEXT a tool returns, rather than on getting the model to
behave. Each one is deterministic, costs no prompt tokens for the logic itself, and has
its own off switch in `config.json` (so the rollback for any of them is a config edit,
not a rebuild).

```
digest          renders a KNOWN command shape down to its signal before the model reads
                it: journalctl and *.log keep error/warn/fail lines, systemctl keeps the
                unit header plus journal lines, apt/dnf/pip put failures first, grep
                keeps the first N, ps/ls/docker ps/Get-ChildItem keep head and tail. The
                result says what was dropped and honours raw=true for the full text.
                Runs before the 6000-char cap, so it selects from the whole output.

field notes     a FAILED result whose signature is understood gets the known cause
                appended, from field-notes.md (11 entries, each traceable to a recorded
                incident, scoped by platform). Applied in _exec_tool, so custom tools
                get it too.

verification    after write_file / edit_file / create_tool the harness parses the file
                in the language its extension claims (python, json, toml, yaml, bash)
                and, for a custom tool, checks the contract the loader enforces
                (NAME, DESCRIPTION, SCHEMA, run). The verdict lands in the same result.
```

Why these: measured on this box's own log, 88% of ~2,760 real tool calls were five
primitives with shell first, so the model's diet is mostly other programs' output. The
old behaviour cut a 30-line error out of 6k chars of routine output; that is scissors,
not judgement.

Measured, three runs per leg, same model and task, feature toggled by config:

```
digestion on/off    T13 (13 KB log, error in the middle)   3 steps / 22,214 tokens
                                                           5 steps / 37,985 tokens
field notes on/off  T14 (a command that does not exist)    fires 3/3, but 4x steps and
                                                           3.6x tokens, because that
                                                           signature is ambiguous
verification on/off T15+T16                                with it: "invalid JSON at line
                                                           1". Without it: "Done."
fixed overhead      5186 -> 5242 tokens (the three raw arguments; verification adds none)
```

The field-note result is the honest one: the mechanism fires reliably and on an
ambiguous signature it is a net cost, so the library carries two new rules (only
signatures that essentially always mean that cause, no imperative actions) and the
ambiguous entry was rewritten. A note that guesses is worse than no note.

Tests: new `tests/test_digest.py` (53 checks) and `tests/test_verify.py` (28), both
offline; `tests/run_eval.py` now runs a 16-task graded set with per-feature counters and
per-task sandbox logs under `tests/eval-runs/`. Full suites green: 53 + 28 + 34 + 84 +
92 + 171.

Two bugs the new tests caught before this shipped, both cases of the harness lying:
`bash` on this host is a WSL relay that cannot exec `/bin/bash`, so a good script was
reported as a shell syntax error (a verifier may now only fail when the failure is a
verdict about the file); and the custom-tool contract check called `compile()` and then
walked it as if it were a tree.

Plan, measurements and the items still open: `docs/tinycmdr-harness-scaffolding-plan.md`.

## 2.0.0, sha256 df5881e9f409acc6 - an empty turn no longer ends the run (2026-09-13)

A reasoning model can end its own turn right after starting to think: the response carries no
`content`, a few characters of `reasoning_content`, and `finish_reason=stop`. The harness read that
as the final answer, posted "the model returned only reasoning" and stopped the run, so the task sat
dead until the operator prompted again. Six of those in the fleet between 2026-09-11 and 09-13, on a
LAN llama.cpp server and on cloud endpoints alike, always inside a tool loop, never near the context
window and never truncated.

The turn is now asked once more on the same endpoint:

```
drop     the empty assistant turn (a transcript whose last word is the model's own silence
         invites the same silence, and strict providers reject an empty assistant message)
nudge    "SYSTEM: your previous turn came back EMPTY ... continue the task now", as the
         last user message, so it is what the model answers
retry    same endpoint, same turn, once per run (`usage["empty_retry"]`). Only if that comes
         back empty too does the operator see a warning - and that warning no longer sends
         them to llm.no_think, because thinking is by design on these models
```

Chosen over raising `max_tokens`: the generation is degenerate, not truncated, and the sibling
failure (`finish_reason=length`, reasoning ate the whole budget) already had this retry. Every host
was restarted onto this hash and `maintenance/fleet-version-report.ps1` shows all six in sync.

Tests: `tests/test_checkin.py` gains 8 checks (the retry happens, the empty turn is dropped, the nudge
lands last, two empty turns in a row still end the run with honest text). That suite was also
importing without `os`, so it had not been running at all - fixed.

## v1.0.0 — enterprise build: one file, one approved endpoint, a terminal (2026-09-13)

A second build of the same agent for environments where the rules are stricter than the ones the
Mattermost bot assumes. It is generated from `tinycmdr.py` by `maintenance/build-cli-source.py` plus
`maintenance/build-cli-fix.py` (4,172 lines, sha256 `46dcacad0469dc55`); the pair is byte-reproducible,
every cut anchor is asserted, and the cut spans come from the file's own AST. Do not hand-edit the
result.

What it is: `tinycmdr.py`, a README and a config example in a folder. Standard library only, so there
is no pip or conda step; start it with the Python the machine already has:

    python tinycmdr.py                      interactive
    python tinycmdr.py --once "<task>"      one task, then exit

First run asks for the endpoint, the model and the key, and writes config.json. Everything it keeps
(sessions, notes, ledger, log, tools) stays inside the folder; nothing is written to the user's home
directory.

Built for a DoD / IL5 posture, which is what shaped the decisions:

```
one destination   every HTTP call resolves through llm.base_url. There is no other host
                  literal in the file, and no tool can reach anywhere else. Asserted by
                  tests/test_cli.py, checkable by grep
no web search     no search tool and no provider keys, and no URL-fetch tool either:
                  nothing in the build phones out except the model
no scripts        no .bat, no .cmd, no .ps1, no installer. Environments that whitelist
                  executables block those, so the folder holds a Python file and nothing
                  executable. Start it from Anaconda Prompt or any prompt with python on PATH
one endpoint      config.json holds exactly one entry: the endpoint and the model id.
                  Everything else is a default inside the file
no credentials    no key plumbing and no certificate handling: the platform authenticates, the
                  system trust store is used, and no Authorization header is sent at all unless
                  a key is actually configured (never a placeholder bearer)
plain http        a warning is logged if the endpoint is http and not loopback: the
                  conversation and the tool output it carries would travel unencrypted
shell choice      agent.shell = "powershell" (default) or "cmd", for hosts where PowerShell is
                  restricted or removed. The blocklist covers cmd's own destructive forms too
provider-neutral  the llama.cpp-only switches (stream_options, the no_think template flag) are
                  never sent off-LAN: a hosted gateway would reject a field it does not know
context budget    the prompt budget is 500,000 tokens, matching the 1M-token window the
                  enterprise model carries, so compaction almost never fires. It is a budget
                  rather than the window: a runaway session is capped here, and if the server
                  rejects the prompt anyway the build shrinks and retries
token cap         if a provider rejects max_tokens as too large (DeepSeek caps output far
                  below this build's 16,384 default), the cap is halved once and the call
                  retried, instead of a run dying on a number in our defaults
```

What came out of the Mattermost build, measured: the chat layer (dispatcher, progress reporter,
status text, colour bars, catch-up sweep, attachments, allowed_users, web UI) 1,522 lines; the stall
watchdog, check-in cadence, instance lock and restart handover 202 lines; the Scheduler class and its
tool 190 lines; the failover list, cloud-fallback gate and model routing 44 lines; the web search
providers and tools 62 lines; `requests` replaced by a standard-library shim that keeps the same name,
so all 12 call sites and every monkeypatch still work.

What replaced them: a single endpoint read straight from config.json, and a terminal client with the
verbs the chat commands mapped to (`/new`, `/model`, `/status`, `/tasks`, `/notes`, `/skills`,
`/tools`, `/usage`, `/exit`), green narration, amber tool lines, red failures, Ctrl-C to stop a run in
flight, and `--once` for one-shots.

Verified: `tests/test_cli.py` 90 checks green (the shim, the wizard, the single-endpoint config, the
verbs, the TLS/key/shell paths, the tool loop, and the "endpoint is the only destination" claim);
`tests/test_ledger.py` 171 green against this build, 11 chat-only tests skipped by name; a clean
unpack of the archive runs both. Live: a real task over TLS, a real task against a LAN endpoint, a
tool call that really executed, and a full run on the Windows test box started the way the README prescribes.

Not carried over on purpose: the stall watchdog and check-ins (a human is watching the window), the
scheduler (nothing runs while the window is closed: hand a `--once` line to the OS scheduler), vision
and attachments, the per-channel worker queue, steering, and the restart handover.

## v2.0.0 — the agent knows what time it is (2026-09-13)

Until now nothing in the prompt carried a clock. The system prompt is byte-static on purpose (any
change there invalidates the server's prefix cache for the whole conversation), user messages went in
verbatim, and the only way the bot could answer "what day is it" was to shell out to `date` first. It
did that honestly and often: three `Get-Date` calls in the two days before this release.

Now the trailing block carries one line:

    Current date and time on this machine: 2026-09-13 07:17:37 -0700 (Sunday, Pacific Daylight Time)

It is in the trailing block because that block is re-read on every call anyway, so a line that changes
every minute costs about 25 tokens and nothing else. The same line in the system prompt would re-prefill
the conversation every minute. Local time with its UTC offset and zone name, so "yesterday",
"tomorrow" and "how long ago" resolve without a shell call.

Verified live on the manager box: asked for the date, time, timezone, weekday and yesterday's date with tools
forbidden, the bot answered 2026-09-13, 07:18:03, UTC-07:00 Pacific Daylight Time, Sunday and
2026-09-12 without making a single tool call, and named the block as its source. Its own caveat is the
right one: the stamp is the moment the block was built, so it is a floor rather than the second.

This release also switches the numbering scheme to 2.0.x, which is the only reason the version moves a
major step for one feature.

## v1.9.32 — the repeat guard no longer outlives the change it was measured against (2026-09-12)

Found by reading the Windows test box's own log after the operator flagged it:

    13:24:42  computer_use({"action": "list_apps"})          -> 569 chars
    13:27:12  edit_file(C:leetbot	ools\computer_use.py)   <- the fix
    13:27:42  computer_use({"action": "list_apps"})          -> 569 chars (identical)
    13:27:42  WARNING loop guard: computer_use repeated identically 2 time(s)

The bot read that identical output as "the harness caching a pre-edit call". It was not
cached - it was real - but the guard keyed only on (tool, args), so a THIRD attempt would
have been refused and handed back the PRE-EDIT result, after which a fixed tool looks
broken. This is not specific to computer_use: it applies to every tool on every bot.

- Any real mutation (write_file / edit_file / create_tool) now clears the repeat memory:
  "fix it, then run the same check again" is the most common legitimate repeat there is.
- The refusal and the "[HARNESS: execution #N]" label now say what is true ("nothing has
  changed since") instead of claiming the call "cannot produce a different answer".
- The system prompt described a guard that refused everything; it now says the guard
  applies only while nothing has changed, that a write/edit clears it, and that after a
  fix the SAME command is the right thing to run - the old wording told the model to
  invent a different command, which proves nothing.
- An infrastructure failure (endpoint unreachable/rejecting) now files a RED Done line,
  per the operator: red means something is actually wrong.
- /stop no longer answers "Nothing is running right now" while a wedged run is still unwinding.
  It keyed on an UNSET cancel flag, and the stall watchdog SETS that flag when it gives up on a
  run, so an operator who typed /stop after a watchdog abandon was told their stop did nothing
  while the channel was still busy. Reported live from the Windows test box ("/stop does not seem to work"):
  the stop was not ignored, the answer was wrong. /restart's "is a task running" guard shared the
  blind spot, so it could have restarted mid-wedge.
- /stop is read on the listener thread, so it lands even while a wedged worker still owns the
  channel, and one _stop_channel now serves both entry points. It is idempotent: it re-sets the
  flags and drains the queue instead of reporting a state.
- An abandoned run no longer leaves the channel marked busy, so the next plain message is not
  steered into a run that will never read it, and /new does not queue behind a written-off task.
  "Busy" now means a run is in flight, not that a flag is unset.
- Suites: 76 / 205 / 195 (test_stall +6, including the flagged-run wording and the cleared busy
  flag). The suite fixtures had drifted out of step with the color renames, so they could not run
  at all before this pass.

## v1.9.31 — amber for tools, red kept for real failures (2026-09-12)

Operator: "amber instead. Red for failures or bad things."

  green  #2ecc71  the model's own narration - what it is about to do
  amber  #f1c40f  a tool call that ran
  white  #ffffff  harness status, periodic check-ins, the Done summary
  red    #e74c3c  ONLY a failure: a tool that exited non-zero, or a run that ended badly

Red is never ordinary activity, so a red bar always means something is actually wrong.
A merged tool batch containing a failed call is red even if the other calls succeeded -
the line has to read as "something in here broke".

- Suites: 76 / 205 / 178.

## v1.9.30 — color-coded progress lines, so the noise is readable rather than bigger (2026-09-12)

Operator: "sometimes it comes in a hard to differentiate block of noise … I like the
visibility, I don't want to increase it, just make it easier to read/differentiate."

Mattermost has no text color, but a post can carry Slack-style attachments, which render
a colored left bar (proved against the live server first, then pinned in the suite):

  green  #2ecc71  the model's own narration - what it is about to do
  red    #e74c3c  a tool call that ran
  white  #ffffff  harness status, periodic check-ins, the Done summary

Command replies and the final answer stay UNBARRED on purpose: after a run of colored
lines, an unbarred post is the signal that this is the payload and not more working noise.

- `_post(..., color=)` / `_edit(..., color=)`; only the first chunk of a chunked post
  is barred, and an edit re-sends the bar so a line edited every couple of seconds keeps it.
- Kill switch `agent.color_coded` (default true) restores pre-1.9.30 plain text exactly.
- Suites: 71 / 205 / 178 (test_checkin +12 checks).

## v1.9.29 — the model's narration streams, so the plan is readable while it is being written (2026-09-12)

Stage 3's remaining half, and the half the operator actually asked for: "I sometimes want to tell it
to stop or provide steering commands ... to avoid waiting an eternity while it goes off and does
something pointless."

- `note()` posts the model's interstitial line (💬) only after the call returns. On a local model
  that is the end of a multi-minute generation, and the tools it announced are already running by
  then - the moment to stop or steer has passed. `narration()` streams the same line into ONE post
  that grows as the model writes it (`checkin_stream_notes`, `checkin_stream_seconds` 2s).
- Kept when the text turns out to be the plan (the turn carries tool calls) and DROPPED when it turns
  out to be the final answer, which the caller posts properly - otherwise the channel shows the
  answer twice. `Dispatcher._delete` exists for that.
- The streamed line is not posted twice: when the narration was already live, `interim_cb` is
  skipped for that turn.
- `_stream_chat` now snapshots the accumulated text at most twice a second (and once more when the
  stream ends, so the post never shows a partial line), and counts characters incrementally instead
  of re-summing the whole answer per delta.
- Works with the existing `progress_updates` and `checkin_notes` switches; turn
  `checkin_stream_notes` off to go back to one lump per call.

## v1.9.28 — model calls stream (2026-09-11)

Stage 3 of the changes plan, and the first item on it that changes what the operator SEES rather
than what the logs say. `/stop` closing the socket is the other half of v1.9.27: that version
stopped the run from ACTING on a reply, this one stops the box GENERATING it.

- `_post_watchdog(..., stream=True)` hands back the response when the HEADERS arrive, so the
  existing wall-clock bound becomes a first-byte bound. Prefill on a long prompt stays covered by
  `request_timeout`; an idle stream is caught separately by `llm.stream_idle_seconds` (120).
- `_stream_chat` reads the body in its own thread and drains it through a queue, because while
  blocked in `iter_lines` neither a cancel nor an idle gap can be noticed. On either it CLOSES the
  response, and that close is the cancel: it drops the connection the server is writing into, so a
  local llama.cpp drops the task. Verified against a socket that reports the peer hang-up.
- Deltas assemble into the same message shape the JSON path returns (content, reasoning_content,
  and tool_calls merged by index, since OpenAI-shaped servers stream tool calls in fragments), so
  usage accounting, the `finish=length` escalation and the clamp check are unchanged.
- A stream that fails (not SSE, torn, idle) marks that URL and retries the SAME endpoint without
  streaming before anything is demoted: a server that cannot stream is still a working model.
- `stream` reaches `requests.post` only when it is True, so every non-streaming test keeps its
  existing fake. `llm.stream` (default true) and `llm.stream_idle_seconds` are the knobs; the
  suites' fixture pins `stream: false` so the old path stays covered too.
- The usage line gains `ttft` for streamed runs, and the log gains one line per streamed call
  (chunks, first-delta latency, server-reported tok/s, wall time).
- `test_ledger`'s pinned known-gap test (`saw_close is False`) is gone as its own comment
  instructed: a streaming cancel DOES hang up now, and a replacement test asserts it.

## v1.9.27 — /stop is immediate, and mid-run corrections reach the model (2026-09-11)

Live on a Linux bot: the operator sent `/stop` at 19:58:29 and "Leave it alone" at
19:58:37, and the run still executed a shell step at 19:58:57 that paused 32 curl processes on
another host. Two separate defects produced that.

- `/stop` set a `cancel_event` that `Agent.run` only checked at the TOP of a turn, so the current
  turn finished: the model call, then every tool call it asked for. There is now a check before a
  tool batch is executed and inside each tool call (a skipped call still gets an answer, because an
  unanswered `tool_call_id` invalidates the next payload on a strict provider), and a stop that
  lands while a model call is in flight aborts the wait. `OperatorStop` derives from
  `BaseException` on purpose: an ordinary `except Exception` on the fallback/retry path must not
  swallow a stop and try the next endpoint.
- Any non-command message while a run was live was QUEUED, and a run holds its channel's worker for
  its whole duration, so the correction was only read after the run it was meant to redirect. Text
  now goes into a per-channel steering list, the run drains it at the top of each turn and again
  before it answers (a reply composed before the correction is a reply to the old instruction), and
  anything the run never read is requeued rather than dropped.
- `/stop` still cannot close the socket of a request already in flight (urllib3 will not), so the
  abandoned call keeps generating server-side. What it no longer does is let that reply act.
- Carried in this same file, and called out here because it is not part of the above: a tool-schema
  trim the operator had this box's bot do earlier, which landed 14:36-14:50 with no version bump and
  no changelog entry of its own (that is what made its provenance look like drift until he confirmed
  it). It shortens the built-in tool descriptions and lets `tool_skill` read one section (or
  a byte range) instead of only the first 4000 characters. Measured with the accounting the
  compaction work uses: tool schemas 2298 -> 2152 estimated tokens (9195 -> 8609 characters) on
  every request. It is in this build because it is in this file, and it is recorded here so nobody
  has to guess where it came from.

## v1.9.26 — a changed .env now survives /restart (2026-09-11)

Splitting one Mattermost bot account into two exposed this: skyteck's `.env` was given a new
`tinycmdr_MM_TOKEN`, the bot was restarted, and it came back on the OLD account.

The spawned replacement inherits the parent's environment, and `_load_env_file` deliberately never
overwrites an inherited value ("real env wins"), so the stale copy in the parent's environment masked
the edited file. `/restart` looked like it had ignored the change.

- `_spawn_replacement()` now strips the keys `.env` owns before starting the child, so the child reads
  the file for itself. Values that genuinely come from the OS (systemd, a container) are still
  inherited for every key `.env` does not define, and the systemd path does not spawn at all.
- `_env_file_keys()` is the helper; `tests/test_ledger.py` asserts a stale inherited token does not
  reach the child and that unrelated variables still do.

## v1.9.25 — /restart hands over to whoever owns the process (2026-09-11)

`/restart` always spawned a detached copy of itself and exited. That is only correct when nothing
else would start the bot: it puts a second process on disk while the first is still alive, and the
instance lock decides which one survives. The casualties were real. On a supervised Windows host the
supervisor's child lost the lock, logged `bot exited 3 (lock held elsewhere)`, and the supervisor sat
out a 300 s backoff while an unsupervised orphan held the bot. Under systemd the same habit produced
two instances, one losing the race for the web port (`web UI port 8788 busy — retrying`).

- `restart_owner()` decides who starts the bot again: `systemd` when `INVOCATION_ID` is set (the unit
  ships `Restart=always`), `supervisor` when `tinycmdr_SUPERVISED=1` is in the environment, `self`
  when neither is true, which is the hand-launched or logon-task case where spawning a replacement is
  still the only way back.
- the systemd and supervised paths exit with `RESTART_EXIT_CODE` (75) instead of spawning, so the
  hand-over is explicit and distinguishable from a crash. The `self` path is unchanged.
- the Windows keep-alive (`tinycmdr-supervise.py`, a local component, not part of this package) sets
  `tinycmdr_SUPERVISED=1` when it starts the bot, relaunches at once on exit 75 without counting a
  failure, and no longer announces a revival for a restart that was asked for.
- v1.9.24 exists only as a version bump of v1.9.23 made on one host while it was being repaired; this
  release supersedes it.

tests: the owner decision and both restart paths (hand over, and spawn when there is no owner) are
asserted in `tests/test_ledger.py`.

## v1.9.23 — a Mattermost call could block forever and silence a channel (2026-09-11)

Symptom: a run goes quiet, the stall notice fires every 8 minutes, and `/stop` does nothing. The
channel stays dead until the bot is restarted.

Cause, from `py-spy dump` on the live process: the run thread was parked in

    _post (tinycmdr.py) -> mattermostautodriver client.post -> httpx.post
      -> httpcore start_tls -> ssl do_handshake

waiting on a TLS handshake that never completed, 30+ minutes in. The Mattermost driver's default
`request_timeout` is `None`, and in httpx `timeout=None` disables the connect, read and write
timeouts — so any remote stall (a dropped edge, a half-dead keep-alive, a server that stops
answering mid-response) blocks that call for ever. Because the run holds the session lock for its
whole duration, every later message in the channel queues behind it in silence, and `/stop` cannot
help: the blocked thread never reaches a cancellation check.

- the driver now gets a 60 s request timeout. httpcore passes the connect timeout to `start_tls`,
  so an unfinished handshake raises after 60 s instead of hanging, the run reports the failure, and
  the lock is released. Normal calls are sub-second, so this only ever fires on a real stall.

## v1.9.22 — a batched tool turn could be rejected outright (2026-09-11)

A task died before it ran: `api.deepseek.com` answered 400 "An assistant message with 'tool_calls'
must be followed by tool messages responding to each 'tool_call_id'".

The model had batched two tool calls into one assistant turn. The loop guard fired on the first of
them, and its advisory nudge was appended as a `user` message INSIDE the per-call loop, so the
sequence became: assistant tool_calls [1, 2] -> tool result 1 -> user nudge -> tool result 2.
Every OpenAI-compatible provider requires a batch's tool results to appear consecutively, so the
whole request was rejected. The local llama.cpp servers do not validate this, which is why the same
shape had been accepted for weeks and only a cloud model ever reported it.

One change for the cause, two so a future variant cannot repeat it:

- the loop-guard nudge is queued and appended AFTER the whole batch, never inside it
- a tool call that produced no result is answered with an explicit tool message, instead of being
  skipped: skipping leaves the same unanswered `tool_call_id` hole
- `_repair_tool_pairing()` runs at the one choke point every payload passes (`_payload`). It moves a
  non-tool message that landed inside a tool block to after the block, answers any `tool_call_id`
  with no result, and logs a warning when it had to change anything, so the call site still gets
  fixed rather than silently papered over

`tests/test_ledger.py` gains five checks: the valid shape, the flagged shape, the repaired payload
(both results kept in order, nudge preserved and moved), the missing-result case, and source pins on
the queueing and the choke point.

## v1.9.21 — the suites are hermetic, and one gap is pinned honestly (2026-09-11)

The suites only passed on a machine that already had a `config.json`, because they imported the
live `tinycmdr.py` and that file refuses to start without one. Since `config.json` is written by
the installer and never shipped, the suites could not verify a fresh unpack: on a clean extraction
`test_stall` failed every startup check with "config.json not found" and `test_ledger` failed one
budget assertion against the code default (24000) instead of a configured value.

- the suites now import a byte-identical copy of `tinycmdr.py` from a temp dir that has a generated
  `config.json` beside it, so they run anywhere: a host, CI, or a stranger's fresh download.
- the fixture points at `127.0.0.1:1`, never a port a real local service might hold: an earlier
  fixture used 8081 and silently reached a live service, which broke two tests that patch
  `requests.post` and expect no endpoint at all.
- when a real install's `config.json` is present the tuning test still asserts THAT file, so the
  fleet's own numbers stay checked on a host.
- `_post_watchdog`'s abandonment is documented rather than papered over: the caller stops waiting
  but the socket stays open, so a trickling llama.cpp keeps generating for an answer nobody reads.
  Closing a `requests.Session` does not help (urllib3 closes only IDLE pooled connections), so a
  real cancel means owning the socket. `test_ledger` now PINS that behaviour, with a comment saying
  to delete the test the day it starts failing.

## v1.9.20 — the external-gateway surface is gone (2026-09-11)

The web UI had grown two jobs: the local chat page (`/`, `/api/chat`, `/api/health`) and a
compatibility surface for an external agent gateway (`/v1/capabilities`, `/v1/models`,
`/v1/chat/completions`, a session-id header and a tool-progress SSE event). That gateway path
was dropped on the fleet, so the second job was dead weight: nothing called it, the
environment variables it documented were never read anywhere, and it kept a foreign protocol
name in the file (it is also why a reader of the code described tinycmdr's architecture
wrong).

- deleted 263 lines: the compat comment block, `_flatten_content`, `_tool_event_args`,
  `_bearer_ok`, `_sse`, `_sse_raw`, `_gateway_chat`, the three `/v1/*` routes, and the
  session-header handling.
- kept: the web UI proper (`/`, `/api/chat`, `/api/health`, manifest, icon) and the shared
  `_json` / `_send` / `_auth_ok` helpers.
- also dropped the name from the two strings a human actually reads: the `skill` tool's
  description and the "no skills installed" hint.
- the three suites never touched the gateway and still pass: 110 / 154 / 37.
- after the cut the file still carries 9 mentions, all provenance notes on why a tool
  mirrors a shape ("execute_code equivalent", "like a cron gateway"). Those never
  reach the model and are the only record of the design, so they stay.


## 2026-09-11 — the Linux half of the install kit

tinycmdr was Windows-only to ship: `install/install-tinycmdr.ps1` and a logon-triggered
scheduled task. The Linux fleet hosts are Ubuntu and the code was already POSIX-aware
(`bash` instead of PowerShell, `start_new_session` + `os.killpg` instead of `taskkill /T`),
so only the plumbing was missing.

- `install/install-tinycmdr.sh` — the same contract as the PowerShell installer (fleet-defaults,
  venv, `config.json`, token into a 600 `.env`, verify), and it **enables a systemd unit**, so
  "comes up at boot" is the unit's job, not a logon trigger's. `--verify-only` / `--force` /
  `--uninstall` / `--no-start` mirror the Windows switches.
- `maintenance/restart-tinycmdr.sh` (the POSIX twin of the `.ps1`) and `launch-tinycmdr.sh`.
- One trap worth keeping: a bare `[ -n "$X" ] && cmd` line under `set -e` aborts the script when
  the test fails, so every conditional in the installer is a real `if`.
- Another: Ubuntu 22.04 has python3 3.10 and pip but **no python3-venv**, so `python3 -m venv`
  dies on ensurepip. The installer apt-installs it (`--no-deps` refuses instead).
- Verified on both hosts: byte-identical `tinycmdr.py` (sha256 44cd21e9…), a real agent turn
  through the local fallback API, `kill -9` → active again in ~12 s, `is-enabled` = enabled.
- Do not park the token on a command line: pass `--token-file`, because the agent side redacts
  or mangles secrets mid-command and argv is readable in `ps`.

Everything below was found and fixed on the the manager box host on 2026-09-10. Each entry
says what broke, what the fix is, and the evidence that it was real — the
"why" is the part worth keeping, because most of these were invisible until the
request payload was dumped and read.

## v1.9.4 — the freeze (bot went deaf, no log, no error)

`tool_shell` / `tool_execute_code` used `subprocess.run(capture_output=True,
timeout=N)`. On timeout that kills the direct child only; a grandchild holding
the pipe keeps `communicate()` blocked forever, so the worker thread wedged
mid-tool: the channel went silent with nothing in the log.

- `run_capture()` writes stdout/stderr to temp files instead of pipes
- `_kill_tree()` reaps the whole tree (`taskkill /F /T`)
- stall guard (`worker_gen`, `active`), hard stop, `/stop` handled on the
  listener thread so it works while a run is stuck, "queued behind a run"
  notice
- evidence: `tmp/repro_pipe_hang.py` froze for 25s and orphaned a pipe holder

## v1.9.5 — loop countermeasures (the run that re-issued the same call 8×)

The model could not see that it had already run a call, and the ledger re-sent
a finished task as a fresh order on every call.

- exact repeats execute at most twice (`agent.loop_dedupe_after: 2`); the third
  returns the cached result with `NOT RE-EXECUTED` instead of running again
- the assistant `tool_calls` turn is kept in the payload (parity with Hermes) —
  before this the payload was tool results attributed to nobody
- ledger hygiene: finished items render `[done, no action]` and their
  instruction text is cut, so a done task stops reading as a standing order
- `llm.max_context_tokens` 180000 → 110000: the old value exceeded the server's
  per-request window (n_ctx 262144 / 2 slots = 131072), so `_force_shrink`
  dropped blocks mid-run and the model re-ran checks it thought it had lost

## v1.9.6 — attack the attempt, not just the repeat

- a repeat's tool output carries `[HARNESS: ... execution #N]`, so the model
  reads "you already ran this" rather than silence
- the loop-guard nudge fires on the **first** repeat (was the third: two wasted
  executions, possibly of a mutating command)
- system prompt states plainly: never re-issue a call you have already made
- bug found while labelling: a **refused** mutating call was still counted as a
  change, so a report could claim a change that never ran

## v1.9.7 / v1.9.8 — one /status, guardrails visible

- `/status` existed twice (Mattermost handler + web UI) and had drifted: the web
  copy silently lacked sampling and notes. Both now call `status_text()`, plain
  text so it renders correctly in both
- `/status` shows the guardrails: `limits: steps 100 · run 35m · stall 8/20m ·
  dedupe 2 · loop-stop 6`

## v1.9.9 — sampling is inherited; payload dump hook

- `agent.debug_dump_dir` writes each request body to disk (off by default, blank
  = disabled). This is the only way to see what the model was actually shown
- sampling is no longer pinned: the model file already carries the intended
  stack (`general.sampling.temp=1.0`, `top_p=0.95`, `top_k=20`)
- `/status` reads the effective values back off the endpoint's `/props`
  (`default_generation_settings.params`, cached 5 min), so inheritance is
  visible, not blind

## v1.9.10 — the "nonsense answers" root cause

Asked to list Docker containers, the bot replied *"Nothing actionable in that
message — looks like channel noise (or a truncated paste; the leading token
reads like a message ID)."* The payload dump showed why:

```
29 user   85 chars  'List the Docker containers that are running...'
30 user 4790 chars  '[context only — live machine state, refreshed for this
                     reply. It is NOT a new request from the operator]...'
```

- the volatile state block was appended **after** the operator's message, so the
  last user turn the model saw was 4.8k chars of live state, and it answered
  that — the bad replies say so verbatim ("the refreshed state just confirms
  everything I've reported still holds")
- the marker text ("this is NOT a request") did not help; position beats
  wording. The block is now inserted **before** the request, so the request
  stays last
- a template that fails to convert its own textual tool call left
  `<tool_call><function=shell>...` in `content`; that was posted to chat as the
  answer AND stored in history. It is now parsed and executed, and the markup is
  stripped either way

## v1.9.11 — sampling can no longer be sent (standing rule)

Sampling must always be inherited: tinycmdr can be pointed at a cloud provider
or a different local model at any time, and a pinned temp/top_p/top_k would
override that endpoint's own stack.

- `apply_sampling()` is a choke point that **strips** sampling keys from the
  body, even ones already on the payload — it never adds any
- stray values left in `config.json` are ignored and reported: `/status` shows
  `IGNORED config: temperature`
- `config.json` and the code defaults carry no sampling keys at all

## v1.9.17 — a broken config.json says so, and the allowlist cannot crash

Both from reading a hand-edited config: `"allowed_users": [<id>...]` with no
quotes is not JSON, so the bot died at import with a JSON traceback instead of a
sentence, and two allowlist shapes were dangerous.

- `parse_config_text()` reports `config.json is not valid JSON: Expecting value
  (line N, column M) - every string in JSON needs double quotes`, and startup
  validation surfaces it instead of a traceback at import.
- `user_is_allowed()` tolerates a null or a bare string. `uid not in None` raises,
  and a single-user *string* turns `sender in allowed` into a substring test - a
  name merely containing the id would have been let through.
- validation refuses an **empty** allowlist: deny-by-default plus nothing allowed
  means the bot answers nobody, which reads as "broken". It now says so.
- the installer no longer writes `"allowed_users": null` (PowerShell renders an
  empty array that way, and that null is what fed the crash above); it writes `[]`.

## v1.9.15 — loopback is a guess, and console glyphs are real UTF-8

Two things a careful reading of the bot-aTOWER transcript showed:

- **the installer silently wrote a loopback model URL.** `-ModelBaseUrl` defaults
  to `http://127.0.0.1:8081/v1`, which is the only neutral default for a package
  that must not ship any one host's address - but on a machine with no local
  model it can never work, and the installer did not say so. It now prints a NOTE
  naming the likely cause, and lists `llm.base_url` among the outstanding items,
  instead of treating a default as configuration. Pass
  `-ModelBaseUrl http://<model-host>:8081/v1` for a model on the network.
- **the console glyphs were mojibake, not just risky.** With the crash fixed, the
  output came out as `prompt overhead Γëê 4267 tokens` - that is the UTF-8 bytes of
  "~=" being read through cp437. The process now switches the console to UTF-8
  (`SetConsoleOutputCP(65001)`) and writes UTF-8 to its streams, so the em dash
  and "~=" render properly in a PowerShell window; `errors="replace"` stays as the
  net for consoles that cannot be switched.

## v1.9.14 — the CLI died on a legacy console code page

The first install on a fresh host got all the way to the verification step and
then reported `INSTALL FAILED: Traceback (most recent call last):`. Two separate
bugs, one behind the other:

- **the app crashed printing its own banner.** A stock PowerShell console uses a
  legacy code page (cp437). Python's stdout follows it, and the em dash in
  `tinycmdr CLI - model ...` has no cp437 byte, so `print()` raised
  `UnicodeEncodeError` and killed the process. Nothing to do with the Python
  version (it was 3.12, the pinned one, and reproduces there): it is the console
  encoding. It only ever hit the *console* path - the bot itself logs to a file
  with explicit UTF-8 and posts JSON to Mattermost, which is why it survived
  here for weeks. Fixed by reconfiguring stdout/stderr with `errors="replace"` at
  import, so an unencodable glyph becomes `?` instead of a traceback.
- **the installer treated that crash as its own failure.** With
  `ErrorActionPreference = Stop`, a native command writing to stderr counts as a
  terminating error, so the probe aborted the install and reported only the
  traceback's first line. Now the preference is relaxed for the probe only, the
  child gets `PYTHONIOENCODING=utf-8`, and the real tail of the output is shown.
  A probe that gets no answer is reported as **installed but not verified**
  (exit 3) rather than as a failed install - the files and the scheduled task are
  in place either way.

Also added `-VerifyOnly`: run the probe against an existing install and stop, no
administrator rights and no reinstall. That is how the probe path was tested end
to end here (live endpoint -> `READY`, exit 0; dead endpoint -> readable reason,
exit 3), and how a host can be re-checked after editing config.json.

## v1.9.13 — shipping to another Windows host (package + installer)

`maintenance/build-package.py` builds `dist/tinycmdr-<v>-win.zip`;
`install/install-tinycmdr.cmd` (double-click) or `install-tinycmdr.ps1` installs it
on a new box. The dry-install test found four things that would have broken every
new host, all fixed here:

- **the code defaults carried this box's identity.** `DEFAULT_CONFIG` named this
  fleet's chat host and this box's model endpoint outright, so a fresh install
  quietly pointed at this server. Both neutral now, and an
  unconfigured host fails fast with "set mattermost.url" instead of dying inside
  the Mattermost driver. The builder now refuses to ship any host value that
  appears in a shipped code file.
- **PowerShell 5.1's `Set-Content -Encoding UTF8` writes a BOM.** The generated
  `config.json` was unparseable, and `web-token.txt` read back one byte long — an
  invisible byte in an HTTP header, i.e. 401s with no visible cause. All writes
  are BOM-less and the loader reads `utf-8-sig`, so a Notepad edit cannot break
  startup either.
- **`ConvertTo-Json` collapses a one-element array.** `allowed_users` came out as
  the *string* `"testuser123"` instead of a list, which would have turned the
  allowlist check into a substring match. Forced back to a list, verified by
  re-reading the file.
- **a BOM-less `.ps1` is read as ANSI by PS 5.1.** A UTF-8 em dash became a smart
  quote, terminated the string, and produced a wall of bogus parse errors.
  Shipped scripts are ASCII-only and the builder gates it (the live
  `restart-tinycmdr.ps1` had the same em dash hiding in it).

**The silent close.** Running the `.ps1` by double-click (or "Run with
PowerShell") printed *"running scripts is disabled on this system"* and the
window closed before it could be read — this machine's execution policy is
Restricted at every scope, and only an explicit `-ExecutionPolicy Bypass` ever
worked. Hence `install-tinycmdr.cmd`: it elevates itself, runs PowerShell with
`Bypass`, transcribes everything to `%TEMP%\tinycmdr-install.log`, and waits
before closing. Elevation is now needed **only** to register the scheduled task,
so `-SkipTask` installs files from a normal shell.

**The local web page is opt-in and off by default.** `web.enabled` is false in
the code defaults and in the shipped template; `-EnableWeb` turns it back on
(loopback, fresh token). It was the wrong thing to lead with: tinycmdr is driven
from Mattermost, and a local check needs no port at all —
`python tinycmdr.py --once "/status"` or `--cli`. The installer verifies a new
install that way now, instead of generating a token for a page nobody opened.

## v1.9.12 — the origin of the unexplained 0.6

The stale `temperature: 0.6` that nobody could account for was a **default in
tinycmdr.py itself** (`"temperature": 0.6`, ~line 105), applied whenever
`config.json` did not override it. Removed.

## Duplicate assistant turns (found via the dump, not a version boundary)

The parity echo that keeps the assistant tool-call turn in the payload
**appended** after the raw reply was already there, so every tool call appeared
in the transcript twice with the same tool-call id (content trimmed). A
duplicated assistant turn is the shape that teaches a model to repeat itself.
It now replaces the raw reply. The old test read `asst[0]` and passed happily
with the duplicate present — the assertion is now the count.

## Measured effect (same prompt: "list the Docker containers, read-only")

```
              runs   steps/run   LLM calls/run   answer latency
before        8      2-12        3-13            34-311s (plus 864s/824s outliers)
after         4      3-6         4-7             58-80s, 4/4 correct
```
The speed gain is **fewer calls per run** (13 → 4), not a faster box — tok/s was
unchanged (12-35 before, 28-32 after). Each LLM call on this box costs ~15-20s of
thinking plus a prefill, so eliminating repeated reads is the whole win. The
runaway tail (12-step loops at 864s) is gone.

Caution recorded on purpose: the fastest run in the set (32s) was the one that
answered "nothing actionable" — it was quick because it skipped the work.

## Tests

271 assertions, all green: `tests/test_stall.py` (124: loop guard, dedupe,
dispatcher, sampling, payload shape, inline tool calls, /status), `tests/test_ledger.py`
(110), `tests/test_checkin.py` (37).

Each fix carries a test that fails on the old behaviour. Two of them were
**pinning the bug**: one asserted `max_context_tokens >= 150000` (the value that
overflowed the server window) and one asserted sampling *was* sent explicitly
(the policy that produced the stale 0.6). Both now assert the invariant.

## Rollback / snapshots

- `snapshots/tinycmdr.py.v1.9.3-rollback.bak` — before today's work
- `snapshots/tinycmdr.py.v1.9.12.bak` — current

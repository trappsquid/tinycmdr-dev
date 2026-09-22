# Corrections staged for after the the Windows test box evaluation (2026-09-22)

Nothing here is implemented while the operator evaluates 1.0.0 on the Windows test box ("we will hold on
more code improvements until I can test the Windows test box"). Each entry says what it is, why it is
deferred, and what it costs, so the next session can pick one up without re-deriving anything.

Order is the operator's interest, not severity.

## 1. `tinycmdr model add` - an endpoint entry is added by a command, never by hand  (NEW)

**His request, verbatim:** "I tried to manually add another LLM endpoint editing the config.json and
realized it was stupid not to have a feature like heremes 'Hermes model' to add an entry via
interactive CLI instead of users messing up python syntax."

**What happened:** the hand edit left `config.json` invalid (JSON, "Expecting ',' delimiter",
line 23), the supervisor relaunched the bot 27 times between 23:10 and 23:22 on 2026-09-21, every
start died with exit 2, and the harness's own message named the exact line - which is the only
reason this was a 12-minute fault instead of a mystery.

**The shape:** grow the D1 verb surface where it already lives (`tinycmdr model ...`):

```
tinycmdr model                        list what this install can route to (today)
tinycmdr model add <alias>            prompt for base_url, model id, api_key_env, timeout; append
                                     to llm.fallbacks (or write the primary with --primary)
tinycmdr model remove <alias>         drop an entry, after showing what it is
tinycmdr model edit <alias> <field>   change one field
```

Rules the entry has to obey, from the code that already exists: a hosted endpoint is a FALLBACK
with `api_key_env` and never the primary (a cloud primary runs with `Bearer none`); the key itself
goes to `.env`, never into config.json; every write goes through `atomic_write_text` and
`set_global_model`'s validation; and a candidate config is `json.loads`-ed BEFORE anything is
written, so the command cannot leave the file broken. Show the diff and ask before writing.

**Cost:** one verb group (the verbs sit in the region the console build CUTS, so the console build
stays byte-identical and a console session keeps its own slash commands), prompting that works
without a tty, and a suite next to `tests/test_verbs.py`. The natural moment is with the next
release batch.

**Worth considering beside it:** when `load_config()` meets a config that does not parse it is
already fatal with the line named. A `--repair`/hint path that prints the failing fragment and
points at `tinycmdr model`/`config.example.json` would have turned his 12 minutes into one message.

## 2. F6: probe a FALLBACK endpoint's window on failover  (from the audit, deferred)

`_endpoint_window()` asks the endpoint the install is configured for, once per `WINDOW_TTL`. When a
fallback answers instead, its own window is unknown, so the budget comes from the primary's number.
Cold today (the fleet's `allow_cloud_fallback` is a per-host flag and the local box is the primary),
which is why it was deferred. Cost: one window probe per switch, cached like the primary's.

## 3. F5: run the live tokenizer comparison once  (from the audit, never run)

`tests/test_tokens.py` has an opt-in leg (`TINYCMDR_TEST_TOKENIZE_URL`) that compares `est_tokens`
against a real tokenizer. It has never been pointed at a box, so the content-aware divisors are
verified only against each other. Cost: one env var and a metadata `POST /tokenize` to a LAN box -
do it when nothing is benchmarking there.

## 4. F13: the lane-parity enforcement test  (optional, no defect today)

No lane touches `AGENT.run` outside `drive_run()` and no drift was found by measurement, so this is
a guard against future drift rather than a fix: a suite that fails when a lane grows its own run
logic. Costs a new suite; the operator has twice said he prefers few tests over many.

## 5. The reconnect tracebacks (third-party, measured, left alone)

`mattermostautodriver` 2.3.0 raises on the server's CLOSED frame instead of breaking its loop, so
every idle-socket close costs a 5-6 s blind window: 277 + 171 `WSMessageTypeError` lines across our
logs, against 2 messages the 60 s catch-up sweep ever recovered. Fixing it means vendoring a
patched driver: the 11.x line wants `httpx~=0.28.1` and `mmpy_bot` 2.2.1 pins `httpx<0.28`, so it
cannot simply be upgraded. Revisit only if a real message is lost.

## 6. Cosmetic, known, not worth a batch on their own

- a push writes `<file>.bak-push<version>-<date>-<stamp>`: the date appears twice because the
  tag already carries it. Harmless, and the files are deleted by the cleanup below.
- the ledger's `lost items between reads: 1 -> 0` WARNING appears in one suite's output on
  purpose: that test shrinks a ledger to prove `ledger_check()` shouts.
- `C:leetbot` on the Windows test box AND the other Windows box, and `~/tinycmdr` on the manager box, are empty and refuse
  deletion while a session holds a handle on them; a logoff or reboot releases them.
  `MoveFileEx(..., MOVEFILE_DELAY_UNTIL_REBOOT)` returned false, so it is a manual step, not
  something to script again.

## 7. Host-level, waiting on the operator (not code)

- the LAN model box: `llm.model` is `cloud`, so its default route is DeepSeek and not the LAN box. Deliberate
  for the box that benchmarks the LAN model, or leftover?
- the manager box: `tinycmdr doctor` warns that `llm.api_key` is set in `config.json`; `.env` is the home.
- the Windows test box: the evaluation box. It rejoins the fleet build only on his word.

## 8. Left over from the 2026-09-22 rename, waiting on a decision

The tree is renamed and clean; these are the pieces a rename cannot finish on its own.

- **the manager box's scheduled task still names the launcher the rename deleted** (`tinycmdr-service.vbs`), so
  that bot cannot respawn until one ELEVATED command re-points it at `tinycmdr-service.vbs`, or
  until the manager box itself is migrated. Unelevated, `Set-ScheduledTask` and `schtasks /Change` both answer
  `Access is denied`. the manager box's bot runs the pre-rename build from memory until then and holds
  `tinycmdr.log`, `logs/` and two locks, which cannot be deleted while it runs.
- **Five hosts are not migrated** (.13, .47, .9, the MacBook, the manager box): folders, units, tasks and plists
  still carry the pre-rename name, and their bots are RUNNING that build. Their dead leftovers are
  gone; the live installs were left up deliberately. the Windows test box is the one box on the new names.
- **`dist/` keeps 46 pre-1.0.0 shapes** whose payload is the old-named build. Archive bytes can only
  be deleted, not scrubbed, and deleting them needs the operator's word.
- **The Z: share (VPS Admin) still holds the pre-rename 1.0.0 artifact set, its sidecars and the
  pre-rename state docs.** Restaging that share is his call; nothing there was touched.
- **The published blog slug is `/posts/tinycmdr/`** (and `/posts/tinycmdr-cli/`). A live URL is a
  link-break, not a remnant, so it stays until he says otherwise.
- **GitHub**: the repo rename is his. The docs now read `github.com/tinycmdr/tinycmdr` - confirm the
  org/repo slug and the URLs are corrected in one pass.
- **the Windows test box carries the fresh install** (task `Tinycmdr`, `C:	inycmdr`, token + search keys + its
  own model key in `.env`, model `main` at the LAN box). Two things he may want changed: its
  `agent.bot_name` is the machine name (`bot-atower`) against the account `@the Windows test box`, and the README
  could say the public zip asks every install question (it ships no `fleet-defaults.json`).
## Where the fleet cleanup rules live

`~/hermes-tmp/fleet-push/clean-install.sh` (Unix) and `clean-install.ps1` (Windows) are the tidiers
used on 2026-09-22: backups, pre-rename `tinycmdr*` artifacts, repo tooling copies, `tests/`,
`docs/`, `snapshots/`, `__pycache__/`, and (Unix) everything in `maintenance/` except the restart
helper the verb calls. They deliberately keep state (`sessions/`, `notes.md`, `tasks.json`,
`config.json`, `.env`), the operator's own artifacts (`reports/`, `platform-tools/`, `uploads/`,
his benchmark logs) and host tooling. The lesson is in the `fleet-access` skill.

# How tinycmdr is developed

For whoever is at the keyboard next: another model, another harness, another person, a reviewer
with a fresh clone. Nothing here asks to be trusted - every claim names the command that decides
it, and the ones that matter run automatically (the pre-push hook, the gate, CI).

Read this with `python3 maintenance/where.py` open beside it. That command, not this page, is the
answer to "what is this box".

---

## 1. The record is computed, never remembered

| question | the thing that decides it | command |
| :--- | :--- | :--- |
| Which trees are on this box, and what are they? | `maintenance/where.py` `ROLES` + this box's `maintenance/where-roles.json` | `python3 maintenance/where.py` |
| Is the tree the bot runs from clean, current, and the one the bot actually started from? | same, `--check` (exit 1 on a violation) | `python3 maintenance/where.py --check` |
| What does GitHub have *now* (not what this clone last fetched)? | `git ls-remote` + `gh release list` | `python3 maintenance/where.py --remote` |
| Is the tree I am looking at **released**? | `where.py`'s ORIGIN block: how far HEAD is past the newest tag | `python3 maintenance/where.py` |
| What shipped, and what is still open? | `STATUS.json`, anchored to commits/tags/files | `python3 tests/test_status.py` |
| Does the code pass? | the suites | `venv/bin/python tests/run_all.py` |
| Does the published package install and run? | the install jobs: build, install from the archive, `doctor`/`health`/`--once` | `bash maintenance/smoke-install.sh` |
| Is the published prose stale? | the measured blocks regenerated from the tree | `python3 maintenance/measured-block.py` |
| Would this push leak something private? | `maintenance/leak-gate.py` over the tree, the commits a push would add, or every reachable blob | `python3 maintenance/leak-gate.py --history` |
| What is the long-form history? | `CHANGELOG.md`, one section per release | - |

Two rules follow from that table, and they are the whole point of this page:

1. **If a fact is not in one of those places, it does not exist.** Notes, handoffs and chat
   transcripts are evidence, not authority - the project has already been burned by a hand-written
   map that went two releases stale and named a deleted tree.
2. **Do not answer "what is live / what is dev / what changed" from memory.** Run the command.

## 2. The trees

Two shapes are supported, and which one a box uses is *declared*, not guessed:

**A. one tree** (`same_as`) - the install the bot runs from is also where code work happens. It is
the shape a single-operator box usually wants, and it is the one this checkout declares in
`maintenance/where-roles.json`:

```json
[{"role": "dev", "same_as": "live",
  "why": "one tree: code work happens in the install, on a topic branch"}]
```

**B. live + dev** (the shipped default in `maintenance/where.py` `ROLES`). `~/tinycmdr` is the
install and is never edited by hand; `~/tinycmdr-dev` is the release line. This is the shape that
survives "I edited production and forgot", and a box that has both keeps them.

Either way the invariant is the same: **the tree the bot runs from must carry no uncommitted change
to a tracked file when you push or pull** - that is the state that blocks a `git pull` when
the remote tree is ahead. `where.py --check` fails on it, and
`maintenance/pre-push.sh` runs that check.

On a one-tree box, `git status` being dirty is normal and expected while you work. `--check`
reports it anyway, on purpose. Commit or stash before pushing.

## 3. The flow

```bash
cd ~/tinycmdr
python3 maintenance/where.py              # 1. what is this box, before touching anything
git switch -c <topic>                     # 2. one change, one branch
# ...edit...
bash maintenance/batch.sh -m "scope: what it does"   # 3. numbers + gate (-j4) + commit + push
```

- **The gate** is `tests/run_all.py` and nothing else. A suite that cannot run exits `77` and
  counts as **red** - a machine that graded nothing cannot report success. `--select 'tests/test_*x*'`
  narrows a run while you work on one suite.
- **Landing a batch is one verb**: `bash maintenance/batch.sh -m "<scope: what it does>"` renders the
  published numbers, runs the whole gate at `--jobs 4` (each suite keeps its own temp dir and process
  group; the tree-write report gives up per-suite names, prints one union line and says so), commits
  and pushes. Repeat `-m` for the body. Five hand steps it replaced: render, chase the prose numbers,
  gate, commit, push.
- **The artifact is graded too.** `bash maintenance/smoke-install.sh` builds the public
  package, installs from the archive it produces (not the working tree) and runs
  `doctor`/`health`/`--once` against it; CI runs the same on macOS, Linux and Windows
  (the `install` and `install-windows` jobs). Every other installer check installs from a
  tree its own suite assembles, so this is the only one that touches what other hosts
  actually download. It uses a stub model by default; point `TINYCMDR_SMOKE_BASE_URL` at a
  real endpoint to drive the same turn against a model.
- **The pre-push hook** is not tracked by git; install it once per clone:
  ```bash
  printf '#!/bin/sh\nexec bash "$(git rev-parse --show-toplevel)/maintenance/pre-push.sh"\n' \
      > .git/hooks/pre-push && chmod +x .git/hooks/pre-push
  ```
- **Commit subjects state the change, they do not tell the story.** `<area>: <what changed>` - no
  anecdotes, no "and the ledger says so", no version story. The body carries the reason when it is
  not obvious.
- **A release** is cut from a clean, gated `main`: `bash maintenance/release.sh <notes-file>`.
  It needs `gh` authenticated and `maintenance/private_rules.py` present, bumps nothing itself
  (version, CHANGELOG and README are part of the change), pushes, tags, attaches the assets and
  proves them. A published number is never rebuilt. Those assets - not this working tree - are what
  every other host installs from, so a change that is not released is a change no box but this one
  has; the tags are the claim about what shipped, which is why `tests/test_status.py` grades them.
- **The bot takes a change only on restart**: `bash maintenance/restart-tinycmdr-macos.sh`
  (or the platform's script in `maintenance/`). Editing a file under a running agent changes
  nothing until then - and if the tree *is* the install, that restart is what puts an unreleased
  edit into production, so restart from a commit you have gated.

## 4. What is deliberately NOT on GitHub

The install keeps its state in plain files beside the code. Those files are **per-host** and
gitignored; their shipped defaults live in the tree. A reviewer should expect them to be missing
from a clone, and should not "restore" them:

| not in git | what it is | the shipped default |
| :--- | :--- | :--- |
| `.env`, `config.json` | tokens, endpoint, this box's identity | `.env.example`, `config.example.json` |
| `sessions/`, `logs/`, `spill/`, `tinycmdr.log`, `state.json`, `jobs.json`, `tinycmdr.lock` | conversation and runtime state | - |
| `memory/`, `notes.md`, `field-notes.md`, `atlas.md`, `experiments.jsonl`, `web-sessions.json` | this box's working memory (`memory/` is the OKF bundle; `notes.md` is the legacy file it reads and no longer writes) | created on the host (`tests/fixture-field-notes.md` is what the digest suite stages) |
| `tools/`, `skills/` | drop-in tools and prose skills built on this host | `tools/` starter files, `skills/README.md` |
| `maintenance/private_rules.py` | private leak patterns | `private_rules.example.py` |
| `maintenance/where-roles.json` | this box's tree declaration | `ROLES` in `maintenance/where.py` |
| `venv/`, `dist/` | the private environment, and built archives | built by `maintenance/build-package.py` |

`maintenance/build-package.py` refuses to ship credentials, config, logs, session history or
notes - a leak there is a leak onto every host. `soul.md` is the exception on that list: it is
tracked, because it is the seed the agent's workspace starts from.

That seed is also the one shipped file an operator is expected to EDIT, so a persona is an
edit to a shipped file and is treated as one: `tinycmdr update` copies it aside
(`soul.md.bak-update-<stamp>`) and then **never overwrites it** - `_apply_package` compares it
with the shipped seed and leaves anything else exactly as it is. `tinycmdr doctor` says whether
the file is edited or still the shipped seed. Editing it is always safe: `update` installs a
release package, so nothing merges over it. Anything that discards local changes outside
tinycmdr - `git reset --hard`, `git checkout -- .` in a tree that happens to be a checkout -
still takes the persona with it, which is why the copy exists. The shipped default also lives in
the build as `DEFAULT_SOUL`, so a host with no `soul.md` at all still runs a real persona.

## 5. Secrets and privilege

- Credentials live in `.env` on the host, mode 600: the chat token (`TINYCMDR_MM_TOKEN`), the sudo
  password (`SUDO_PASSWORD`), per-bot keys. The names the app writes are the names it reads
  (`tests/test_host_surface.py`); values never print, and anything named `*PASSWORD` or `*PASSWD` of 6+
  characters is scrubbed from tool output.
- Privilege is the host's decision and the repository carries none of it: an install may be given a
  scoped `NOPASSWD` grant for the read-only verbs it needs (service queries, logs, power state).
  Anything that LOADS a unit, plist or scheduled task is deliberately outside that family - loading
  one is full escalation - and anything outside the grant takes the operator's password. Same rule
  as everything else here: it lives in the host's own file, never in a tracked one.
- Never put a host path, host name, token or private domain in a tracked file. That is what
  `maintenance/leak-gate.py` is for, and it is the reason `where-roles.json` is gitignored.

## 6. Before you add anything

- Reuse the existing machinery: `where.py` for state, `STATUS.json` for open work, `run_all.py` for
  proof, `release.sh` for shipping. A second way to do any of those is the bad habit this page
  exists to prevent.
- A claim in prose gets an anchor git can check, or it does not go in `STATUS.json`.
- Numbers that appear in `docs/tinycmdr-what-it-is.md` are rendered from the tree
  (`maintenance/measured-block.py`) - never hand-edit inside a `measured:` block.
- Keep the gate green *and* keep `maintenance/check-tree-clean.py` honest: a suite writes to its own
  temp dir, never into the checkout.

## 7. Cutting a release (the batch)

`release.sh` ships; it does not decide. A release is **four files, one order, and three gates** — and
the order is not a style, it is what makes each gate see a true claim.

**Which repository things live in** (this is the part that bites):

| what | where | why |
|---|---|---|
| the batch, the tags the ledger is checked against, the ledger itself | `trappsquid/tinycmdr-dev` (this tree) | history and the record |
| the RELEASE object + its assets, and the generated product tree | `trappsquid/tinycmdr` (the install surface) | every installer and updater in the wild fetches `releases/latest/download/...` from there |

`gh` resolves a repository from the tree it runs in, so `release.sh` sets `GH_REPO` to the install
surface and pins the CI wait to this tree (`--repo "$TREE_REPO"`). `tests/test_contracts.py` fails
if the repo a release goes to is not the repo the update path and both installers download from —
a release published into the source repo is a release nobody can fetch.

### The batch (one commit)

1. **`tinycmdr.py`** - `VERSION = "1.0.4N"`. Byte-exact replacement: the working tree is CRLF, so a
   `sed` anchored with `$` silently misses.
2. **`CHANGELOG.md`** - fold `## [Unreleased]` into `## [1.0.4N] - <date>`: one physical line per
   entry under `Added`/`Changed`/`Fixed`, each carrying the mechanism and the test - no summary
   paragraph, no operator quotes, no dates or measurement stories. Leave an empty `## [Unreleased]`
   heading at the top.
3. **`STATUS.json`** - one item per change, **`"state": "unreleased"` with
   `"anchor": {"commit": "<sha>", "expect": "untagged"}`**. That is the only shape the gate accepts
   before the tag exists: `tests/test_status.py` refuses a `shipped` item whose anchor is not in a
   tag AND refuses an `unreleased` one whose commit IS, so "no `expect` at all" is not available
   any more. `release.sh` runs `maintenance/ledger-tag.py`, which promotes the item to
   `shipped` + `expect: tagged` **after** the tag exists.
4. **`docs/tinycmdr-what-it-is.md`** - `python3 maintenance/measured-block.py --write`; it renders the
   blocks AND the prose numbers the gate grades, so nothing is left to fix by hand. `pre-push` refuses
   a stale block, so this is not optional even for a one-line change.
5. **Then**: `bash maintenance/batch.sh -m "<scope: what it does>"` - the ONE verb: renders the numbers
   (step 4 again, for free), runs the whole gate at `--jobs 4` (green, `0 skipped`; each suite keeps its
   own temp dir and process group, giving up only the tree-write report's per-suite names - one union
   line, and it says so), commits the batch with the message, and pushes. Subject <= 50 chars; repeat
   `-m` for the body (the why, the measurement, the test). It replaces five hand steps per batch
   (render, the prose numbers, gate, commit, push), each one skippable and each one skipped at least
   once.

**A fix does not wait for a release, and a release is not grown by fixes.** Fixes land on `main` as
ordinary commits; when a version is due, the batch above ships whatever `main` holds - it is never
held open to gather more work, and its notes name the user-visible changes, not the work that
produced them.

### The cut

```bash
bash maintenance/release.sh ~/tinycmdr-notes-<version>.md
```

The notes file becomes the release body **verbatim** (title exactly `v<version>`), so write it
fresh, factual, one sentence per item, no process narration, no audit or session name, no finding
id. `release.sh` then runs, in this order (each step exists because skipping it broke a release):

1. the leak gate over the tree **and** over the range it is about to push; the dirty-tree check;
   a refusal if the tag already exists;
2. build every published shape, check the README's download names against `dist/`, check the
   archives against the page's assets, write `SHA256SUMS`;
3. push `main` (this tree) and **regenerate and push the product tree's `main` in the same breath**
   (`publish-product.py --write ../tinycmdr-product --push`, then the force-push it prints) - its
   workflow runs on a push to its `main`, and the two gates are independent: different repositories,
   the same tree. Their WAITS then overlap, which is what a cut's wall clock is made of (before this
   it was ~30 minutes of waiting for two runs that never depended on each other; measured 2026-10-08);
4. **wait for this repo's `gate` on that sha** - a release flows from green CI, and a published
   number is never rebuilt. The surface's TAG is not pushed yet: it follows this tree's;
5. create the tag **locally**, run `ledger-tag.py` to promote the items, commit the promotion, and
   only then push the tag and `main` together - the pre-push hook grades the WORKING TREE, so an
   item still saying `unreleased` while a local tag carries its commit blocks the tag's own push;
6. push the surface's tag, then **wait for the install surface's `tests` on the commit pushed in
   step 3** - the workflow a stranger sees, which nothing used to consult. That run has been going
   since step 3, so this wait is normally already over;
7. `gh release create --verify-tag` on the install surface, upload the stable aliases, read the
   assets back, check the README names against the published release, fetch tags;
8. `tests/test_status.py` - the record and the tag must agree before the script says `published`.

Two deliberate overrides exist, and each says so in the notes: `TINYCMDR_SKIP_CI_GATE=1` (this
repo's gate) and `TINYCMDR_SKIP_PRODUCT_CI=1` (the surface's tests).

**A cut that dies after the tag is finished by name, not by memory**:
`bash maintenance/finish-release.sh [<notes-file>] [--dry-run]` - the release body comes from the
version's own CHANGELOG section when no file is given. `release.sh` refuses to re-enter once its tag
exists (a published number is never rebuilt), so this is the tail on its own: build every shape,
check it, attach it to the release on the install surface, read the result back. `--dry-run` stops
before the two `gh release` calls.

**Publishing the product tree alone** is a normal operation and needs no "ship it" - it is what a
reader clones; only cutting a release number does. It is the way a workflow fix, a README fix or a
product-suite fix reaches the surface without a release.

**The Windows tier is one declared file**: `tests/windows-tier.json`, read by
`tests/run_all.py --tier <must|scheduled|windows>` and named by both workflows instead of a
hand-kept list in each. `must` is graded on Windows in the dev CI on every push, `scheduled` is the
rest of what can run there (nightly, and on demand), `excluded` is red there and carries its reason,
`not_applicable` cannot run there at all, and `dev_only` marks the suites the product does not ship
(the surface reads the same file and skips them out loud). `tests/test_contracts.py` fails if a
suite in the tree is not declared exactly once, if an exclusion carries no reason, or if
`STATUS.json`'s `windows-tier-on-the-install-surface` item disagrees; the runner refuses a pattern
that matches no suite. Two lists drifting is what put a Windows-only crash in a suite the surface
ran and the dev line never did (measured 2026-10-08, tests/test_lane_choice.py).

**The archives are not byte-reproducible across builds** (zip metadata differs; `install.sh` and
the other plain files are identical). So a re-download is checked against the release's own
`SHA256SUMS`, never against a local rebuild - what pins the CONTENTS is `check-package-assets.py`,
which compares every asset inside the archive with the tree byte for byte. Measured 2026-10-08: the
same tree rebuilt gave different archive sums and identical file sums.

**What a push costs, measured 2026-10-08** (so the next person shortens the right thing): the
suite run IS the job - 391s of a 399s ubuntu job, 15.1 min for the surface's 85-suite Windows job,
6.2 min for the dev tier's 24 - which is why every job now passes `--jobs` (4 on ubuntu, 3 on
windows and macOS: 8.1 min -> ~2.5, 15.1 -> ~6). Everything else is noise: `checkout` 1s (a
`fetch-depth: 0` clone is not the problem), `setup-python` + pip 8s, the leak job 0.2 min, the two
`install from the built package` jobs 0.6-0.8 min each, and `maintenance/pre-push.sh` 4.4s locally.
**Pushing twice while a run is in flight is the expensive habit**: the workflows are per-ref
`cancel-in-progress`, so the second push kills the first run's remaining jobs - and a cancelled job
marks the whole RUN failed, which is what `release.sh`'s surface wait refuses on. Measured: nine
pushes in three hours, every earlier run's macOS job cancelled at its 15th minute, macOS never
graded at all after 13:55, and runs reading `failure` for jobs that never finished. `pre-push.sh`
now warns when a run for the branch is still going; batch the next change instead.

**After a cut, verify from outside the repo**: download the published `SHA256SUMS` and one archive
and check the sum; `releases/latest/download/install.sh` returns 200; `gh run list` shows the gate
green on the tagged commit and the surface's tests green on the pushed tree. `release.sh` also
grades the built archives before publishing: `check-readme-assets.py` (every README download name)
and `check-package-assets.py` (**every asset the page's routes serve**, byte-identical to the tree -
the derivation lives once, in `maintenance/package_assets.py`, because 1.0.68-1.0.70 shipped without
`assets/webui.css` at all).

**The rule for a reported bug.** It lands as the fix PLUS the invariant that grades its class -
and the invariant lives where the class lives: a must-agree pair in `tests/test_contracts.py`, an
artifact surface in `maintenance/check-package-*.py` (both run by `release.sh`), a page behaviour in
`tests/test_webui_page.py`. Naming the must-agree a bug violated, and where that agreement is
checked, is part of calling it fixed. The classes this rule came from: assets that
never shipped (three hand lists), a router prefix swallowing a newer route, a page harness that
fabricated ids the page no longer used and missed ids it did, a doc route table naming a deleted
file, and installers writing `.env` keys nothing read.

Traps this project has actually paid for:

- **Do not edit the tree while a gate run is in progress.** The runner's report attributes your
  write to whichever suite was running, and a half-written `STATUS.json` makes `test_status` read
  garbage.
- **A push is refused if any tracked file is dirty** in the tree declared `live` - including a doc you
  edited after the last commit. The hook is right; commit it.
- **The leak gate reads every tracked file.** A chat host, a LAN address or a bot account name in a
  ledger detail is refused before it can reach the remote.
- **`git add -A` in a live tree takes host state** unless `.gitignore` covers it (that is how
  `*.log.*` got there).
- **The tag is created on the remote; `release.sh` fetches it back, and §1's
  `N commit(s) past <tag> (UNRELEASED)` line is what tells you afterwards whether the cut landed.**
- **Everything I write is the fewest words that carry the fact.** A commit subject is <= 50
  characters, `scope: what it does`; a body carries the *why*, the measurement or the test that pins
  it - not an inventory of steps and not a restatement of the diff. A `CHANGELOG.md` entry is one
  sentence on one line; `STATUS.json` details are one sentence; the release notes carry one sentence
  per item. The instruction a rule gives is this file's business; the incident behind it is not.

### The leak gate has to be armed

`maintenance/leak-gate.py` grades three surfaces: the working tree, everything a push adds, and
(`--history`) every reachable commit and blob. Three places publish from this tree and each one
runs it, so arm the clone once and let the others follow:

```bash
bash maintenance/install-hooks.sh              # this clone: the full pre-push set
bash maintenance/install-hooks.sh --leak-only  # just the gate (python3 + stdlib, no venv)
```

`tinycmdr doctor` prints `leak gate : armed` or `NOT ARMED - run maintenance/install-hooks.sh`.
CI runs the same gate over exactly what a push or a PR adds (the `leak` job), and `release.sh`
scans the range it is about to push. The patterns come from this host's private inventory
(`maintenance/private_rules.py`, gitignored) or, on a runner, from the repository secret
`TINYCMDR_LEAK_PATTERNS`; with neither, the job grades the shipped example and says so in its log.

## Looking at the page

The web page is my own design (`assets/webui.css`, the backdrop photo
(`assets/roman-temple-spring.jpg`, the replacement for the drawn
colonnade), the bundled fonts, the icons inlined into the markup). To look at the tree's own page WITHOUT touching
the running service, serve a scratch copy - the service on 8790 belongs to the install:

```bash
rm -rf /tmp/preview && mkdir -p /tmp/preview/tinycmdr
cp tinycmdr.py /tmp/preview/tinycmdr/
cp -R assets /tmp/preview/tinycmdr/            # webui.css, fonts, the backdrop photo, the mascot
cp ~/tinycmdr/theme.toml /tmp/preview/tinycmdr/ 2>/dev/null || true
python3 - <<'EOF'
import json, pathlib
pathlib.Path("/tmp/preview/tinycmdr/config.json").write_text(json.dumps(
    {"llm": {"base_url": "http://127.0.0.1:9/v1", "model": "probe"},
     "web": {"enabled": True, "host": "127.0.0.1", "port": 8791, "token": "preview"}}))
EOF
cd /tmp/preview/tinycmdr && TINYCMDR_NO_BROWSER=1 "$HOME/tinycmdr-dev/venv/bin/python" tinycmdr.py --web --no-browser
# then open http://127.0.0.1:8791/#token=preview
```

Never let a check open a browser for the operator (`TINYCMDR_NO_BROWSER=1` is the opt-out
`_browser_possible()` honours; every suite sets it). Hard-reload (⌘⇧R) after an art or
stylesheet change: `/icon.png` is cached for a day, and the tab keeps the old favicon even
after the page is fixed.

What is served, and where it comes from:

| route | source |
| :--- | :--- |
| `/` | `WEB_PAGE` in tinycmdr.py, with `{{VERSION}}`, `{{THEME_COLOR}}`, `{{EMPTY_ART}}` and the backdrop element substituted |
| `/page.css` | `assets/webui.css`, with `{{THEME}}` = the host's theme roles (one theme.toml decides the terminal and the page) |
| `/temple.jpg` | `assets/roman-temple-spring.jpg`, the stage's backdrop photo (drawn by `.colonnade`, dimmed and feathered) |
| `/fonts/*.woff2` | the four bundled OFL faces (Cinzel 600/700, Inter variable, JetBrains Mono) |
| `/manifest.webmanifest` | built in tinycmdr.py (`_web_manifest()`), the PWA manifest (icons versioned like the art) |
| `/chibi.png`, `/mark.png`, `/icon.png` | `assets/page-*.png` when the host ships them, else the built-in badge |

**Look at an INSTALL, not only at the tree.** Every page defect that has reached a user so far was
invisible from the source tree, where all the files exist: 1.0.68-1.0.70 shipped without
`assets/webui.css` (the page rendered as raw unstyled markup), and the installers copied a hand
list that had never gained `assets/`, so even a correct package produced a fresh install with no
stylesheet at all - both found by looking at a real install (2026-10-04). The
installer suite now drives a real install into a temp dir and asserts the installed tree carries
every asset the routes serve; for the eyes, point the scratch recipe above at an INSTALLED copy
(then hard-reload: art and CSS are cached).

A missing asset is a supported state, not an error: no chibi falls back to the mark, no
photo draws no backdrop, and the page never logs a 404 for art it does not have.

## The agent-protocol surfaces (A2UI, A2A, MCP)

Three protocol surfaces, all built to a rent rule: **a feature that is off
registers nothing**, so a box that never sets it has a byte-identical payload.

**A2UI** — the page is a surface the agent can draw on. The hidden `render_ui` tool
emits a standard A2UI v1.0 `createSurface` envelope against the catalog the page
declares (`Card`, `Column`, `Row`, `Text`, `Divider`; `{"path": ...}` bindings resolve
against `createSurface.dataModel`); `a2ui_validate()` refuses anything else by name.
The payload rides the transcript line (`WebRun.add(kind, text, **extra)` -> the page's
`a2uiRender`) and NEVER the prompt: the model sees the one-line summary. A lane with no
surface answers honestly. Tests: `tests/test_a2_surface.py` covers the door and the
envelope and caps, and the page suite's shim the renderer.

**A2A** — the mesh door, off by default (`web.a2a`):
- `GET /.well-known/agent-card.json` is public metadata (no token) once enabled;
- `POST /a2a` speaks the v1.0 JSON-RPC binding with the page token as a `Bearer`
  (`_auth_ok` accepts it): `SendMessage` runs one message through this box and returns a
  `Task` (`TASK_STATE_COMPLETED`, or `TASK_STATE_FAILED` when the endpoint never
  answered), `GetTask`/`ListTasks` read the in-memory task ring (`A2A_MAX_TASKS`), and
  `CancelTask`/streaming/push answer the spec's own errors (-32002/-32004). A
  client-supplied `taskId` is the idempotency key: the id is stored as a
  `TASK_STATE_WORKING` placeholder before the run starts (so `GetTask` answers while the
  message is in flight), and any later `SendMessage` with that id returns the stored task
  without running the message again - the retry a timed-out peer sends is safe.
- The client is a hidden `a2a` tool (list/card/send) that is registered **only** when
  `agent.a2a_remotes` is non-empty - the A2A check lives in `tests/test_a2_surface.py`.

Verify by hand: set `web.a2a` true, restart, then
`curl http://127.0.0.1:8790/.well-known/agent-card.json`, and a `SendMessage` with
`-H 'Authorization: Bearer <TINYCMDR_WEB_TOKEN>'`.

## 8. Starting from nothing

A fresh clone, or a new model told only "work on tinycmdr here":

```bash
python3.12 -m venv venv && venv/bin/pip install -r requirements.txt -r requirements-test.txt
python3 maintenance/where.py --remote      # what is this box, and what has GitHub got
venv/bin/python tests/run_all.py --jobs 4  # the gate, in parallel (~100 s; §7 lands a batch)
python3 tests/test_status.py               # what is already known-open
```

That is the whole onboarding. If those four disagree with any other document you were handed,
they are right and the document is stale.

One honest wrinkle, verified by cloning to a fresh directory on a machine that already has an
install: `where.py` declares roles for the box it runs on, so the shipped table expects `~/tinycmdr`
(and `~/tinycmdr-dev` beside it). A clone somewhere else shows those roles as MISSING - that is the
tool being truthful about a layout it does not recognise, not a fault. Declare your own in
`maintenance/where-roles.json` (gitignored, format in §2), or point `TINYCMDR_WHERE_ROLES=<file>` at
a declaration of your own. The roles describe a box; they are never a statement about this
repository.

## The update rule (2026-10-03)

**Every user, on every released version, types `tinycmdr update` (or `/tinycmdr update` in
chat) and it works.** That is a hard rule, not an aspiration:

- the command fetches the latest **release package** and its `SHA256SUMS`, verifies the
  download before touching the install, and applies it while leaving every host-owned path
  alone (`config.json`, `.env`, `soul.md`, notes, `tools/`, `skills/`, sessions, state,
  jobs, tasks, logs, spill, venv, `theme.toml`);
- it never shells out to git: a dirty, pruned or gitless checkout is not a blocker (that
  was the 1.0.46-1.0.48 dead end);
- an install whose own updater is missing or broken is repaired by the **published**
  updater (`update.sh` / `update.ps1`, attached to every release): the `tinycmdr` launcher
  probes the local code and falls back to it;
- an update that changed the version must leave the NEW code running: chat restarts onto
  it, and a terminal session says exactly how;
- a host-owned file whose default moved is reported, with the remedy, by both `update` and
  `doctor` (shipped default beside it: `theme.default.toml`).

Anything that breaks this rule is a defect, not a compatibility note. Tests:
`tests/test_verbs.py` (the verb, the published updater, the release attachments) and the
two shims' fallback paths.

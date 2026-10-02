# How tinycmdr is developed

For whoever is at the keyboard next: another model, another harness, another person, an auditor
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
to a tracked file when you push or pull** - that is the state that blocked a `git pull` on
2026-09-29 while the other tree sat 27 commits ahead. `where.py --check` fails on it, and
`maintenance/pre-push.sh` runs that check.

On a one-tree box, `git status` being dirty is normal and expected while you work. `--check`
reports it anyway, on purpose. Commit or stash before pushing.

## 3. The flow

```bash
cd ~/tinycmdr
python3 maintenance/where.py              # 1. what is this box, before touching anything
git switch -c <topic>                     # 2. one change, one branch
# ...edit...
venv/bin/python tests/run_all.py          # 3. the gate: the same command CI runs
bash maintenance/pre-push.sh              # 4. the cheap set (also installed as the hook)
git commit && git push -u origin <topic>  # 5. push; CI grades macOS + Linux (Windows subset)
```

- **The gate** is `tests/run_all.py` and nothing else. A suite that cannot run exits `77` and
  counts as **red** - a machine that graded nothing cannot report success. `--select 'tests/test_*x*'`
  narrows a run while you work on one suite.
- **The pre-push hook** is not tracked by git; install it once per clone:
  ```bash
  printf '#!/bin/sh\nexec bash "$(git rev-parse --show-toplevel)/maintenance/pre-push.sh"\n' \
      > .git/hooks/pre-push && chmod +x .git/hooks/pre-push
  ```
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
gitignored; their shipped defaults live in the tree. An auditor should expect them to be missing
from a clone, and should not "restore" them:

| not in git | what it is | the shipped default |
| :--- | :--- | :--- |
| `.env`, `config.json` | tokens, endpoint, this box's identity | `.env.example`, `config.example.json` |
| `sessions/`, `logs/`, `spill/`, `tinycmdr.log`, `state.json`, `jobs.json`, `tinycmdr.lock` | conversation and runtime state | - |
| `notes.md`, `field-notes.md`, `atlas.md`, `experiments.jsonl`, `web-sessions.json` | this box's working memory | created on the host (`tests/fixture-field-notes.md` is what the digest suite stages) |
| `tools/`, `skills/` | drop-in tools and prose skills built on this host | `tools/` starter files, `skills/README.md` |
| `maintenance/private_rules.py` | this fleet's leak patterns | `private_rules.example.py` |
| `maintenance/where-roles.json` | this box's tree declaration | `ROLES` in `maintenance/where.py` |
| `venv/`, `dist/` | the private environment, and built archives | built by `maintenance/build-package.py` |

`maintenance/build-package.py` refuses to ship credentials, config, logs, session history or
notes - a leak there is a leak onto every host. `soul.md` is the exception on that list: it is
tracked, because it is the seed the agent's workspace starts from.

That seed is also the one tracked file an operator is expected to EDIT, so a persona is an
uncommitted modification and has to be treated as one: `tinycmdr update` copies an edited
`soul.md` aside (`soul.md.bak-update-<stamp>`) before its `git pull`, `tinycmdr doctor` says
whether the file is edited or still the shipped seed, and a pull that would overwrite it refuses
instead of merging. Editing it is safe on a normal install (no checkout, nothing pulls over it);
on a checkout, remember that anything discarding local changes - `git reset --hard`,
`git checkout -- .` - takes the persona with it. The shipped default also lives in the build as
`DEFAULT_SOUL`, so a host with no `soul.md` at all still runs a real persona.

## 5. Secrets and privilege

- Credentials live in `.env` on the host, mode 600: the chat token (`TINYCMDR_MM_TOKEN`), the sudo
  password (`SUDO_PASSWORD`), per-bot keys. The names the app writes are the names it reads
  (`tests/test_env_names.py`); values never print, and anything named `*PASSWORD` or `*PASSWD` of 6+
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

`release.sh` ships; it does not decide. The batch is four files and a decision, and this is the whole
of it - the two most recent releases were cut exactly this way.

1. **`tinycmdr.py`** - `VERSION = "1.0.4N"`. Byte-exact replacement: the working tree is CRLF, so a
   `sed` anchored with `$` silently misses.
2. **`CHANGELOG.md`** - fold `## [Unreleased]` into `## [1.0.4N] - <date>`: a summary paragraph, then
   `Changed`/`Added`/`Fixed` below it, each entry carrying the mechanism and the test. Leave an empty
   `## [Unreleased]` heading at the top.
3. **`STATUS.json`** - re-anchor every item this release carries to `{"commit": "<sha>"}` with **no**
   `expect`. That is the "merged, nobody is claiming a release yet" state, and `release.sh` promotes
   it to `expect: tagged` + `shipped` once the tag exists (`maintenance/ledger-tag.py`). Do **not**
   write `expect: untagged` on a commit the cut is about to tag: CI grades that claim while the tag
   is being created, and it fails. That is precisely the 1.0.39 incident.
4. **`docs/tinycmdr-what-it-is.md`** - `python3 maintenance/measured-block.py --write`, plus the prose
   line count the same tool's check complains about.
5. **Then**: `python3 tests/run_all.py` (green, `0 skipped`), `bash maintenance/pre-push.sh`, and
   `bash maintenance/release.sh <notes-file>`. The notes file becomes the release body verbatim, so
   write it fresh and factual. Afterwards, verify from outside the repo: download the published
   `SHA256SUMS` and one archive and check the sum.

Traps this project has actually paid for:

- **Do not edit the tree while a gate run is in progress.** The runner's G2 report attributes your
  write to whichever suite was running, and a half-written `STATUS.json` makes `test_status` read
  garbage.
- **A push is refused if any tracked file is dirty** in the tree declared `live` - including a doc you
  edited after the last commit. The hook is right; commit it.
- **The leak gate reads every tracked file.** A chat host, a LAN address or a bot account name in a
  ledger detail is refused before it can reach the remote.
- **`git add -A` in a live tree takes host state** unless `.gitignore` covers it (that is how
  `*.log.*` got there).
- The tag is created on the remote; `release.sh` fetches it back, and §1's
  `N commit(s) past <tag> (UNRELEASED)` line is what tells you afterwards whether the cut landed.

## 8. Starting from nothing

A fresh clone, or a new model told only "work on tinycmdr here":

```bash
python3.12 -m venv venv && venv/bin/pip install -r requirements.txt -r requirements-test.txt
python3 maintenance/where.py --remote      # what is this box, and what has GitHub got
venv/bin/python tests/run_all.py           # the baseline you are moving from
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

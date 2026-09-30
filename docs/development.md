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

**A. one tree** (`same_as`, used on this box). The install the bot runs from is also where code
work happens. `maintenance/where-roles.json` says so:

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
| `notes.md`, `tasks.json`, `tasks.md`, `tasks.journal.jsonl`, `field-notes.md`, `atlas.md`, `experiments.jsonl`, `web-sessions.json` | this box's working memory and task ledger | created on the host (`tests/fixture-field-notes.md` is what the digest suite stages) |
| `tools/`, `skills/` | drop-in tools and prose skills built on this host | `tools/` starter files, `skills/README.md` |
| `maintenance/private_rules.py` | this fleet's leak patterns | `private_rules.example.py` |
| `maintenance/where-roles.json` | this box's tree declaration | `ROLES` in `maintenance/where.py` |
| `venv/`, `dist/` | the private environment, and built archives | built by `maintenance/build-package.py` |

`maintenance/build-package.py` refuses to ship credentials, config, logs, session history, notes or
ledger - a leak there is a leak onto every host. `soul.md` is the exception on that list: it is
tracked, because it is the seed the agent's workspace starts from.

## 5. Secrets and privilege

- Credentials live in `.env` on the host, mode 600: the chat token (`TINYCMDR_MM_TOKEN`), the sudo
  password (`SUDO_PASSWORD`), per-bot keys. The names the app writes are the names it reads
  (`tests/test_env_names.py`); values never print, and anything named `*PASSWORD` or `*PASSWD` of 6+
  characters is scrubbed from tool output.
- Root is not blanket-granted on this box: a scoped `NOPASSWD` drop-in lives at
  `/private/etc/sudoers.d/tinycmdr`. `launchctl bootstrap/bootstrapout` are excluded on purpose -
  loading a plist is a full escalation. Everything else needs the password.
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

## 7. Starting from nothing

A fresh clone, or a new model told only "work on tinycmdr here":

```bash
python3.12 -m venv venv && venv/bin/pip install -r requirements.txt -r requirements-test.txt
python3 maintenance/where.py --remote      # what is this box, and what has GitHub got
venv/bin/python tests/run_all.py           # the baseline you are moving from
python3 tests/test_status.py               # what is already known-open
```

That is the whole onboarding. If those four disagree with any other document you were handed,
they are right and the document is stale.

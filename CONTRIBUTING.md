# Contributing to tinycmdr

Thanks for your interest. This project has a few conventions that are
load-bearing — they exist because the project has paid for them. Reading this
file before your first PR saves you and me a review round.

## Before you write code

1. Read [`AGENTS.md`](AGENTS.md) and [`docs/development.md`](docs/development.md).
   They are the contract; this file is the summary.
2. For anything beyond a one-line fix, open an issue first so I can agree on
   direction. Drive-by feature PRs without a prior discussion are likely to be
   closed, kindly.

## Set up and prove your change

```bash
python3.12 -m venv venv && venv/bin/pip install -r requirements.txt -r requirements-test.txt
venv/bin/python tests/run_all.py     # the gate; must be green
bash maintenance/pre-push.sh         # leak gate, tree-clean, regenerated numbers
```

- **The gate must be green.** A suite that cannot run exits `77` and counts as
  red. Never silence or skip a suite to get to green.
- **Every bug fix carries a regression test** that fails without the fix.
- **One commit per finding.** Message shape: `scope: what it does`, with a body
  explaining *why* plus the measurement or test that pins it. Look at the last
  twenty commits for the shape to copy.
- Keep diffs minimal. Refactors, renames, and "while I was in there" changes are
  separate PRs or they are rejected.

## Repo hygiene

- The repo is the recipe, not the kitchen. Never commit host state: `.env`,
  `config.json`, `soul.md`, `theme.toml`, logs, sessions, notes, or anything the
  app writes at runtime. Ship `*.example.*` / `*.default.*` files instead; the
  app materializes the live copy on first run. Enforcement (`maintenance/check-hygiene.py`
  in pre-push and CI) lands with it in an upcoming release; `maintenance/leak-gate.py`
  already checks secrets today.
- Never commit secrets, host names, internal paths, or tokens — not even in
  tests, fixtures, or commit messages. `maintenance/leak-gate.py` checks the
  tree, the commits, and every reachable blob, and it does not negotiate.
- Only add files a human edits. If a machine generates it, it does not go in git.

## What not to touch

- Numbers inside `measured:` blocks in `docs/tinycmdr-what-it-is.md` — they are
  rendered from the tree by `maintenance/measured-block.py`.
- `STATUS.json` entries — they are re-anchored by the maintenance tooling at
  release time.

## License

By contributing, you agree that your contributions are licensed under the MIT
License.

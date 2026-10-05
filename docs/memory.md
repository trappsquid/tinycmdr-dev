# Memory

The harness's durable memory is an **Open Knowledge Format (OKF) v0.2 bundle** under
`memory/`. This document is the profile of that spec this build implements, why the
format was chosen, and the rent it pays in the prompt.

The memory system was redesigned (2026-10-04) to work off Google Cloud's OKF.
Decisions taken then: the bundle is `memory/`; the tool surface is ONE tool named
`memory` (the old `remember`/`notes` pair is gone); existing `notes.md` is left in place
(no migration) and is still read; attestation is reserved for a later phase.

## Why a format, not a bigger file

`notes.md` was a flat list of dated lines with a curator. It had no provenance (where a
fact came from), no trust (who confirmed it), no lifecycle (deprecate instead of delete),
and its whole text had to fit the prompt. OKF answers those five questions from
frontmatter and makes progressive disclosure the shape of the corpus:

| question | OKF field |
| :--- | :--- |
| what was this created from? | `sources` (+ per-claim footnotes) |
| how much should I trust it? | `generated` / `verified` -> trust tier |
| is it still true? | `stale_after` (absolute instant) |
| is it the current version? | `status: draft \| stable \| deprecated` |
| was this number computed the sanctioned way? | `type: Attested Computation` (reserved) |

## Layout

```
memory/
  index.md      # frontmatter: okf_version "0.2" only; the progressive-disclosure list
  log.md        # newest-first update history, ISO dates
  <slug>.md     # one concept per fact
  .lock         # the cross-process write lock (never a concept)
```

`sources/*.json`, `notes.md` and the session sidecars are untouched by this system.

## Concept documents

YAML frontmatter + markdown body. `type` is the only required key (spec §4.1); this
build writes:

```markdown
---
type: Fact                      # Fact | Host | Runbook | Decision (unknown types are fine)
title: Disk: root is 65% full
description: Root volume: 287 of 460 GiB used.      # one line, derived from the body if absent
tags: [host, disk]
status: stable                  # absent is stable; deprecated is kept, flagged
stale_after: 2026-11-01T00:00:00Z
generated: { by: tinycmdr/<model>, at: 2026-10-04T09:00:00Z }
verified: { by: human:operator, at: 2026-10-04T09:05:00Z }
sources:
  - { resource: "df -h", title: "df output" }
---

# Notes

Root is at 65%.
```

Actor convention (spec §7): agents `tinycmdr/<version-or-model>`, people
`human:operator`. Trust tiers are derived from `verified` (spec §5.3): none =
unverified, non-human = machine-confirmed, a `human:` entry = human-reviewed.

**The YAML subset**: scalars, inline lists (`[a, b]`), inline maps (`{k: v}`) and block
lists of maps - exactly what the spec's own examples use. It is parsed with the stdlib
(three dependencies, and YAML is not one). Unknown keys and unknown types are preserved
and tolerated, per spec §4.1/§11; a concept with no frontmatter at all is still listed.

## The tool

`memory` (schema declares six fields; the handler accepts the rest):

| action | effect |
| :--- | :--- |
| `add` | one new concept; a duplicate title is refused and names the id to `update` |
| `update` | rewrite the body/fields in place, bumping `generated.at` |
| `deprecate` | `status: deprecated` - kept for history and links, flagged in the index |
| `forget` | delete the file; the log records the removal, not the content |
| `read` | the concept verbatim, with its tier/staleness header |
| `search` | title/description/tags/body substring hits |
| `list` | the index (what the prompt carries) |

Rules that are not negotiable in the handler: a body over `memory_concept_max_chars` is
**refused, never truncated** (a half-fact rides every future prompt - the notes.md
lesson); the bundle refuses new concepts past `memory_max_concepts`; every mutation runs
under one lock (`serialized_on(MEMORY_LOCK_FILE)`) so the concept, `index.md` and
`log.md` move together; text passes the secret scrubber first.

## What the prompt carries

`volatile_context()` (the trailing state block, never the system prompt) carries
`index.md`, capped at `memory_index_max_chars`, with each entry's flags - `(stale)`,
`(deprecated)`, `(unverified|machine-confirmed|human-reviewed)`. Concepts are read on
demand with `memory {action: "read"}`. The index over budget is cut with a marker that
names `memory action=list`; a read path never rewrites the bundle.

`notes.md` is legacy: still read (capped at `notes_max_chars`) and labelled as such, but
no tool writes it. Retiring it is the operator's call.

## Rent

The feature was built to a budget, and the budget is pinned:

- default static state (11 core tool schemas + system prompt): **~4.3K est tokens**;
- the all-tools-revealed worst case must stay inside the ratchet in
  `tests/test_envelope.py` (**5,400 est**; this landed at 5,331, which is why the
  `memory` schema declares six fields rather than eleven);
- no standing-instruction line anywhere names the format - the atlas carries two lines
  (`notes.md` legacy, `memory/`) and nothing else rides the static prompt.

## What pins it

- `tests/test_memory_okf.py` - the format contract: round-trip (unknown keys included),
  quoting, conformance, tiers, staleness, add/update/deprecate/forget, index, log, and
  tolerance of foreign files.
- `tests/test_cross_process.py` - three processes adding at once: every concept lands and
  the shared index lists all three.
- `tests/test_envelope.py`, `tests/test_disclosure.py` - the rent (ceiling, per-schema cap).
- `tests/test_harness_extras.py` - the secret scrubber on a memory write.

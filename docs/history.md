# This repository's history

The commits older than 2026-10-07 were machine-sanitised on that date and then re-published here,
in full, with their tags. This page says exactly what was replaced, so nobody has to guess.

Three classes of text were substituted:

- **Specific private values** - a chat host name, a LAN address, an account name, and a bot token
  that had reached a commit *message*. The replacements read `[redacted]`, and the exact strings
  are deliberately not printed here; the gate that finds them is `maintenance/private_rules.py`,
  which is not published. The token was rotated at the same time, and the repository it sat in was
  taken private (it is kept as the pre-sanitisation record).
- **The names of my own private working records** - session names, report filenames, the
  share they live on - and this fleet's own network prefixes.
- **Nothing else.** Finding ids that were written beside a reason in a source comment were left
  alone, and no test logic, no conclusion and no shipped string was rewritten.

## How to check it

- **The current release is byte-identical to what was published before the sanitisation.** The
  v1.0.87 archives and their `SHA256SUMS` were re-uploaded unchanged, so anyone holding an earlier
  download can verify that the code was not touched. (Releases before v1.0.87 keep their tags but
  their archives are not re-published; the tag is the source.)
- **Every tag points at its sanitised commit**, so `git diff v1.0.86 v1.0.87` gives the same
  change it always did, apart from the substituted classes above.
- `maintenance/leak-gate.py --history` scans every commit and every reachable blob for the private
  values and the working-record names; it reports clean on this history.

## Why not publish the original history instead

Because the bot token was in it. A published value cannot be un-published, so the choice was
between a described sanitisation and no history at all. This is the described one.

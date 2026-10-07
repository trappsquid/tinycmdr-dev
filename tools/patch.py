"""patch: one targeted edit per call, fuzzy anchors, a unified diff back.

Reach for it when edit_file's exact match fails: the anchor may differ from the
file in whitespace, indentation, line endings or case and still be the block
you mean. One replacement per call (or every occurrence with replace_all),
a byte-identical .bak before the write, every byte the edit did not reach kept
as it was (each line keeps its own ending; inserted lines take the file's
dominant one), and the diff of what changed so a wrong edit is visible when it
happens.
"""
import difflib
import os
import re
from pathlib import Path

NAME = "patch"
DESCRIPTION = ("Edit a file by matching a fuzzy anchor: tolerant of whitespace, "
               "indentation, line endings and case; one replacement per call; "
               "a unified diff back. Use when edit_file's exact match fails.")
SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string"},
        "old_string": {"type": "string", "description": "The anchor to find"},
        "new_string": {"type": "string", "description": "What replaces it"},
        "replace_all": {"type": "boolean",
                        "description": "Replace every match, not just a unique one"},
    },
    "required": ["path", "old_string", "new_string"],
}
MUTATES = True


def _lf_map(text):
    """(folded, start, after): `text` with every CRLF folded to LF, plus for every
    index of the folded text the index of that same character in the file (`start`) and
    the index just past it, its own \\r included (`after`). `start` carries an end
    sentinel. The maps are what let an edit write the file's own bytes back everywhere
    the match did not reach: a file that mixes endings (line1 CRLF, line2 LF) used to
    come back uniformly the dominant convention, so an edit to one line rewrote every
    other line's ending too."""
    folded, start, after = [], [], []
    i, n = 0, len(text)
    while i < n:
        if text[i] == "\r" and i + 1 < n and text[i + 1] == "\n":
            i += 1                     # the \r of a CRLF; the \n is folded below
        folded.append(text[i])
        start.append(i)
        after.append(i + 1)
        i += 1
    start.append(n)                    # sentinel: len(folded) -> end of text
    return "".join(folded), start, after


def _splice(text, head_at, tail_at, replacement):
    """`text` with the original bytes [head_at, tail_at) replaced, the rest byte-exact."""
    return text[:head_at] + replacement + text[tail_at:]


def _spans_exact(text, needle):
    out, start = [], 0
    while True:
        i = text.find(needle, start)
        if i < 0:
            return out
        out.append((i, i + len(needle)))
        start = i + max(1, len(needle))


def _spans_ci(text, needle):
    return [(m.start(), m.end())
            for m in re.finditer(re.escape(needle), text, re.I)]


def _spans_lines(lines, old_lines, skip_blanks):
    """[(first_line, last_line)] windows matching old_lines per line, edges and
    indentation ignored. skip_blanks also tolerates blank lines inside the
    block (the window then spans first..last matched line)."""
    want = [ln.strip() for ln in old_lines]
    if skip_blanks:
        want = [w for w in want if w]
        comp = [(i, ln.strip()) for i, ln in enumerate(lines) if ln.strip()]
        return [(comp[j][0], comp[j + len(want) - 1][0])
                for j in range(len(comp) - len(want) + 1)
                if [c[1] for c in comp[j:j + len(want)]] == want] if want else []
    return [(i, i + len(want) - 1)
            for i in range(len(lines) - len(want) + 1)
            if [ln.strip() for ln in lines[i:i + len(want)]] == want] if want else []


def run(args, ctx):
    path = Path(args["path"]).expanduser()
    old, new = args["old_string"], args["new_string"]
    replace_all = bool(args.get("replace_all"))
    if old == new:
        return "ERROR: old_string and new_string are identical"
    if not path.is_file():
        return f"ERROR: {path} does not exist"
    try:
        raw = path.read_bytes()
    except OSError as e:
        return f"ERROR reading {path}: {e}"
    if len(raw) > 8 * 1024 * 1024:
        return f"ERROR: {path} is over 8 MiB - edit it in pieces with a script"
    try:
        text = raw.decode("utf-8")
        enc = "utf-8"
    except UnicodeDecodeError:
        # A Latin-1/CP1252 source is a byte stream, not broken text. "replace" turned every
        # non-ASCII byte into U+FFFD and the writer then emitted those replacement
        # characters - bytes the edit never touched were destroyed, under a diff that
        # showed only the intended line. latin-1 maps every
        # byte 1:1, so anything the edit does not touch round-trips exactly.
        text = raw.decode("latin-1")
        enc = "latin-1"
    nl = "\r\n" if "\r\n" in text else "\n"
    lf, start, after = _lf_map(text)
    lf_old = old.replace("\r\n", "\n")
    lf_new = new.replace("\r\n", "\n")
    if not lf_old.strip():
        # An anchor with no non-blank content is unanchorable: as one word of spaces it
        # matched the first blank line under the whitespace-insensitive strategy, and as
        # a run of blank lines it matched (and collapsed) any adjacent blank run (night
        # audit run 11, A-133/A-134). Nothing to anchor on - refuse instead of editing
        # blind.
        return ("ERROR: old_string has no non-blank content - there is nothing to "
                "anchor on. Include the text of the line (or lines) to change.")
    # Only the lines the edit INSERTS take the file's dominant ending; the map keeps
    # every byte outside the match (including a lone CRLF in an LF file) as it was.
    replacement = lf_new.replace("\n", nl)

    # strategies in order of precision; the first with exactly one hit wins
    for strategy, spans in (
            ("exact", _spans_exact(lf, lf_old)),
            ("case-insensitive", _spans_ci(lf, lf_old))):
        if spans:
            if len(spans) > 1 and not replace_all:
                return (f"ERROR: old_string occurs {len(spans)} times. Include "
                        f"more surrounding lines, or set replace_all.")
            out = text
            for a, b in reversed(spans):
                # `after[b-1]` carries the \r of a CRLF that ended the match: it belongs
                # to the replaced text, not to the line after it.
                out = _splice(out, start[a], after[b - 1], replacement)
            return _finish(path, raw, text, out, strategy, len(spans), enc)

    lines = lf.split("\n")
    starts = [0]
    for ln in lines[:-1]:              # folded index of each line's first character
        starts.append(starts[-1] + len(ln) + 1)
    for strategy, skip in (("whitespace/indentation-insensitive", False),
                           ("blank-line-insensitive", True)):
        hits = _spans_lines(lines, lf_old.split("\n"), skip)
        if hits:
            if len(hits) > 1 and not replace_all:
                return (f"ERROR: {len(hits)} candidate regions match after "
                        f"normalisation. Include more surrounding lines, or "
                        f"set replace_all.")
            out = text
            for a, b in reversed(hits):
                # The window is lines a..b. Its last line's ending is kept, not dropped:
                # that newline is the separator before line b+1 (or the file's final
                # newline), and a CRLF there is one character pair, not a \r to lose.
                end = starts[b + 1] - 1 if b + 1 < len(lines) else len(lf)
                tail = start[end]
                if end < len(lf) and text[tail - 1:tail] == "\r":
                    tail -= 1
                out = _splice(out, start[starts[a]], tail, replacement)
            return _finish(path, raw, text, out, strategy, len(hits), enc)
    return ("ERROR: old_string not found in any of the four match modes "
            "(exact, case, whitespace, blank lines). Read the section with "
            "read_file and copy a shorter unique anchor.")


def _finish(path, raw, before, out, strategy, count, enc="utf-8"):
    if out == before:
        return "OK: nothing changed"
    try:
        data = out.encode(enc)
    except UnicodeEncodeError as e:
        # The file's own encoding cannot carry what was just typed. Say that instead of
        # writing replacement characters or a mojibake re-encode (A-131's sibling risk).
        return ("ERROR: %s is %s, which cannot carry %r. Convert the file first, or "
                "write it with write_file." % (path, enc, e.object[e.start:e.end]))
    backup = path.with_name(path.name + ".bak")
    try:
        backup.write_bytes(raw)               # byte-identical, or it is no backup
        tmp = path.with_name(path.name + ".tmp-patch")
        tmp.write_bytes(data)                 # bytes: no newline translation, no re-encode
        os.replace(tmp, path)
    except OSError as e:
        return f"ERROR writing {path}: {e} (backup: {backup.name})"
    rows = []
    for line in difflib.unified_diff(
            before.replace("\r\n", "\n").split("\n"),
            out.replace("\r\n", "\n").split("\n"),
            fromfile=f"{path} (before)", tofile=f"{path} (after)",
            lineterm="", n=1):
        rows.append(line[:200])
        if len(rows) >= 60:
            rows.append("... diff truncated; read the file to see the rest")
            break
    return (f"OK: patched {path} [strategy: {strategy}] "
            f"({count} occurrence(s), backup: {backup.name})\n"
            f"--- diff ---\n" + "\n".join(rows))

if __name__ == "__main__":
    # Standalone smoke test: `python patch.py path=... old_string=... new_string=...`
    # prints run()'s result. ctx is empty here; a tool that needs the run's ctx
    # (shell, send_file, report) is called through the harness.
    import json
    import sys
    args = dict()
    for tok in sys.argv[1:]:
        k, _, v = tok.partition("=")
        try:
            args[k] = json.loads(v)
        except ValueError:
            args[k] = v
    print(run(args, dict()))

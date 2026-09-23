"""patch: one targeted edit per call, fuzzy anchors, a unified diff back.

Reach for it when edit_file's exact match fails: the anchor may differ from the
file in whitespace, indentation, line endings or case and still be the block
you mean. One replacement per call (or every occurrence with replace_all),
a byte-identical .bak before the write, the file's own newline convention kept,
and the diff of what changed so a wrong edit is visible when it happens.
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
    text = raw.decode("utf-8", "replace")
    nl = "\r\n" if "\r\n" in text else "\n"
    lf, lf_old, lf_new = (s.replace("\r\n", "\n") for s in (text, old, new))

    # strategies in order of precision; the first with exactly one hit wins
    for strategy, spans in (
            ("exact", _spans_exact(lf, lf_old)),
            ("case-insensitive", _spans_ci(lf, lf_old))):
        if spans:
            if len(spans) > 1 and not replace_all:
                return (f"ERROR: old_string occurs {len(spans)} times. Include "
                        f"more surrounding lines, or set replace_all.")
            out = lf
            for a, b in reversed(spans):
                out = out[:a] + lf_new + out[b:]
            return _finish(path, raw, text, out, nl, strategy, len(spans))

    lines = lf.split("\n")
    for strategy, skip in (("whitespace/indentation-insensitive", False),
                           ("blank-line-insensitive", True)):
        hits = _spans_lines(lines, lf_old.split("\n"), skip)
        if hits:
            if len(hits) > 1 and not replace_all:
                return (f"ERROR: {len(hits)} candidate regions match after "
                        f"normalisation. Include more surrounding lines, or "
                        f"set replace_all.")
            for a, b in reversed(hits):
                lines = lines[:a] + lf_new.split("\n") + lines[b + 1:]
            return _finish(path, raw, text, "\n".join(lines), nl, strategy,
                           len(hits))
    return ("ERROR: old_string not found in any of the four match modes "
            "(exact, case, whitespace, blank lines). Read the section with "
            "read_file and copy a shorter unique anchor.")


def _finish(path, raw, before, after_lf, nl, strategy, count):
    if after_lf == before.replace("\r\n", "\n"):
        return "OK: nothing changed"
    out = after_lf.replace("\n", nl) if nl != "\n" else after_lf
    backup = path.with_name(path.name + ".bak")
    try:
        backup.write_bytes(raw)               # byte-identical, or it is no backup
        tmp = path.with_name(path.name + ".tmp-patch")
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write(out)                      # newline="": no translation
        os.replace(tmp, path)
    except OSError as e:
        return f"ERROR writing {path}: {e} (backup: {backup.name})"
    rows = []
    for line in difflib.unified_diff(
            before.replace("\r\n", "\n").split("\n"), after_lf.split("\n"),
            fromfile=f"{path} (before)", tofile=f"{path} (after)",
            lineterm="", n=1):
        rows.append(line[:200])
        if len(rows) >= 60:
            rows.append("... diff truncated; read the file to see the rest")
            break
    return (f"OK: patched {path} [strategy: {strategy}] "
            f"({count} occurrence(s), backup: {backup.name})\n"
            f"--- diff ---\n" + "\n".join(rows))

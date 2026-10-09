"""The doc-drift gate: docs/tinycmdr-what-it-is.md must agree with the tree.

This project's pitch is measured, honest numbers, so a stale number in that document is not
cosmetic - it is the one thing that would make a reader distrust every other figure in it.
file is 21,635 lines / 1.03 MB), "no benchmark or eval harness" (tests/eval_tasks.py has 18
machine-graded tasks), "no release process" (.github/workflows/ci.yml + maintenance/release.sh),
and section 3.2's budget defaults were off by 2-6x (40/10/180/6000 where the tree says
250/75/300/10000).

So the numbers are rendered from the code between markers by maintenance/measured-block.py,
and this suite is the gate. It falsifies itself on a doctored copy: a gate that cannot fail
grades nothing.

What is NOT graded here, and cannot be: anything measured against a live endpoint (the token
figures in section 4.1). Those stay hand-written with their provenance, and the check below
only asserts the provenance is present so a reader can re-run it.

    python tests/test_measured_doc.py
"""
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DOC = BASE / "docs" / "tinycmdr-what-it-is.md"
SRC = BASE / "tinycmdr.py"
GENERATOR = BASE / "maintenance" / "measured-block.py"

FAILS = []


def check(name, cond, detail=""):
    if cond:
        print(f"ok   {name}")
    else:
        FAILS.append(name)
        print(f"FAIL {name}: {detail}")


def load_generator():
    spec = importlib.util.spec_from_file_location("tc_measured_block", GENERATOR)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tc_measured_block"] = mod
    spec.loader.exec_module(mod)
    return mod


SCALE = BASE / "tests" / "tool_index_scale.py"
# Both documents state this figure, and the generated doc's own gate reads only the doc:
# README said ~5.9 while the doc said 5.7, and no check could tell (run 22, A-2026-10-07-58).
PER_TOOL_RX = re.compile(r"([\d.]+) (?:chars|characters)(?:[^\n]{0,40})?per tool"
                         r"(?:[^\n]{0,20})?at 80 tools")


def measured_chars_per_tool():
    """The index chars per tool at 80 tools, from the scale gate's own run. None on failure.

    The number is measured against a staged tree by `tests/tool_index_scale.py`; asking that
    tool rather than pinning a constant is what makes this a check on the DOCUMENTS.
    """
    out = subprocess.run([sys.executable, str(SCALE), "0", "80"], cwd=str(BASE),
                         capture_output=True, text=True, timeout=300)
    if out.returncode != 0:
        return None
    for line in out.stdout.splitlines():
        fields = line.split()
        if fields and fields[0] == "80" and len(fields) >= 10:
            try:
                return float(fields[9])
            except ValueError:
                return None
    return None


def _prose(doc):
    """The doc with every measured block cut out - the only part nothing renders for you.

    What is inside the markers is generated from the tree and gated by stale_blocks(); the
    prose between them is written by hand, and until 2026-09-29 nothing checked it at all.
    """
    out, at = [], 0
    for m in re.finditer(r"<!-- measured:([A-Za-z0-9_]+):start -->", doc):
        out.append(doc[at:m.start()])
        end = doc.find("<!-- measured:%s:end -->" % m.group(1), m.end())
        cut = doc.find("\n", end) if end != -1 else m.end()
        at = cut + 1 if cut != -1 else len(doc)
    out.append(doc[at:])
    return "".join(out)


def main():
    check("the generator is in the tree", GENERATOR.exists(), GENERATOR)
    if not GENERATOR.exists():
        return 1
    check("the doc is in the tree", DOC.exists(), DOC)
    if not DOC.exists():
        return 1
    mb = load_generator()
    check("the generator knows the blocks before it renders anything",
          set(mb.BLOCKS) >= {"header", "surface", "budgets", "readability"}, mb.BLOCKS)

    doc = DOC.read_text(encoding="utf-8")
    missing = [n for n in mb.BLOCKS if mb.current_block(doc, n) is None]
    check("every measured block has its marker pair in the doc", not missing, missing)

    stale = mb.stale_blocks()
    check("the committed numbers agree with the tree", not stale,
          "\n".join("%s: %s" % (n, w.splitlines()[-1] if w else "") for n, w in stale))

    # The markers sit OUTSIDE the fence, so the rendered page does not show HTML comments
    # as code: each fenced block's body starts with a fence line.
    for name in sorted(mb.FENCED):
        body = (mb.current_block(doc, name) or "").splitlines()
        check("the %s block is fenced, markers outside" % name,
              len(body) > 2 and body[0].strip() == "```" and body[-1].strip() == "```",
              body[:1] + body[-1:])

    # The count must not depend on the checkout: GitHub's Windows job checks out with CRLF
    # (core.autocrlf=true), which adds one byte per line - about 21 KB here, enough to make
    # the doc look stale and fail the gate on one platform only.
    work = Path(tempfile.mkdtemp(prefix="tc-docdrift-crlf-"))
    try:
        crlf = work / "tinycmdr-crlf.py"
        # From the LF-normalized bytes: the checked-out file already carries stray CRs, and
        # a blind \n -> \r\n would produce CR CR LF, which is a different test.
        lf = SRC.read_bytes().replace(b"\r\n", b"\n")
        crlf.write_bytes(lf.replace(b"\n", b"\r\n"))
        check("a CRLF checkout measures the same as an LF one",
              mb.file_stats(crlf) == mb.file_stats(SRC),
              "%s vs %s" % (mb.file_stats(crlf), mb.file_stats(SRC)))
    finally:
        shutil.rmtree(work, ignore_errors=True)

    # The falsification: the same check, on a copy with one rendered number changed, MUST
    # come back stale. Without this, "agree with the tree" could pass by not looking.
    work = Path(tempfile.mkdtemp(prefix="tc-docdrift-"))
    try:
        doctored = work / "what-it-is.md"
        shutil.copy2(DOC, doctored)
        text = doctored.read_text(encoding="utf-8")
        line = next((l for l in text.splitlines() if l.startswith("code                ")), "")
        check("the surface block carries a line-count line to doctor", bool(line), line)
        n = line.split()[1]
        text = text.replace(line, line.replace(n, "%s9" % n), 1)
        doctored.write_text(text, encoding="utf-8")
        caught = mb.stale_blocks(doctored)
        # Only the block that carries the doctored line is reported; the section-8 block
        # carries the same count in a different line, so it is not stale and must not be
        # blamed. What matters is that the doctoring was caught at all.
        check("a doctored line count IS caught (the gate can fail)",
              [c[0] for c in caught] == ["surface"], [c[0] for c in caught])

        # ...and a missing marker is caught too, rather than silently skipped.
        text = doctored.read_text(encoding="utf-8").replace(
            "<!-- measured:budgets:start -->", "<!-- measured:budgets:gone -->")
        doctored.write_text(text, encoding="utf-8")
        caught = dict(mb.stale_blocks(doctored))
        check("a missing marker is a failure, not a skip",
              caught.get("budgets") == "markers missing", caught.get("budgets"))
    finally:
        shutil.rmtree(work, ignore_errors=True)

    # The facts that are NOT computable must keep their provenance, and the claims this suite
    # was written against must not come back.
    # These checks were PHRASE-shaped, and 2026-09-29 is what that cost (review 5.1/5.2): the
    # guard forbade the literal "no benchmark or eval harness" while a synonym - "no evaluation
    # suite" - sat in the same file contradicting the gated block twelve lines above it, and six
    # numbers in the unguarded prose had gone stale unnoticed (a 5.8k-line file, 474 assertions
    # twice, 43 skills three times). A remembered sentence is not a fact, so a passing run of
    # this suite did not mean the document was consistent. Denials are a FAMILY of phrasings
    # now, and every number the prose restates has to equal the one rendered from the tree.
    check("the doc points at the command that re-measures the token figures",
          "maintenance/measure-prompt.py" in doc, "no pointer to measure-prompt.py")
    check("the doc names the graded set it does have",
          "tests/eval_tasks.py" in doc, "eval tasks not mentioned")
    check("the doc names the release machinery it does have",
          "maintenance/release.sh" in doc, "release.sh not mentioned")
    check("the doc says releases are unsigned (the honest half of the claim)",
          "NOT signed" in doc or "not signed" in doc)

    # The tool table advertised execute_code as running Python "in-process". It runs
    # `[sys.executable, "-X", "utf8", "-c", code]` under a timeout, and the subprocess is the
    # better design - it is what makes a runaway script killable - so the doc was underselling
    # it. Asserted in BOTH directions: the sentence cannot drift back, and the call cannot
    # quietly become an exec without this suite saying so. Matched on the two halves, not the
    # whole argv: a flag added between them (the "-X utf8", 2026-10-02) is not
    # the call going away.
    app = (BASE / "tinycmdr.py").read_text(encoding="utf-8", errors="replace")
    check("the doc does not claim execute_code runs Python in-process",
          "run Python in-process" not in doc, "the in-process claim is back in the table")
    check("execute_code runs Python in a subprocess (what makes it killable)",
          "sys.executable," in app and '"-c", code' in app,
          "the subprocess call is gone from tinycmdr.py")

    denials = ("no benchmark or eval harness", "no evaluation suite", "no eval set",
               "no evaluation harness", "no release process", "no test suite", "no ci")
    said = [d for d in denials if d in doc.lower()]
    check("the doc denies neither the eval set nor the release process, in any phrasing",
          not said, said)

    f = mb.facts()
    prose = _prose(doc)
    # The patterns come from the generator (mb.PROSE_NUMBERS) - the same list --write
    # rewrites through, so the graded and the written numbers cannot drift apart.
    for label, pat, key in mb.PROSE_NUMBERS:
        want = f[key]
        wrong = [n for n in re.findall(pat, prose) if int(n.replace(",", "")) != want]
        check("every %s the prose states matches the tree (%s)" % (label, want), not wrong, wrong)

    # ...and --write OWNS those numbers (mb.rewrite_numbers): a doctored one must come
    # back repaired, or the hand step it replaced returns the first time a number moves.
    prose_now = mb.prose_of(doc)
    present = 0
    for label, pat, _key in mb.PROSE_NUMBERS:
        mo = re.search(pat, prose_now)
        if not mo:
            continue
        present += 1
        # Doctor the NUMBER only, keeping the words around it: replacing the whole match
        # would delete the pattern and the writer would have nothing to repair.
        bad = prose_now[:mo.start(1)] + mo.group(1) + "9" + prose_now[mo.end(1):]
        fixed, n = mb.rewrite_numbers(bad, f)
        check("--write repairs a doctored %s" % label,
              n >= 1 and fixed == prose_now, (label, n))
    check("the prose restates at least one measured number to doctor", present > 0, present)

    # A per-host number in a document a stranger reads is a claim about the author's box:
    # skills/ is gitignored, this box holds two, and the doc claimed 43 in three places.
    per_host = re.findall(r"\d+ (?:prose )?skills", prose)
    check("the prose quotes no per-host skill count", not per_host, per_host)

    # ---- the "flat as it grows" figure, in BOTH documents --------------------------------
    # The generated doc's gate reads only the generated doc, so README's headline number could
    # rot with nothing failing - and it had, by 0.2 (run 22, A-2026-10-07-58). Both are graded
    # against the scale gate's own run, so the next tool-name change moves them together.
    measured = measured_chars_per_tool()
    check("the scale gate reports chars per tool at 80 tools", measured is not None, measured)
    if measured is not None:
        for path in (BASE / "README.md", DOC):
            text = path.read_text(encoding="utf-8")
            figures = PER_TOOL_RX.findall(text)
            check("every 'per tool at 80 tools' figure in %s is measured" % path.name,
                  bool(figures) and all(abs(float(f) - measured) < 0.05 for f in figures),
                  "%s states %s; measured %.1f" % (path.name, figures, measured))

    print()
    if FAILS:
        print("%d FAILED: %s" % (len(FAILS), ", ".join(FAILS)))
        return 1
    print("all doc-drift checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""The memory bundle: OKF frontmatter subset, concepts, index.md, log.md.

The corpus is written by an agent and read by an agent (and, later, by other OKF
tools), so what is graded here is the FORMAT contract, not prose: a parse/dump
round-trip that loses nothing (unknown keys included), conformance on everything we
write (`type` is always present), the trust/lifecycle derivations the spec defines,
and the index and log a consumer browses.
"""
import importlib.util
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SRC = BASE / (os.environ.get("TINYCMDR_TEST_APP")
              or os.environ.get("TINYCMDR_SRC") or "tinycmdr.py")
# A STAGED copy: the module writes into BASE_DIR while it is being imported, so importing
# the checkout's own file plants sessions/ and its log in the repo.
STAGE = Path(tempfile.gettempdir()) / "tinycmdr-test-stage-memory"
STAGE.mkdir(parents=True, exist_ok=True)
shutil.copy2(SRC, STAGE / "tinycmdr.py")
spec = importlib.util.spec_from_file_location("tinycmdr_memory_under_test",
                                              STAGE / "tinycmdr.py")
fb = importlib.util.module_from_spec(spec)
sys.modules["tinycmdr_memory_under_test"] = fb
spec.loader.exec_module(fb)

TMP = Path(tempfile.mkdtemp(prefix="fbmemory-"))
import atexit                                                            # noqa: E402
atexit.register(lambda: shutil.rmtree(TMP, ignore_errors=True))
sys.path.insert(0, str(BASE / "tests"))
import hermetic                                                          # noqa: E402
hermetic.redirect_repo_files(fb, TMP)
FAILURES = []
PASSES = []


def check(name, cond, detail=""):
    if cond:
        PASSES.append(name)
        print(f"ok   {name}")
    else:
        FAILURES.append(name)
        print(f"FAIL {name}: {detail}")


def _fresh():
    """An empty bundle for a test: the suite shares one process, not one tree."""
    shutil.rmtree(fb.MEMORY_DIR, ignore_errors=True)
    fb.MEMORY_DIR.mkdir(parents=True, exist_ok=True)


def test_frontmatter_round_trips_every_field_we_write():
    fm = {"type": "Fact", "title": "Disk: root is 65% full",
          "description": "Root volume: 287 of 460 GiB used (2026-10-04).",
          "tags": ["host", "disk"], "status": "draft",
          "stale_after": "2026-11-01T00:00:00Z",
          "generated": {"by": "tinycmdr/main", "at": "2026-10-04T09:00:00Z"},
          "verified": [{"by": "human:operator", "at": "2026-10-04T09:05:00Z"}],
          "sources": [{"id": "df", "resource": "https://example.invalid/df",
                       "title": "df -h output"}],
          "x_custom": "kept"}
    text = fb.okf_dump(fm, "# Notes\n\nRoot is at 65%.\n")
    got, body = fb.okf_parse(text)
    check("a concept round-trips through dump/parse unchanged", got == fm,
          f"{got!r} != {fm!r}")
    check("...with the body intact", body.strip() == "# Notes\n\nRoot is at 65%.",
          repr(body))


def test_conformance_type_always_present():
    text = fb.okf_dump({"type": "Fact", "title": "T"}, "body")
    got, _ = fb.okf_parse(text)
    check("every written concept carries a non-empty type (spec §11)",
          bool(str(got.get("type") or "").strip()), repr(got))
    check("unknown keys survive the round-trip (spec §4.1)", "x_custom" in
          fb.okf_dump({"type": "Fact", "x_custom": 1}), "custom key dropped")


def test_scalars_that_would_misparse_get_quoted():
    risky = ["has: colon", "- leading dash", "trailing space ", "", "true", "42",
             "needs # hash",
             # json.dumps is how the emitter quotes these, so parse must UNDO the
             # escapes rather than only strip the quotes: a Windows path doubled its
             # backslashes on every rewrite (A-2026-10-05-29).
             r"C:\Users\David Trapp\tinycmdr", 'say "hi"', r"two\\pairs\\here"]
    for value in risky:
        text = fb.okf_dump({"type": "Fact", "title": value}, "")
        got, _ = fb.okf_parse(text)
        check(f"a scalar round-trips: {value!r}", got.get("title") == value,
              repr(got.get("title")))
    tricky = r"C:\Users\David Trapp"
    once = fb.okf_dump({"type": "Fact", "title": tricky}, "")
    again, _ = fb.okf_parse(once)
    check("a backslash value re-dumps byte-identically (idempotent)",
          fb.okf_dump({"type": "Fact", "title": again.get("title")}, "") == once,
          repr(again.get("title")))
    plain = fb.okf_dump({"type": "Fact", "title": "plain-title_1"}, "")
    check("...and an unquotable one stays unquoted", "title: plain-title_1" in plain,
          plain)


def test_trust_tiers_and_staleness():
    check("no verified key is unverified", fb._okf_tier({}) == "unverified")
    check("a bare verified mapping reads as a one-element list",
          fb._okf_tier({"verified": {"by": "human:operator",
                                     "at": "2026-10-04T09:00:00Z"}}) == "human-reviewed")
    check("machine verification is machine-confirmed",
          fb._okf_tier({"verified": [{"by": "process:disk-check",
                                      "at": "2026-10-04T09:00:00Z"}]}) == "machine-confirmed")
    check("a human among verifiers is human-reviewed",
          fb._okf_tier({"verified": [{"by": "process:x", "at": "2026-10-04T09:00:00Z"},
                                     {"by": "human:operator", "at": "2026-10-04T09:00:00Z"}]
                        }) == "human-reviewed")
    check("no stale_after is not stale", fb._okf_stale({}) is False)
    check("a past stale_after is stale",
          fb._okf_stale({"stale_after": "2000-01-01T00:00:00Z"}) is True)
    check("a future stale_after is fresh",
          fb._okf_stale({"stale_after": "2999-01-01T00:00:00Z"}) is False)
    # A-2026-10-05-27: the compare used to be lexical, so a non-zero-padded date read
    # as the FUTURE ('2026-9-1' < '2026-10-05') and the concept silently never aged.
    check("a non-zero-padded past date still compares as a date",
          fb._okf_stale({"stale_after": "2000-1-1"}) is True)
    check("a date-only past value is stale",
          fb._okf_stale({"stale_after": "2000-01-01"}) is True)
    check("an unreadable value fails open, named not silent",
          fb._okf_stale({"stale_after": "soon-ish"}) is False)


def test_new_concept_writes_a_conformant_file_and_slugs_it():
    _fresh()
    made = fb.memory_new_concept("Disk: root is 65% full", "Root is at 65%.",
                                 ctype="Fact", tags=["host"], description="df -h")
    check("the slug is derived from the title", made["id"] == "disk-root-is-65-full",
          made["id"])
    text = made["path"].read_text(encoding="utf-8")
    fm, body = fb.okf_parse(text)
    check("the file parses with a type and title",
          fm.get("type") == "Fact" and fm.get("title") == "Disk: root is 65% full")
    check("generated.by names the harness as the actor",
          str(fm["generated"]["by"]).startswith("tinycmdr/"), repr(fm.get("generated")))
    check("generated.at is an ISO instant", str(fm["generated"]["at"]).endswith("Z"),
          repr(fm.get("generated")))
    check("the body is the markdown after the frontmatter", body.strip() == "Root is at 65%.")
    try:
        fb.memory_new_concept("Disk: root is 65% full", "again")
        check("a second concept with the same title is refused", False, "no raise")
    except FileExistsError as e:
        check("a second concept with the same title is refused", str(e) == made["id"], str(e))
    other = fb.memory_new_concept("Disk root is 65 full", "x")
    check("a different title on a taken slug gets -2", other["id"] == made["id"] + "-2",
          other["id"])


def test_update_preserves_unknown_keys_and_bumps_generated():
    _fresh()
    made = fb.memory_new_concept("A fact", "old body")
    path = made["path"]
    fm, body = fb.okf_parse(path.read_text(encoding="utf-8"))
    fm["x_kept"] = "yes"
    fm["generated"]["at"] = "2000-01-01T00:00:00Z"
    path.write_text(fb.okf_dump(fm, body), encoding="utf-8")
    out = fb.memory_update_concept(made["id"], body="new body", description="now")
    fm2, body2 = fb.okf_parse(path.read_text(encoding="utf-8"))
    check("an update rewrites the body", body2.strip() == "new body", repr(body2))
    check("an update keeps unknown keys", fm2.get("x_kept") == "yes", repr(fm2))
    check("an update bumps generated.at",
          fm2["generated"]["at"] != "2000-01-01T00:00:00Z", repr(fm2["generated"]))
    check("an unknown id changes nothing", fb.memory_update_concept("nope", body="x") is None)


def test_lifecycle_deprecate_and_forget():
    _fresh()
    made = fb.memory_new_concept("Old port number", "8080")
    fb.memory_set_status(made["id"], "deprecated", reason="moved to 8790")
    fm, _ = fb.okf_parse(made["path"].read_text(encoding="utf-8"))
    check("deprecate writes status: deprecated", fm.get("status") == "deprecated", repr(fm))
    index = fb.memory_index_render()
    # The flag itself, not the exact parenthesisation: every index line carries its
    # verification tier beside the lifecycle flags now, so "(deprecated)" alone would
    # grade punctuation instead of the claim.
    check("the index flags a deprecated concept",
          re.search(r"\([^)]*\bdeprecated\b[^)]*\)", index) is not None, index)
    again = fb.memory_set_status(made["id"], "stable")
    fm2, _ = fb.okf_parse(made["path"].read_text(encoding="utf-8"))
    check("returning to stable drops the key", "status" not in fm2, repr(fm2))
    fb.memory_forget(made["id"])
    check("forget removes the concept file", not made["path"].exists())
    log = fb.MEMORY_LOG.read_text(encoding="utf-8")
    check("the log records the removal", "forgotten" in log, log[-200:])


def test_index_is_progressive_disclosure():
    _fresh()
    fb.memory_new_concept("Alpha fact", "a", ctype="Fact", description="first")
    fb.memory_new_concept("Beta host note", "b", ctype="Host", description="second",
                          stale_after="2000-01-01T00:00:00Z")
    fb.memory_index_update()
    index = fb.MEMORY_INDEX.read_text(encoding="utf-8")
    fm, _ = fb.okf_parse(index)
    check("index.md declares the OKF version it targets",
          fm.get("okf_version") == "0.2", repr(fm))
    check("...and is otherwise frontmatter-free", len(fm) == 1, repr(fm))
    check("sections are per type", "# Fact" in index and "# Host" in index, index)
    check("an entry links its concept with the description",
          "* [Alpha fact](alpha-fact.md) - first" in index, index)
    check("a stale concept is flagged",
          re.search(r"\([^)]*\bstale\b[^)]*\)", index) is not None, index)
    check("a fresh one is not",
          re.search(r"\bstale\b", index.split("# Host")[0]) is None, index)
    check("an empty bundle renders an empty index", "No concepts yet" in
          fb.memory_index_render([]), "no empty marker")


def test_index_cut_names_what_it_drops():
    """An over-budget index is cut on a line boundary and NAMES the sections it lost
    (A-2026-10-05-26): the index is grouped by type, so a blind head-cut dropped whole
    categories from every prompt with nothing said."""
    _fresh()
    for i in range(3):
        fb.memory_new_concept("Fact %d" % i, "body", ctype="Fact",
                              description="x" * 150)
    fb.memory_new_concept("Runbook only item", "step", ctype="Runbook",
                          description="wire the thing")
    fb.memory_index_update()
    saved = fb.CONFIG["agent"].get("memory_index_max_chars")
    fb.CONFIG["agent"]["memory_index_max_chars"] = 400
    try:
        block = fb.volatile_context()
    finally:
        fb.CONFIG["agent"]["memory_index_max_chars"] = saved
    check("an over-budget index says it was cut", "index cut at" in block, block[-400:])
    check("...and names the dropped section", "dropped: Runbook" in block,
          block[-400:])


def test_log_is_newest_first_and_date_grouped():
    _fresh()
    fb.memory_new_concept("One", "1")
    fb._memory_log_append("Creation", "[One](one.md)")
    fb._memory_log_append("Update", "[One](one.md) - second change")
    log = fb.MEMORY_LOG.read_text(encoding="utf-8")
    import time as _t
    today = _t.strftime("%Y-%m-%d")
    check("today's heading exists once", log.count("## " + today) == 1, log)
    check("entries are newest first",
          log.index("second change") < log.index("**Creation**"), log)
    check("the log opens with its title", log.startswith("# Memory log"), log[:60])


def test_parser_is_permissive():
    check("a document with no frontmatter parses as body-only",
          fb.okf_parse("just text") == ({}, "just text"))
    fm, body = fb.okf_parse("---\ntype: Fact\nbroken line without colon\n---\nbody")
    check("an unknown line inside frontmatter is ignored, not fatal",
          fm.get("type") == "Fact" and body.strip() == "body", f"{fm!r} {body!r}")
    fm2, _ = fb.okf_parse("---\ntitle: X\n---\nno type at all")
    check("a concept without type still parses (consumers must tolerate it)",
          fm2.get("title") == "X", repr(fm2))


def test_a_foreign_or_broken_file_never_breaks_the_bundle():
    """The corpus is written by an agent, so a hand-made or foreign file lands in it.
    Spec §11: consumers must tolerate a missing frontmatter block, an unknown type and
    unknown keys - the scan and the index included."""
    _fresh()
    fb.memory_new_concept("Good one", "a")
    (fb.MEMORY_DIR / "foreign.md").write_text("no frontmatter at all", encoding="utf-8")
    (fb.MEMORY_DIR / "half-good.md").write_text("---\ntitle: No type\n---\nbody",
                                                encoding="utf-8")
    ids = {c["id"] for c in fb.memory_scan()}
    check("a concept with no frontmatter is still listed", "foreign" in ids, sorted(ids))
    check("...and one without a type parses", "half-good" in ids, sorted(ids))
    index = fb.memory_index_render()
    check("the index renders with both present",
          "Good one" in index and "No type" in index, index)
    check("...filing the typeless one under Concept", "# Concept" in index, index)


def test_the_index_line_carries_the_operative_token():
    """The index is the WHOLE prompt side of memory: it must not read as complete while
    the operative token was trimmed away (measured 2026-10-04: an index line ended
    "…ffmpeg 8.1.1 at" and `-lmin`, the point, never reached the prompt)."""
    _fresh()
    # getattr so a build WITHOUT the derivation fails these checks instead of crashing the
    # suite: falsification is a FAIL summary, not a traceback (test_route_hint's rule).
    derive = getattr(fb, "_memory_description", lambda *a, **k: "")
    title = "DVD mpeg2 quality is capped by -lmin, not -b:v (~/dvd_work)"
    body = ("Measured 2026-10-04 on the real source (30s slice at t=2700, libvmaf ADM/VIF "
            "vs a lossless reference of the same slice), ffmpeg 8.1.1 at /opt/homebrew/bin:"
            "\n\n- `-b:v 5000k`, `7000k` and `9000k` produced **byte-identical** files. "
            "The encoder is not budget-limited; it is limited by `-lmin`, default **236**.\n")
    desc = derive(title, body)
    check("the derived index line carries the OPERATIVE token, not the first sentence",
          "-lmin" in desc, desc)
    check("...and it is a complete unit (no silent mid-clause cut)",
          desc.endswith(".") or desc.endswith("…"), desc)
    desc2 = derive("A title", "- " + "x" * 400)
    check("a unit that cannot fit is cut WITH the ellipsis saying so",
          desc2.endswith("…") and len(desc2) < 200, desc2[-40:])

    made = fb.memory_new_concept(title, body)
    check("a concept born with no description gets that line in its frontmatter",
          "-lmin" in str(made["fm"].get("description") or ""),
          made["fm"].get("description"))
    check("an explicit description is kept verbatim",
          fb.memory_new_concept("Another", "body", description="the point -lmin")["fm"]
          .get("description") == "the point -lmin")


def test_add_refuses_a_restatement_and_supersedes_replaces():
    """Nothing piles up: a restatement of a concept already in the bundle is refused with
    the id to update, and a replacement says so with `supersedes` - the ledger's rule
    (keys+exact_config refused, supersedes named) applied to memory."""
    _fresh()
    body = ("The optical drive test is a non-empty `drutil status` output; system_profiler "
            "intermittently prints nothing even with the drive attached. macOS has no "
            "setsid. The ONLY env var is reset by the script; use --only NAME.\n")
    first = fb.tool_memory({"action": "add", "title": "DVD drive detection", "body": body},
                           {"session_key": "memdup"})
    check("the first add lands", first.startswith("OK"), first[:120])
    again = fb.tool_memory({"action": "add", "title": "Drive detection notes", "body": body},
                           {"session_key": "memdup"})
    check("a restatement under a new title is REFUSED with the id to update",
          again.startswith("REFUSED") and "dvd-drive-detection" in again, again[:200])
    check("...and the refusal names both ways out",
          "supersedes" in again and "update" in again, again[:200])
    nearby = fb.tool_memory({"action": "add", "title": "Mail queue depth",
                             "body": "The mail queue depth is checked with mailq and the "
                                     "spool drains every five minutes on the relay.\n"},
                            {"session_key": "memdup"})
    check("a genuinely different fact is not caught by the duplicate rule",
          nearby.startswith("OK"), nearby[:120])
    new = ("The optical drive test is a non-empty `drutil status` output; system_profiler "
           "intermittently prints nothing even with the drive attached, so use drutil "
           "first. macOS has no setsid; use --only NAME, the ONLY env var is reset.\n")
    sup = fb.tool_memory({"action": "add", "title": "DVD drive detection (drutil)",
                          "body": new, "supersedes": "dvd-drive-detection"},
                         {"session_key": "memdup"})
    check("a superseding add lands and says what it replaced",
          sup.startswith("OK") and "supersedes memory/dvd-drive-detection" in sup, sup[:240])
    old = fb._memory_find("dvd-drive-detection")
    check("the replaced concept is deprecated, not piled up",
          old["fm"].get("status") == "deprecated", old["fm"].get("status"))
    made = fb._memory_find("dvd-drive-detection-drutil")
    check("...and the new one names it",
          made["fm"].get("supersedes") == "dvd-drive-detection",
          made["fm"].get("supersedes"))
    check("an unknown supersedes id names the miss",
          "no such concept" in fb.tool_memory(
              {"action": "add", "title": "Zed fact", "body": "a zed fact about zed",
               "supersedes": "nope"}, {"session_key": "memdup"}))
    index = fb.memory_index_render()
    check("the index shows the replacement story",
          "(deprecated, unverified)" in index, index)


def test_nothing_rides_as_permanently_true():
    """The tier is stated even when it is the default: an unstated stance reads as
    authority, and the index is the only part of memory the model actually sees."""
    _fresh()
    fb.memory_new_concept("A plain fact", "something plain about this box")
    index = fb.memory_index_render()
    check("an unverified concept SAYS so in the index",
          "unverified" in index, index)
    fm = {"type": "Fact", "title": "Checked", "verified": [{"by": "human:operator"}]}
    fb.MEMORY_DIR.joinpath("checked.md").write_text(
        fb.okf_dump(fm, "a body"), encoding="utf-8")
    index = fb.memory_index_render()
    check("a human-reviewed one says that instead",
          "human-reviewed" in index and "checked.md" in index, index)


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
    print(f"\n{len(PASSES)} passed, {len(FAILURES)} failed")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())

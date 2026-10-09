"""test_memory_surface - one merged suite (test_memory_okf, test_memory_prompts).

They grade the same surface and ran as separate gate processes before; each
member keeps its body, its check() and its summary, and the runner below
restores the environment, the cwd and sys.path between members. Zero checks
were deleted: test_memory_okf: globals()-> _ns.
"""
import os
import sys


def _run(name, fn):
    """One member, its own snapshot: env, cwd and sys.path restored afterwards."""
    saved_env = dict(os.environ)
    saved_cwd = os.getcwd()
    saved_path = list(sys.path)
    print("== member %s: start" % name)
    try:
        rc = fn()
    except SystemExit as exc:
        rc = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    finally:
        os.environ.clear()
        os.environ.update(saved_env)
        try:
            os.chdir(saved_cwd)
        except OSError:
            pass
        sys.path[:] = saved_path
    rc = int(rc or 0)
    print("== member %s: exit %d" % (name, rc))
    return rc


def _suite_test_memory_okf():
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


    def _call_tool(args, ctx=None):
        """tool_memory, with a raise turned into a value: a RED check, not a traceback."""
        try:
            return fb.tool_memory(args, ctx or {})
        except Exception as e:                                            # noqa: BLE001
            return "RAISED %s: %s" % (type(e).__name__, e)


    def _tier(fm):
        """_okf_tier, likewise: a raise grades as a FAIL rather than crashing the suite."""
        try:
            return fb._okf_tier(fm)
        except Exception as e:                                            # noqa: BLE001
            return "RAISED %s: %s" % (type(e).__name__, e)


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
                 # Backslashes on every rewrite.
                 r"C:\Users\Example User\tinycmdr", 'say "hi"', r"two\\pairs\\here"]
        for value in risky:
            text = fb.okf_dump({"type": "Fact", "title": value}, "")
            got, _ = fb.okf_parse(text)
            check(f"a scalar round-trips: {value!r}", got.get("title") == value,
                  repr(got.get("title")))
        tricky = r"C:\Users\Example User"
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
        # The compare used to be lexical, so a non-zero-padded date read
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


    def test_update_preserves_unknown_keys_and_keeps_generated():
        """A touch is not a re-derivation: `generated` is the date the CONTENT was produced,
    so an update keeps it and records itself in `touched` instead. Re-dating generated on
    every update made the provenance claim the content was freshly derived (2026-10-05)."""
        _fresh()
        made = fb.memory_new_concept("A fact", "old body")
        path = made["path"]
        fm, body = fb.okf_parse(path.read_text(encoding="utf-8"))
        fm["x_kept"] = "yes"
        fm["generated"]["at"] = "2000-01-01T00:00:00Z"
        path.write_text(fb.okf_dump(fm, body), encoding="utf-8")
        fb.memory_update_concept(made["id"], body="new body", description="now")
        fm2, body2 = fb.okf_parse(path.read_text(encoding="utf-8"))
        check("an update rewrites the body", body2.strip() == "new body", repr(body2))
        check("an update keeps unknown keys", fm2.get("x_kept") == "yes", repr(fm2))
        check("an update KEEPS generated (the derivation date did not move)",
              fm2["generated"]["at"] == "2000-01-01T00:00:00Z", repr(fm2.get("generated")))
        touched = fm2.get("touched") or {}
        check("...and says it was touched, with an actor and an instant",
              str(touched.get("by") or "").startswith("tinycmdr/")
              and str(touched.get("at") or "").endswith("Z"), repr(touched))
        fb.memory_set_status(made["id"], "deprecated", reason="gone")
        fm3, _ = fb.okf_parse(path.read_text(encoding="utf-8"))
        check("a status change keeps generated too",
              fm3["generated"]["at"] == "2000-01-01T00:00:00Z" and "touched" in fm3, repr(fm3))
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
        """An over-budget index is cut on a line boundary and NAMES the sections it lost.
    The index is grouped by type, so a blind head-cut dropped whole
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


    def test_the_prompt_index_renders_at_read_time_so_staleness_is_never_frozen():
        """A-2026-10-08-142: index.md only moves when a mutating verb runs, so a concept
    whose stale_after instant passed while the bundle was otherwise quiet kept riding
    every prompt with no (stale) flag. The prompt renders the index from the concepts;
    the file stays the mutation-written browse copy."""
        _fresh()
        fb.memory_new_concept("A dated fact", "true as of when it was written",
                              stale_after="2999-01-01T00:00:00Z")
        fb.memory_index_update()
        # Time passes with no further mutation: the instant lands in the past on the concept
        # while index.md still carries the render from when it was fresh.
        fb.memory_update_concept("a-dated-fact", stale_after="2001-01-01T00:00:00Z")
        on_disk = fb.MEMORY_INDEX.read_text(encoding="utf-8")
        check("the file still says what the last mutation rendered (it is not rewritten by reads)",
              re.search(r"\([^)]*\bstale\b[^)]*\)", on_disk) is None, on_disk)
        block = fb.volatile_context(session_key="stale-probe")
        check("the prompt's index flags the concept stale on the first read after the instant",
              re.search(r"\[A dated fact\]\(a-dated-fact\.md\)[^\n]*\([^)]*\bstale\b",
                        block) is not None, block[-300:])


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


    def test_add_scrubs_description_tags_and_sources():
        """A-2026-10-08-145: only title and body went through the scrubber, so a secret in
    the added description, a tag or a source landed in the concept file and in the
    prompt-carried index."""
        _fresh()
        secret = "hunter2-memdesc"
        fb._SECRETS.add(secret)
        try:
            out = fb.tool_memory({"action": "add", "title": "Scrub probe",
                                  "body": "a fact about the probe",
                                  "description": "the value is " + secret,
                                  "tags": ["probe", secret],
                                  "sources": ["measured with " + secret]},
                                 {"session_key": "mem-scrub"})
            check("the add lands", out.startswith("OK"), out[:160])
            c = fb._memory_find("scrub-probe")
            blob = c["path"].read_text(encoding="utf-8") if c else ""
            check("the concept file carries no secret in any field",
                  secret not in blob and "«redacted»" in blob, blob[:300])
            index = fb.MEMORY_INDEX.read_text(encoding="utf-8")
            check("...and neither does the prompt's index",
                  secret not in index and "«redacted»" in index, index)
        finally:
            fb._SECRETS.discard(secret)


    def test_add_refuses_a_title_another_concept_holds():
        """A-2026-10-08-151: the add door checked only the concept AT THE SLUG, so a title
    held at another id (a renamed concept, a '-2' slug, a foreign file) could be taken
    by a second concept - the update path already checks every concept."""
        _fresh()
        (fb.MEMORY_DIR / "foreign-stem.md").write_text(
            fb.okf_dump({"type": "Fact", "title": "Shared title"}, "a foreign body"),
            encoding="utf-8")
        out = _call_tool({"action": "add", "title": "Shared title",
                          "body": "a second concept claiming the same title"},
                         {"session_key": "mem-dup"})
        check("the add is refused and names the holder",
              out.startswith("ERROR") and "foreign-stem" in out, out[:200])
        check("...and nothing was written",
              fb._memory_find("shared-title") is None, "a second concept landed")


    def test_a_failed_forget_is_reported_not_answered_ok():
        """A-2026-10-08-144: tool_memory ignored memory_forget's None, so a file held open
    (PermissionError on Windows, a permissions problem anywhere) was announced as
    forgotten while it stayed on disk and in the index. A directory with no write
    permission is the POSIX way to make unlink fail."""
        if not hasattr(os, "geteuid") or os.geteuid() == 0:
            print("skip a failed forget needs POSIX directory permissions to stage")
            return
        _fresh()
        fb.memory_new_concept("Held open", "a fact that cannot be deleted")
        _call_tool({"action": "list"}, {})          # create the lock inside the dir
        made = fb._memory_find("held-open")
        check("the concept exists before the attempt", made is not None)
        mode = fb.MEMORY_DIR.stat().st_mode & 0o777
        try:
            os.chmod(fb.MEMORY_DIR, 0o500)          # unlink needs write on the DIRECTORY
            out = _call_tool({"action": "forget", "id": "held-open"},
                             {"session_key": "mem-forget"})
        finally:
            os.chmod(fb.MEMORY_DIR, mode)
        check("a forget that could not delete says ERROR, not OK",
              out.startswith("ERROR"), out[:200])
        check("...naming the still-present file",
              made["path"].exists(), "the file is gone")


    def test_a_superseding_add_re_renders_the_index_after_the_deprecation():
        """A-2026-10-08-143: the add path rendered index.md BEFORE deprecating the concept it
    supersedes, so the browse copy kept the old concept unflagged next to its replacement
    until some later mutation happened to re-render. The tool's own report renders fresh,
    so the reply looked right while the artifact did not."""
        _fresh()
        ok = fb.tool_memory({"action": "add", "title": "Ports in use",
                             "body": "The web UI listens on the configured port only."},
                            {"session_key": "mem-sup"})
        check("the first add lands", ok.startswith("OK"), ok[:120])
        sup = fb.tool_memory({"action": "add", "title": "Ports in use (v2)",
                              "body": "The web UI listens on 8787 unless web.port says "
                                      "otherwise; the old port is free afterwards.",
                              "supersedes": "ports-in-use"}, {"session_key": "mem-sup"})
        check("the superseding add lands", sup.startswith("OK"), sup[:160])
        on_disk = fb.MEMORY_INDEX.read_text(encoding="utf-8")
        check("index.md flags the superseded concept deprecated in the same mutation",
              re.search(r"\[Ports in use\]\(ports-in-use\.md\)[^\n]*\([^)]*\bdeprecated\b",
                        on_disk) is not None, on_disk)
        check("...and the prompt's index says so too",
              re.search(r"\[Ports in use\]\(ports-in-use\.md\)[^\n]*\([^)]*\bdeprecated\b",
                        fb.volatile_context(session_key="mem-sup")) is not None)


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


    def test_the_volatile_block_is_scrubbed():
        """A secret on disk (notes, the bundle) must not ride the prompt in the clear."""
        _fresh()
        secret = "hunter2-volatile-secret"
        fb.NOTES_FILE.write_text("context: " + secret, encoding="utf-8")
        fb._SECRETS.add(secret)
        try:
            block = fb.volatile_context(session_key="scrub-probe")
            check("a secret in notes never rides the prompt block in the clear",
                  secret not in block and "«redacted»" in block, block[:200])
        finally:
            fb._SECRETS.discard(secret)
            fb.NOTES_FILE.unlink(missing_ok=True)


    def test_search_reads_words_not_a_contiguous_string():
        """A prose query matches when every word is somewhere in the concept."""
        _fresh()
        fb.tool_memory({"action": "add",
                        "title": "Audit share: repo path and rotation",
                        "body": "The report share holds the audit files and rotates them "
                                "on a schedule."}, {})
        out = fb.tool_memory({"action": "search", "query": "audit share rotation"}, {})
        check("a prose query matches with the words anywhere in the concept",
              "1 match" in out and "Audit share" in out, out[:160])
        out = fb.tool_memory({"action": "search", "query": "audit conceptmissing"}, {})
        check("...and a query with a word the concept lacks still misses",
              "no concept matches" in out, out[:120])


    def test_a_bare_string_verifier_entry_reads_as_a_verifier():
        """`verified:\\n- human:operator` is natural YAML shorthand for {by: human:operator},
    but a string entry has no `.by`: the tier was lost and the index raised
    AttributeError (2026-10-05)."""
        _fresh()
        check("a list of bare strings names the verifier",
              _tier({"verified": ["human:operator"]}) == "human-reviewed",
              _tier({"verified": ["human:operator"]}))
        check("a bare string is the same shorthand",
              _tier({"verified": "human:operator"}) == "human-reviewed",
              _tier({"verified": "human:operator"}))
        check("a machine string is machine-confirmed, not unverified",
              _tier({"verified": ["process:disk-check"]}) == "machine-confirmed",
              _tier({"verified": ["process:disk-check"]}))
        check("a mixed list still finds the human",
              _tier({"verified": ["process:x", "human:operator"]}) == "human-reviewed",
              _tier({"verified": ["process:x", "human:operator"]}))
        fm, _ = fb.okf_parse(fb.okf_dump(
            {"type": "Fact", "title": "T", "verified": ["human:operator"]}, ""))
        check("the shorthand survives dump/parse",
              _tier(fm) == "human-reviewed", repr(fm.get("verified")))
        (fb.MEMORY_DIR / "checked-string.md").write_text(fb.okf_dump(
            {"type": "Fact", "title": "Checked", "verified": ["human:operator"]}, "b"),
            encoding="utf-8")
        try:
            index = fb.memory_index_render()
        except Exception as e:                                           # noqa: BLE001
            index = "RAISED %s: %s" % (type(e).__name__, e)
        check("...so the index says human-reviewed without raising",
              "human-reviewed" in index, index[-200:])


    def test_a_caller_description_is_bounded_like_the_generator():
        """The index is the whole prompt: a caller-supplied description is bounded at the
    generator's bound, and the result says when it was cut (a 3,000-char description was
    accepted verbatim and evicted the rest of the index, 2026-10-05)."""
        _fresh()
        bound = getattr(fb, "_MEMORY_DESCRIPTION_MAX", 160)
        out = _call_tool({"action": "add", "title": "Verbose concept",
                          "body": "the body of the concept stands here",
                          "description": "This is the point, " * 200})
        check("an over-long description is accepted", out.startswith("OK"), out[:120])
        c = fb._memory_find("verbose-concept")
        desc = str((c["fm"].get("description") if c else "") or "")
        check("the stored description is within the generator's bound",
              bool(desc) and len(desc) <= bound + 1, len(desc))
        check("...cut at a word boundary with the ellipsis saying so",
              desc.endswith("…"), desc[-20:])
        check("...and the result says it was cut",
              "cut" in out and str(bound) in out, out[:260])
        out2 = _call_tool({"action": "add", "title": "Terse concept",
                           "body": "another body stands here", "description": "short point"})
        c2 = fb._memory_find("terse-concept")
        check("a description that fits is kept verbatim",
              out2.startswith("OK") and bool(c2)
              and c2["fm"].get("description") == "short point",
              repr(c2["fm"].get("description") if c2 else out2[:120]))
        big = {"action": "update", "id": "terse-concept", "description": "x " * 300}
        out3 = _call_tool(big)
        c3 = fb._memory_find("terse-concept")
        check("an update bounds a caller description too, and says so",
              out3.startswith("OK") and "cut" in out3
              and len(str(c3["fm"].get("description") or "")) <= bound + 1, out3[:200])


    def test_tags_take_one_string_or_a_list_and_refuse_the_rest():
        """`tags: 7` raised TypeError out of the tool; `tags: "oneshot"` became ten
    one-character tags (2026-10-05). A string is ONE tag; a list of strings is the list;
    anything else is an ERROR."""
        _fresh()
        out = _call_tool({"action": "add", "title": "One tag", "body": "a body stands here",
                          "tags": "oneshot"})
        c = fb._memory_find("one-tag")
        check("a string tag is ONE tag, not its characters",
              out.startswith("OK") and bool(c) and c["fm"].get("tags") == ["oneshot"],
              repr(c["fm"].get("tags") if c else out[:120]))
        out2 = _call_tool({"action": "add", "title": "Number tag", "body": "a body stands here",
                           "tags": 7})
        check("a non-string, non-list tags value is refused as an ERROR",
              out2.startswith("ERROR"), out2[:160])
        out3 = _call_tool({"action": "add", "title": "Mixed tag", "body": "a body stands here",
                           "tags": ["ok", 7]})
        check("a list holding a non-string is refused too", out3.startswith("ERROR"), out3[:160])
        out4 = _call_tool({"action": "add", "title": "List tag", "body": "a body stands here",
                           "tags": ["host", "disk"]})
        c4 = fb._memory_find("list-tag")
        check("a list of strings still works",
              out4.startswith("OK") and bool(c4)
              and c4["fm"].get("tags") == ["host", "disk"],
              repr(c4["fm"].get("tags") if c4 else out4[:120]))


    def test_update_refuses_a_title_another_concept_holds():
        """Uniqueness was enforced on add and not on update, so two concepts could share a
    title and the title-ordered index grew an ambiguous pair (2026-10-05)."""
        _fresh()
        fb.memory_new_concept("Alpha fact", "the alpha body")
        fb.memory_new_concept("Beta fact", "the beta body")
        raised = None
        try:
            fb.memory_update_concept("beta-fact", title="Alpha fact")
        except ValueError as e:
            raised = str(e)
        check("an update to a title another concept holds is refused, naming it",
              raised is not None and "alpha-fact" in raised, repr(raised))
        c = fb._memory_find("beta-fact")
        check("...and the refused update changed nothing",
              c["fm"].get("title") == "Beta fact", repr(c["fm"].get("title")))
        out = _call_tool({"action": "update", "id": "beta-fact", "title": "Alpha fact"})
        check("the tool reports it as an ERROR",
              out.startswith("ERROR") and "alpha-fact" in out, out[:200])
        check("a title unchanged by the update is not a collision",
              _call_tool({"action": "update", "id": "beta-fact", "title": "Beta fact",
                          "body": "a rewritten beta body"}).startswith("OK"))


    def test_the_log_rotates_to_exactly_one_predecessor():
        """log.md was append-only for ever (2,265 B after ~20 mutations, nothing pruned). The
    bound is one predecessor - log.md -> log.md.1 - the shape the session event ledger
    uses, so a bundle costs at most two log files."""
        _fresh()
        had = hasattr(fb, "_MEMORY_LOG_MAX_BYTES")
        saved = getattr(fb, "_MEMORY_LOG_MAX_BYTES", None)
        fb._MEMORY_LOG_MAX_BYTES = 200
        try:
            for i in range(40):
                fb._memory_log_append("Update", "[One](one.md) - change number %d" % i)
        finally:
            if had:
                fb._MEMORY_LOG_MAX_BYTES = saved
            else:
                del fb._MEMORY_LOG_MAX_BYTES
        rolled = fb.MEMORY_DIR / "log.md.1"
        check("an oversized log rolls to exactly one predecessor", rolled.exists(),
              sorted(p.name for p in fb.MEMORY_DIR.glob("log.md*")))
        log = fb.MEMORY_LOG.read_text(encoding="utf-8")
        check("the live log opens with its title and keeps the newest entry",
              log.startswith("# Memory log") and "change number 39" in log, log[:200])
        old = rolled.read_text(encoding="utf-8") if rolled.exists() else ""
        check("the predecessor holds the entries that were live before the roll",
              old.startswith("# Memory log") and "change number 39" not in old, old[:200])
        check("...and there is no second predecessor",
              sorted(p.name for p in fb.MEMORY_DIR.glob("log.md*")) == ["log.md", "log.md.1"],
              sorted(p.name for p in fb.MEMORY_DIR.glob("log.md*")))


    def test_an_unknown_type_is_refused_by_name():
        """`type: 5` wrote `type: 5` and opened an index section `# 5` (2026-10-05). The
    vocabulary is the bundle's known types; anything else is refused by name."""
        _fresh()
        out = _call_tool({"action": "add", "title": "Numeric type",
                          "body": "a body stands here", "type": 5})
        check("an unknown type is refused as an ERROR", out.startswith("ERROR"), out[:160])
        check("...naming the offending type and the known ones",
              "'5'" in out and "Fact" in out and "Runbook" in out, out[:200])
        check("nothing was written", fb.memory_scan() == [], fb.memory_scan())
        check("a known type still lands",
              _call_tool({"action": "add", "title": "Proper type",
                          "body": "another body stands here",
                          "type": "Runbook"}).startswith("OK"))


    def test_a_non_dict_config_section_never_replaces_the_default():
        """`"agent": null` in config.json replaced the shipped dict, so every reader -
    memory action=list, visible_tool_names, the guard merge itself - raised
    AttributeError (2026-10-05). A load-time guard keeps the default and names the key."""
        import json as _json
        cfg_path = TMP / "config-guard.json"
        cfg_path.write_text(_json.dumps({"agent": None, "llm": "nope"}), encoding="utf-8")
        saved_path, saved_cfg = fb.CONFIG_PATH, fb.CONFIG
        try:
            fb.CONFIG_PATH = cfg_path
            try:
                cfg = fb.load_config()
            except Exception as e:                                        # noqa: BLE001
                cfg = {"agent": "RAISED %s: %s" % (type(e).__name__, e)}
        finally:
            fb.CONFIG_PATH = saved_path
        check("a null section keeps the shipped dict", isinstance(cfg.get("agent"), dict),
              repr(cfg.get("agent"))[:120])
        check("...with its shipped keys intact",
              isinstance(cfg.get("agent"), dict)
              and "memory_concept_max_chars" in cfg["agent"], repr(cfg.get("agent"))[:120])
        check("a string section keeps the shipped dict too",
              isinstance(cfg.get("llm"), dict), repr(cfg.get("llm"))[:80])
        fb.CONFIG = cfg
        try:
            try:
                out = fb.tool_memory({"action": "list"}, {})
            except Exception as e:                                        # noqa: BLE001
                out = "RAISED %s: %s" % (type(e).__name__, e)
            try:
                names = fb.visible_tool_names()
            except Exception as e:                                        # noqa: BLE001
                names = "RAISED %s: %s" % (type(e).__name__, e)
        finally:
            fb.CONFIG = saved_cfg
        check("memory action=list survives a null agent section",
              isinstance(out, str) and not out.startswith(("RAISED", "ERROR")), out[:120])
        check("visible_tool_names survives too",
              isinstance(names, set) and len(names) > 0, repr(names)[:120])


    def test_stale_after_must_be_an_iso_instant_at_write_time():
        """A value the staleness compare cannot read used to be accepted at write time
    (`stale_after: "tomorrow"`, `20200101`) and then silently never fired - the concept
    read fresh for ever (2026-10-05). Validate at the door and name the shape."""
        _fresh()
        out = _call_tool({"action": "add", "title": "Stale tomorrow",
                          "body": "a body stands here", "stale_after": "tomorrow"})
        check("a non-ISO stale_after is refused at write time", out.startswith("ERROR"), out[:160])
        check("...and the accepted shape is named",
              "ISO" in out and "2026-11-01" in out, out[:200])
        fb.memory_new_concept("Upd stale", "a body about the update")
        out2 = _call_tool({"action": "update", "id": "upd-stale", "stale_after": "20200101"})
        check("an unreadable stale_after on update is refused too",
              out2.startswith("ERROR"), out2[:160])
        out3 = _call_tool({"action": "add", "title": "Stale good",
                           "body": "another body stands here",
                           "stale_after": "2026-11-01T00:00:00Z"})
        check("a real ISO instant is accepted", out3.startswith("OK"), out3[:120])
        out4 = _call_tool({"action": "add", "title": "Stale date",
                           "body": "yet another body stands here",
                           "stale_after": "2026-11-01"})
        check("a real ISO date is accepted too", out4.startswith("OK"), out4[:120])


    def main():
        tests = [v for k, v in sorted(_ns.items()) if k.startswith("test_")]
        for fn in tests:
            fn()
        print(f"\n{len(PASSES)} passed, {len(FAILURES)} failed")
        return 1 if FAILURES else 0
    _ns = dict(locals())
    return main()


def _suite_test_memory_prompts():
    """Memory promotion: event-driven, dismissible, and never a nag.

was made at WRITE TIME - the moment a fact is freshest, which correlates with the effort
just spent, not with future value - and only for an order whose wording reads as a
"where is X" question. So the publishable got saved and the load-bearing did not, and
nothing anywhere ever said so. The evidence a fact IS durable is that it cost a lookup
AGAIN, so the offer is event-driven instead: a run that spent hand-driven calls and saved
nothing is asked once per session, the ask is SAVE OR DISMISS, and a dismissal is
remembered for that shape (the order's own words, the census's overlap rule) so it is
never repeated. A bare "dismiss" is a control order: the harness records it and answers
it without a model call.

    python tests/test_memory_surface.py
"""
    import importlib.util
    import json
    import os
    import shutil
    import sys
    import tempfile
    from pathlib import Path

    BASE = Path(__file__).resolve().parent.parent
    SRC = BASE / (os.environ.get("TINYCMDR_TEST_APP")
                  or os.environ.get("TINYCMDR_SRC") or "tinycmdr.py")
    # A STAGED copy: the module writes into BASE_DIR while it is being imported.
    STAGE = Path(tempfile.gettempdir()) / "tinycmdr-test-stage-memprompts"
    STAGE.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SRC, STAGE / "tinycmdr.py")
    spec = importlib.util.spec_from_file_location("tinycmdr_memprompts_under_test",
                                                  STAGE / "tinycmdr.py")
    fb = importlib.util.module_from_spec(spec)
    sys.modules["tinycmdr_memprompts_under_test"] = fb
    spec.loader.exec_module(fb)

    TMP = Path(tempfile.mkdtemp(prefix="fbmemprompts-"))
    sys.path.insert(0, str(BASE / "tests"))
    import hermetic                                                          # noqa: E402
    hermetic.redirect_repo_files(fb, TMP)
    fb._CENSUS_FORCE = True            # this suite is the census's test, not the running bot

    PASSES, FAILS = [], []


    def check(name, cond, detail=""):
        (PASSES if cond else FAILS).append(name)
        print(("ok   " if cond else "FAIL ") + name + ("" if cond else "   " + str(detail)))


    class _Rep:
        def __init__(self):
            self.lines = []

        def say(self, text):
            self.lines.append(text)


    class _FakeResp:
        def __init__(self, data):
            self._data = data

        def raise_for_status(self):
            return None

        def json(self):
            return self._data


    def text_reply(text):
        return {"choices": [{"message": {"role": "assistant", "content": text},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2}}


    def install_stub(fb, seen):
        def fake_post(url, headers, payload, timeout, grace, cancel_event=None, stream=False):
            seen.append(json.loads(json.dumps(payload)))
            return _FakeResp(text_reply("(the model answered)"))
        fb._post_watchdog = fake_post


    def _seed(session, words, calls=5, **extra):
        st = fb.run_state(session, create=True)
        st["calls_by"] = {"shell": calls}
        st["hand_at_start"] = 0
        st["order_words"] = words
        st["order_sample"] = " ".join(words)
        st.update(extra)
        return st


    SHAPE = ["burn", "the", "dvd", "and", "verify", "it"]
    FRESH = ["water", "the", "ferns", "twice", "weekly"]
    FRESH2 = ["oil", "the", "gate", "hinges", "yearly"]
    FRESH3 = ["sweep", "the", "chimney", "in", "spring"]
    OTHER = ["drain", "the", "mail", "queue", "and", "report"]
    RUN_SHAPE = ["encode", "the", "dvd", "title", "set", "now"]

    # getattr so a build WITHOUT the promotion path fails these checks instead of crashing the
    # suite: falsification is a FAIL summary, not a traceback (test_route_hint's rule).
    remember_offer = getattr(fb, "remember_offer", lambda *a, **k: "")
    remember_dismiss_order = getattr(fb, "remember_dismiss_order", lambda *a, **k: "")
    offer_after_run = getattr(fb, "offer_after_run", lambda *a, **k: "")

    # ---- the offer is EVENT-driven ---------------------------------------------------------
    _rep = _Rep()
    _seed("mp-1", SHAPE)
    line = remember_offer("mp-1", _rep, source="main", trigger="event")
    check("a run that spent hand calls and saved nothing is asked to save or dismiss",
          "save it" in line and "dismiss" in line, line[:200])
    check("...and the operator saw exactly one line", _rep.lines == [line], _rep.lines)
    check("...once per session", remember_offer("mp-1", _rep, source="main", trigger="event") == "")
    _seed("mp-2", SHAPE, calls=9, remembered=1)
    check("nothing is asked of a run that saved something",
          remember_offer("mp-2", None, source="main", trigger="event") == "")
    _seed("mp-3", SHAPE, calls=9)
    check("a sub-agent's run never gets the offer",
          remember_offer("mp-3", None, source="child") == "")
    _seed("mp-4", SHAPE, calls=1)
    check("a run under the threshold is not asked",
          remember_offer("mp-4", None, source="main", trigger="event") == "")
    _seed("mp-5", ["which", "port", "the", "web", "ui", "listens"], calls=2, order_is_lookup=1)
    check("...but a small run that ANSWERED a lookup still is (the old trigger kept)",
          "save it" in remember_offer("mp-5", None, source="main"))
    check("the offer is a config lever",
          fb.DEFAULT_CONFIG["agent"].get("remember_offer") is True
          and int(fb.DEFAULT_CONFIG["agent"].get("remember_offer_steps")) == 4)

    # ---- the dismissal sticks -------------------------------------------------------------
    ans = remember_dismiss_order("mp-1", "dismiss")
    check("a bare dismiss is recorded and answered", "won't offer" in ans, ans[:120])
    check("...and only the bare word is a dismissal",
          remember_dismiss_order("mp-1", "dismiss the janitor at noon") == "")
    _seed("mp-6", SHAPE, calls=9)
    check("the dismissed shape is never offered again, in a later session",
          remember_offer("mp-6", None, source="main", trigger="event") == "")
    _seed("mp-7", OTHER, calls=9)
    check("a different shape is still offered",
          "save it" in remember_offer("mp-7", None, source="main", trigger="event"))

    # ---- the run answers a dismissal without a model call ---------------------------------
    seen = []
    install_stub(fb, seen)
    _seed("mp-run", RUN_SHAPE)
    check("the offer is pending for the run's session",
          bool(remember_offer("mp-run", _Rep(), source="main", trigger="event")))
    got = fb.AGENT.run("mp-run", "dismiss")
    check("AGENT.run answers a pending dismiss with the harness line",
          "won't offer" in got, got[:160])
    check("...and made NO model call for it", seen == [], len(seen))
    got2 = fb.AGENT.run("mp-nobody", "dismiss")
    check("with no pending offer the word is an ordinary order (the model is asked)",
          seen != [], (got2[:80], len(seen)))

    # ---- one offer per run, in priority order ---------------------------------------------
    _seed("mp-pri", ["burn", "the", "dvd", "and", "verify", "it"], calls=9, order_repeats=3)
    _pri = offer_after_run("mp-pri", _Rep(), source="main")
    check("a repeated ORDER still gets the mint offer, never the memory ask",
          "mint it" in _pri and "save it" not in _pri, _pri[:180])
    _seed("mp-pri2", OTHER, calls=9)
    _pri2 = offer_after_run("mp-pri2", _Rep(), source="main")
    check("...while an ordinary long run gets the save-or-dismiss ask",
          "save it" in _pri2 and "dismiss" in _pri2, _pri2[:180])

    # ---- A-2026-10-08-147: an offer is answered by the message that FOLLOWS it -------------
    # (a shape of its own: the suite dismissed SHAPE and OTHER earlier)
    _seed("mp-stale", FRESH, calls=9)
    check("offer posted before the staleness test",
          bool(remember_offer("mp-stale", None, source="main", trigger="event")))
    _data = json.loads(fb.PROC_CENSUS_FILE.read_text(encoding="utf-8"))
    for _o in _data.get("__remember_offers__", []):
        if _o.get("session") == "mp-stale":
            _o["at"] = "2020-01-01 00:00"
    fb.PROC_CENSUS_FILE.write_text(json.dumps(_data, indent=1, sort_keys=True), encoding="utf-8")
    check("a days-old offer does not swallow a bare 'no thanks'",
          remember_dismiss_order("mp-stale", "no thanks") == "")
    check("...and the stale record is dropped, not left to nag",
          all(o.get("session") != "mp-stale"
              for o in json.loads(fb.PROC_CENSUS_FILE.read_text(encoding="utf-8"))
              .get("__remember_offers__", [])))
    _seed("mp-save", FRESH2, calls=9)
    check("offer posted before the save answer",
          bool(remember_offer("mp-save", None, source="main", trigger="event")))
    _save_order = getattr(fb, "remember_save_order", lambda *a, **k: False)
    check("a bare 'save it' answers the offer's record (the model still saves)",
          _save_order("mp-save", "save it") is True)
    check("...so a later 'no thanks' in that session is the model's",
          remember_dismiss_order("mp-save", "no thanks") == "")
    _seed("mp-reset", FRESH3, calls=9)
    check("offer posted before /new",
          bool(remember_offer("mp-reset", None, source="main", trigger="event")))
    fb.AGENT.reset("mp-reset")
    check("...and /new takes the pending record with the conversation",
          remember_dismiss_order("mp-reset", "no thanks") == "")

    # ---- A-2026-10-08-148: the repeat offer is throttled, and counts THIS run ---------------
    _cen = (json.loads(fb.PROC_CENSUS_FILE.read_text(encoding="utf-8"))
            if fb.PROC_CENSUS_FILE.exists() else {})
    _cen.setdefault("__orders__", []).append({"words": ["burn", "dvd"], "count": 3,
                                              "last": "2026-10-08 10:00"})
    fb.PROC_CENSUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    fb.PROC_CENSUS_FILE.write_text(json.dumps(_cen, indent=1, sort_keys=True), encoding="utf-8")
    _seed("mp-rep", SHAPE, calls=9, order_repeats=3, order_words=["burn", "dvd"])
    fb.run_state("mp-rep", create=True)["hand_at_start"] = 5   # 4 of the session's 9 calls
    _line = fb.mint_offer("mp-rep", _Rep())
    check("the repeat offer posts with THIS run's hand count",
          "run #3" in _line and "4 hand" in _line, _line[:200])
    check("...and is throttled for a week, not posted on every run",
          fb.mint_offer("mp-rep", _Rep()) == "")
    _seed("mp-zero", SHAPE, calls=9, order_repeats=3, order_words=["burn", "dvd"])
    fb.run_state("mp-zero", create=True)["hand_at_start"] = 9
    check("a run that made no hand calls is not offered",
          fb.mint_offer("mp-zero", _Rep()) == "")

    # ---- A-2026-10-08-149: a damaged census is kept; the keys are bounded -------------------
    _saved = (fb.PROC_CENSUS_FILE.read_text(encoding="utf-8")
              if fb.PROC_CENSUS_FILE.exists() else None)
    try:
        _dam = fb.PROC_CENSUS_FILE.parent / (fb.PROC_CENSUS_FILE.name + ".damaged")
        _dam.unlink(missing_ok=True)
        fb.PROC_CENSUS_FILE.write_text("{not json", encoding="utf-8")
        check("a damaged census loads as an empty map", fb._census_load() == {})
        check("...and is KEPT as one .damaged copy", _dam.exists())
    finally:
        if _saved is not None:
            fb.PROC_CENSUS_FILE.write_text(_saved, encoding="utf-8")
    _max = getattr(fb, "_CENSUS_SIG_MAX", 120)
    _big = {("sig-%03d" % i): {"runs": [], "offered": "", "minted": "",
                               "last": "2026-01-%02d 00:00" % (i % 28)}
            for i in range(_max + 5)}
    fb.PROC_CENSUS_FILE.write_text(json.dumps(_big, indent=1, sort_keys=True), encoding="utf-8")
    fb.procedure_census_bump("shell", {"command": "Get-PSDrive | Select-Object -First 5"})
    _after = fb._census_load()
    _sigs = [k for k in _after if not k.startswith("__")]
    check("the census prunes its signature keys to the cap", len(_sigs) <= _max, len(_sigs))

    print(f"\n{len(PASSES)} passed, {len(FAILS)} failed")
    sys.exit(1 if FAILS else 0)


def main():
    rc = 0
    for name, fn in (("test_memory_okf", _suite_test_memory_okf), ("test_memory_prompts", _suite_test_memory_prompts)):
        rc |= _run(name, fn)
    return 1 if rc else 0


if __name__ == "__main__":
    sys.exit(main())

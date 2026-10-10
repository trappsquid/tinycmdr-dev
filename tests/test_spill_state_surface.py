"""test_spill_state_surface - one merged suite (test_spill, test_spill_durability, test_compaction_continuity).

They grade the same surface and ran as separate gate processes before; each
member keeps its body, its check() and its summary, and the runner below
restores the environment, the cwd and sys.path between members. Zero checks
were deleted: no member needed a namespace rewrite.
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


def _suite_test_spill():
    """Over-cap tool results are SPILLED, not shredded (2026-09-18).

Proven on Windows by the harness's own analysis: a 30,045-char tool result lost ~20,100
middle characters to truncate_middle, and re-issuing the call with `raw=true` lost the same
middle (raw bypasses digestion, not the cap). These checks pin the replacement: the whole text
lands on disk, the prompt gets both ends plus a pointer that works, and a spill that cannot be
written degrades to the old truncation instead of breaking the run. What lands at rest is
the masked copy, in an owner-only folder (A-2026-10-10-07).
"""
    import json
    import os
    import re
    import shutil
    import stat
    import sys
    import tempfile
    import time
    from pathlib import Path

    BASE = Path(__file__).resolve().parent.parent
    TESTS = BASE / "tests"
    sys.path.insert(0, str(TESTS))

    import run_scenario  # noqa: E402

    FAILS = []
    PASSES = []


    def check(cond, what):
        if cond:
            PASSES.append(what)
            print(f"ok   {what}")
        else:
            FAILS.append(what)
            print(f"FAIL {what}")


    def main():
        # A pre-fix run points the suite at an OLD build with TINYCMDR_SRC, like the other
        # suites; the scenario loader spells the same knob TINYCMDR_TEST_APP.
        if os.environ.get("TINYCMDR_SRC") and not os.environ.get("TINYCMDR_TEST_APP"):
            os.environ["TINYCMDR_TEST_APP"] = os.environ["TINYCMDR_SRC"]
        workdir = Path(tempfile.mkdtemp(prefix="fbtest-spill-"))
        try:
            run_scenario.stage_install(workdir, 24000)
            fb = run_scenario.load(workdir)
            cap = int(fb.CONFIG["agent"]["tool_output_max_chars"])
            marker = "MARKER-7QZ42"
            body = "A" * 12000 + marker + "B" * 12000 + "END-OF-OUTPUT"

            # ---- a big result keeps its middle, on disk -------------------------------------
            out = fb.cap_output("shell", body, "command output")
            check(len(out) < len(body), f"the prompt copy is smaller ({len(out)} < {len(body)})")
            check(marker not in out, "the middle is not in the prompt - that is what the cap is")
            m = re.search(r"spill/([A-Za-z0-9_.-]+\.txt)", out)
            check(bool(m), "the result names a spill file")
            spilled = ((fb.BASE_DIR / "spill" / m.group(1)).read_text(encoding="utf-8")
                       if m else "")
            check(marker in spilled, "the text on disk still carries the middle marker")
            check(spilled == body, "and it is byte-for-byte what the tool produced")
            check("Nothing was dropped" in out and "Do NOT" in out,
                  "the pointer says how to read it and not to re-run the command")
            check("find_tools" in out,
                  "the pointer names find_tools for a hidden search_files (%s)" % out[:120])

            # ---- a secret in a tool result is masked BEFORE it lands at rest --------------
            # A-2026-10-10-07: cap_output spilled the raw text and the run loop's scrub
            # came later, so a secret a tool result carried sat raw in spill/ on disk.
            sec = "sk-spill-leak-0123456789"
            prev_secrets = fb._SECRETS
            fb._SECRETS = set(fb._SECRETS) | {sec}
            try:
                body2 = (sec + " head of the result " + "S" * 12000
                         + " middle " + "T" * 12000 + " END")
                out4 = fb.cap_output("shell", body2, "command output")
                m2 = re.search(r"spill/([A-Za-z0-9_.-]+\.txt)", out4)
                spilled2 = ((fb.BASE_DIR / "spill" / m2.group(1)).read_text(encoding="utf-8")
                            if m2 else "")
                check(sec not in spilled2 and "«redacted»" in spilled2,
                      "the spilled file carries no secret")
                check(sec not in out4, "and the prompt copy is masked too, head included")
                row2 = (next((e for e in fb._spill_rows()
                              if e.get("path") == "spill/" + m2.group(1)), None)
                        if m2 else None)
                check(row2 is None or sec not in (row2.get("first") or ""),
                      "and the index row's first line carries no secret")
            finally:
                fb._SECRETS = prev_secrets

            # ---- the folder itself is owner-only -----------------------------------------
            if os.name != "nt":
                sp = fb.BASE_DIR / "spill"
                check(stat.S_IMODE(sp.stat().st_mode) == 0o700,
                      "the spill dir is created 0700")
                os.chmod(sp, 0o755)
                fb._spill_dir()
                check(stat.S_IMODE(sp.stat().st_mode) == 0o700,
                      "an existing wide spill dir is tightened when touched")
            else:
                print("skip the spill dir mode checks (Windows has no POSIX mode bits)")

            # ---- the index is budgeted per render, and says what it left out --------------
            # A-2026-10-10-01: every request re-bought the session's whole spill index -
            # no row cap, and the header's "older lines drop off" sentence was static.
            sp_dir = fb.BASE_DIR / "spill"
            ix_rows = []
            for i in range(1, 26):
                f = sp_dir / ("probe-%02d.txt" % i)
                f.write_text("spill %d\n" % i, encoding="utf-8")
                ix_rows.append({"id": i, "tool": "shell", "path": "spill/" + f.name,
                                "chars": 3000 + i, "at": time.time() - 60 * i,
                                "session": "index-sess", "first": "spill %d" % i})
            saved_rows = list(fb._SPILLS)
            try:
                fb._SPILLS[:] = ix_rows
                block = fb.spill_index_block("index-sess")
                check(len(block) < 2000,
                      "a session with many spills gets a BOUNDED index (%d chars)"
                      % len(block))
                check("spill#25" in block, "the newest spill is listed")
                check("spill#1 " not in block, "the oldest is left out")
                check("older spill(s) are not listed" in block,
                      "and the header says how many, not the old static line")
            finally:
                fb._SPILLS[:] = saved_rows

            # ---- the inline excerpt is not carried into the next run twice ----------------
            # A-2026-10-10-02: the carry stored the capped result verbatim, so every later
            # request of the next run re-bought the excerpt the prompt had already shown.
            saved_cap = fb.CONFIG["agent"]["tool_output_max_chars"]
            fb.CONFIG["agent"]["tool_output_max_chars"] = 8000
            try:
                ex_body = "E" * 5000 + "\nERROR: the flange is loose\n" + "F" * 5000
                capped = fb.cap_output("shell", ex_body, "command output")
                check("name a cause or a failure" in capped,
                      "a cause-naming line from the dropped middle rides inline (%s)"
                      % capped[:120])
                fb.record_tool_result({"session_key": "carry-excerpt-sess"}, "shell",
                                      {"command": "x"}, capped)
                carried = ((fb._carry_load("carry-excerpt-sess").get("entries")
                            or [{}])[0].get("out") or "")
                check("name a cause or a failure" not in carried,
                      "...and the carry copy does NOT re-buy it (%s)" % carried[:120])
                check("spill/" in carried and "Nothing was dropped" in carried,
                      "...while the pointer is still carried (%s)" % carried[:160])
            finally:
                fb.CONFIG["agent"]["tool_output_max_chars"] = saved_cap

            # ---- a small result is untouched -----------------------------------------------
            check(fb.cap_output("shell", "one line", "command output") == "one line",
                  "a result under the cap passes through unchanged")

            # ---- emoji are their OWN token class, not CJK (A-109) --------------------------
            # 400 emoji estimated 307 tokens (1.30 chars/token) because the 4-byte codepoint
            # landed in the CJK 1.3 divisor; a tokenizer spends ~2 tokens on each. The prose
            # half of the check is the guard: a stray emoji in a line of text must NOT be
            # charged 2 tokens apiece.
            _emoji = "\U0001F600" * 400
            _et = fb.est_tokens(_emoji)
            _prose = "the build finished and the report is attached " * 20 + "ok \U0001F600\n"
            check(_et >= 400 * 1.8 and fb.est_tokens(_prose) <= len(_prose) / 2.0,
                  f"an emoji-only sample costs ~2 tokens per emoji ({_et} for 400, "
                  f"{round(400 / _et, 2)} emoji/token), while a prose line with a stray emoji "
                  f"keeps the text divisor ({fb.est_tokens(_prose)} for {len(_prose)} chars)")
            # VS16 (U+FE0F) and ZWJ (U+200D) ride with the base emoji, so a heart+VS16 or a
            # ZWJ family is charged the emoji rate too.
            _vs = fb.est_tokens("\u2764\uFE0F" * 200)
            _zwj = fb.est_tokens("\U0001F468\u200D\U0001F469\u200D\U0001F467" * 50)
            check(_vs >= 200 * 1.8 and _zwj >= 50 * 1.8,
                  f"a VS16 emoji and a ZWJ family are in the emoji class ({_vs} for 200 "
                  f"heart+VS16, {_zwj} for 50 family sequences)")

            # ---- switching it off restores the old behaviour -------------------------------
            fb.CONFIG["agent"]["spill_output"] = False
            try:
                out2 = fb.cap_output("shell", body, "command output")
            finally:
                fb.CONFIG["agent"]["spill_output"] = True
            check("truncated" in out2 and marker not in out2,
                  "spill off falls back to truncation")

            # ---- a spill that cannot be written must never break the run -------------------
            real_dir = fb._spill_dir
            fb._spill_dir = lambda: (_ for _ in ()).throw(OSError("disk full"))
            try:
                out3 = fb.cap_output("shell", body, "command output")
            finally:
                fb._spill_dir = real_dir
            check("truncated" in out3, "a failed spill degrades to truncation, no exception")

            # ---- rotation keeps the folder bounded ----------------------------------------
            # It prunes the files NO live index row names: a row is a pointer the
            # prompt tells the model to follow, so rotating by file count alone is what made
            # session A's spill stop resolving. The bound is therefore the index (12 rows)
            # plus `spill_keep` spares.
            fb.CONFIG["agent"]["spill_keep"] = 3
            for i in range(6):
                fb.cap_output("shell", f"{i}" + "x" * (cap + 200), "command output")
                time.sleep(0.02)
            left = list((fb.BASE_DIR / "spill").glob("*.txt"))
            indexed = [str(e.get("path") or "") for e in fb._spill_rows()]
            check(all((fb.BASE_DIR / p).exists() for p in indexed),
                  "every file a live index row names is still on disk")
            check(len(left) <= fb._SPILLS_MAX + 3,
                  f"rotation keeps the folder bounded ({len(left)} files)")
            fb.CONFIG["agent"]["spill_keep"] = 50

            # ---- a REAL call site spills, on the exact route the analysis lost data on -----
            big = workdir / "big_output.log"
            lines = [f"line {i} token-{i * 7919}" for i in range(4000)]
            big.write_text("\n".join(lines), encoding="utf-8")

            # A read_file is not digested at all (2026-09-29, see _digest_subject): a PATH is not a
            # command, and shaping a document by its FILENAME gutted the chapter files of a
            # rewrite. A read over the cap goes straight to the spill - the whole text on disk,
            # both ends plus a pointer in the prompt - so no filename can change what the model
            # sees, and nothing is dropped either way.
            plain = fb.tool_read_file({"path": str(big), "limit": 4000},
                                      {"session_key": "spill-session"})
            check("spill/" in plain,
                  "a read over the cap hands back a spill pointer, whatever the file is called")
            check("[HARNESS: digested" not in plain,
                  "  and the read is never digested - a document is not a command's output")

            m2 = re.search(r"spill/([A-Za-z0-9_.-]+\.txt)", plain)
            spilled2 = ((fb.BASE_DIR / "spill" / m2.group(1)).read_text(encoding="utf-8")
                        if m2 else "")
            check(bool(m2) and spilled2.count("token-") == 4000,
                  f"and that file holds all 4000 lines, readable by the model "
                  f"({spilled2.count('token-')} found)")

            # ---- the spill INDEX: what is on disk, without carrying any of it --------------
            # (2026-09-21: the pointer worked and was then the only trace, so a run
            # that lost it had no way to know a spill existed. One line per spill, with an id
            # that reads it back.)
            block = fb.spill_index_block()
            check(bool(block) and "spill#" in block and "starts:" in block,
                  "the index names each spill with an id, its path and its first line")
            ids = re.findall(r"spill#(\d+)", block)
            check(bool(ids), f"the index carries ids ({ids[-3:] if ids else []})")
            got = fb.tool_read_file({"path": "spill#" + ids[-1]},
                                    {"session_key": "spill-session"})
            check(not got.startswith("ERROR"), f"an id reads its spill back ({got[:60]!r})")
            check(got.startswith(str(fb.BASE_DIR / "spill")),
                  "  and it resolved to the real file, not to a path named 'spill#N'")
            bad = fb.tool_read_file({"path": "spill#99999"}, {"session_key": "spill-session"})
            check(bad.startswith("ERROR") and "no spill#99999" in bad,
                  "an id this process never wrote is refused, by name")
            check("spill#" in fb.volatile_context(session_key=None),
                  "the index rides in the prompt block")
            for i in range(fb._SPILLS_MAX + 3):
                fb.cap_output("shell", "z" * (cap + 200) + f" tail-{i}", "command output")
            after = fb.spill_index_block()
            ids2 = re.findall(r"spill#(\d+)", after)
            check(len(ids2) == fb._SPILLS_MAX,
                  f"the index is bounded ({len(ids2)} of at most {fb._SPILLS_MAX})")
            check(ids2 and ids2[-1] == str(fb._SPILL_SEQ["n"]),
                  "  and it is the OLDEST lines that drop, never the newest")

            # ---- the index belongs to ONE session, and /new drops that session's pointers ----
            # Measured 2026-09-25 on a live install: process-wide, the index put one
            # conversation's spilled output in front of every other conversation's model, and it
            # survived /new - a fresh order ("how much room is left on the C drive") was answered
            # in two calls and then spent ten more reading the PREVIOUS, stopped run's spill files
            # and re-running its scans.
            # the marker leads, because the index previews a spill's FIRST line only
            fb.cap_output("shell", "mine-s1 " + "q" * (cap + 200), "command output",
                          session="sess-A")
            fb.cap_output("shell", "mine-s2 " + "q" * (cap + 200), "command output",
                          session="sess-B")
            a_idx, b_idx = fb.spill_index_block("sess-A"), fb.spill_index_block("sess-B")
            check("mine-s1" in a_idx and "mine-s2" not in a_idx,
                  "a session's prompt carries only its OWN spill pointers")
            check("mine-s2" in b_idx and "mine-s1" not in b_idx,
                  "  and the other session carries only its own")
            mine = fb._spill_rows("sess-A")
            on_disk = (fb.BASE_DIR / mine[-1]["path"]).exists()
            fb.AGENT.reset("sess-A")
            check(fb.spill_index_block("sess-A") == "",
                  "/new drops this session's spill pointers")
            check(fb.spill_index_block("sess-B") != "",
                  "  and does not touch another session's")
            check(on_disk and (fb.BASE_DIR / mine[-1]["path"]).exists(),
                  "  the spilled FILE stays on disk - nothing was dropped")

            # ---- a live row's file survives another session's spills ------------
            # Measured with spill_keep=3: session A's indexed spill stopped resolving as soon
            # as session B wrote four more, and the block's own promise is "The FULL text is
            # on disk - nothing was dropped".
            with fb._SPILLS_LOCK:
                fb._SPILLS[:] = []
            fb.CONFIG["agent"]["spill_keep"] = 3
            a_out = fb.cap_output("shell", "A" * (cap + 200) + " tail-A", "command output",
                                  session="sess-A")
            m_a = re.search(r"spill/([A-Za-z0-9_.-]+\.txt)", a_out)
            for i in range(5):
                fb.cap_output("shell", "B" * (cap + 200) + f" tail-B{i}", "command output",
                              session="sess-B")
            ids_a = re.findall(r"spill#(\d+)", fb.spill_index_block("sess-A"))
            check(bool(m_a) and bool(ids_a), f"session A has an indexed spill ({ids_a})")
            check((fb.BASE_DIR / "spill" / m_a.group(1)).exists(),
                  "the file session A's index still points at was NOT rotated away")
            got_a = fb.tool_read_file({"path": "spill#" + ids_a[-1]},
                                      {"session_key": "sess-A"})
            check(not got_a.startswith("ERROR"),
                  f"and session A still reads it back ({got_a[:60]!r})")

            # ---- a row whose file is gone drops off the index ------------------------------
            # (the read-back above spilled its own copy, so this checks the DELETED row's id
            # and its file, not "the index is empty")
            (fb.BASE_DIR / "spill" / m_a.group(1)).unlink()
            block_a2 = fb.spill_index_block("sess-A")
            ids_a2 = re.findall(r"spill#(\d+)", block_a2)
            check(m_a.group(1) not in block_a2 and ids_a[-1] not in ids_a2,
                  f"a spill whose file was deleted drops off the index instead of dangling "
                  f"(was {ids_a[-1]}, now {ids_a2})")
            fb.CONFIG["agent"]["spill_keep"] = 50

            # ---- the truncation note must never lie, nor return the text it cut (A-104) ----
            # `limit` 0 or 1 fell through `text[-0:]` (== the whole text): a 50-char body came
            # back in full with a note claiming part of it was gone, and 1 on 500 chars gave 547.
            _small = "abcdefghij" * 5
            _out0 = fb.truncate_middle(_small, 0)
            check("too small" in _out0 and _small[:10] not in _out0,
                  f"truncate_middle(text, 0) refuses instead of returning the whole text "
                  f"({_out0[:60]!r})")
            _out1 = fb.truncate_middle("x" * 500, 1)
            check(len(_out1) < 100 and "500 chars omitted" in _out1,
                  f"truncate_middle(text, 1) does not return 547 chars ({len(_out1)})")
            _outm = fb.truncate_middle("H" * 20 + "M" * 21 + "T" * 20, 21)
            check(_outm.startswith("H" * 10) and _outm.endswith("T" * 10)
                  and "41 chars omitted" in _outm,
                  f"a normal limit keeps a head and a tail and names the real omission "
                  f"({_outm!r})")

            # ---- the spill-signal budget bounds the WHOLE block, wrapper included (A-105) ---
            # Only the picked lines were charged, so a 100-char budget returned a 146-char
            # [HARNESS:] header plus the lines - 215 chars in all.
            _sig_body = "\n".join(f"line {i} error: boom" for i in range(80))
            _sig_small = fb._spill_signal(_sig_body, 0, len(_sig_body), 100)
            check(len(_sig_small) <= 100,
                  f"a 100-char budget is not overrun by the wrapper ({len(_sig_small)})")
            _sig_big = fb._spill_signal(_sig_body, 0, len(_sig_body), 4000)
            check(len(_sig_big) <= 4000 and "error: boom" in _sig_big,
                  "a workable budget still carries the cause lines")

            # ---- the [HARNESS: ...] stripper matches nested brackets (A-106) ---------------
            # The old `[^\]]*` stopped at the FIRST `]`, eating the note's tail and the real
            # output behind it, so two different results could read as one signature.
            check(fb._dedupe_text("[HARNESS: a [nested] b] tail") == "tail",
                  "a nested-bracket note strips cleanly "
                  f"({fb._dedupe_text('[HARNESS: a [nested] b] tail')!r})")
            check(fb._dedupe_text("[HARNESS: note] X [HARNESS: note]")
                  != fb._dedupe_text("[HARNESS: note] Y [HARNESS: note]"),
                  "two genuinely different results do not collapse to one signature")

            print()
            print(f"{len(PASSES)} passed, {len(FAILS)} failed")
            if FAILS:
                print(f"{len(FAILS)} check(s) FAILED")
                return 1
            print("all spill checks passed")
            return 0
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
    return main()


def _suite_test_spill_durability():
    """The spill promise survives a restart, a retry, and a runaway command.

"The FULL text is on disk - nothing was dropped" was process-scoped: the index lived in
memory only (a restart orphaned every pointer), the filename carried a timestamp (the same
output spilled twice wrote two files), and the write had no ceiling (a multi-GB log was
faithfully written byte for byte). Content addressing plus a byte cap close both.

    python tests/test_spill_state_surface.py
"""
    import json
    import re
    import shutil
    import sys
    import tempfile
    import time
    from pathlib import Path

    BASE = Path(__file__).resolve().parent.parent
    TESTS = BASE / "tests"
    sys.path.insert(0, str(TESTS))

    import run_scenario  # noqa: E402

    FAILS = []


    def check(cond, what, detail=""):
        if not cond:
            FAILS.append(what)
            print(f"FAIL {what}: {detail}")
        else:
            print(f"ok   {what}")


    def simulate_restart(fb):
        """Drop the in-memory state the way a fresh process starts, but keep the disk.

    The id COUNTER is process state too: a restart begins at 0 and derives the next id
    from the index. Leaving it standing made this simulation unable to see the id-reuse
    bug the whole check group exists for (run 11, A-107).
    """
        fb._SPILLS[:] = []
        fb._SPILLS_LOADED = False
        fb._SPILL_SEQ["n"] = 0


    def index_records(fb):
        """The index's lines as dicts - rows AND `removed` records (see _spill_index_save)."""
        out = []
        path = fb._spill_index_path()
        if not path.exists():
            return out
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out


    def index_rows(fb):
        return [e for e in index_records(fb) if isinstance(e, dict) and e.get("path")]


    def index_removals(fb):
        return [e for e in index_records(fb) if isinstance(e, dict) and e.get("removed")]


    def main():
        workdir = Path(tempfile.mkdtemp(prefix="fbspill-"))
        try:
            run_scenario.stage_install(workdir, 24000)
            fb = run_scenario.load(workdir)
            spill = fb.BASE_DIR / "spill"
            marker = "MARKER-9K2"
            body = "A" * 12000 + marker + "B" * 12000

            # ---- the same output spills once: one file, one row, stable id
            out1 = fb.cap_output("shell", body, "command output", session="sp1")
            out2 = fb.cap_output("shell", body, "command output", session="sp1")
            name = re.search(r"spill/([0-9a-f]{16}\.txt)", out1)
            check(bool(name), "the spill file is content-addressed", out1[:120])
            check((spill / name.group(1)).read_text(encoding="utf-8") == body,
                  "and holds the whole text", name.group(1))
            rows = fb._spill_rows("sp1")
            check(len(rows) == 1, "the same output does not add a second index row", rows)
            id_before = rows[0]["id"]
            fb.cap_output("shell", body, "command output", session="sp1")
            rows = fb._spill_rows("sp1")
            check(len(rows) == 1 and rows[0]["id"] == id_before,
                  "a retry refreshes the row in place (same id)", rows)
            check(re.search(r"spill/([0-9a-f]{16}\.txt)", out2).group(1) == name.group(1),
                  "and the pointer is the same file", out2[:120])

            # ---- the index survives a restart
            simulate_restart(fb)
            rows = fb._spill_rows("sp1")
            check(len(rows) == 1 and rows[0]["id"] == id_before,
                  "the row is reloaded from disk after a restart", rows)
            resolved = fb._spill_path("spill#%d" % id_before, "sp1")
            check(resolved and Path(resolved).exists(),
                  "and spill#<id> still resolves", resolved)

            # ---- /new's removal is persistent too
            fb.AGENT.reset("sp1")
            check(fb._spill_rows("sp1") == [], "reset drops this session's rows")
            check([r for r in index_removals(fb) if r["removed"].startswith("spill/")],
                  "the removal itself is written into the index (not just process memory)",
                  index_removals(fb))
            simulate_restart(fb)
            check(fb._spill_rows("sp1") == [],
                  "and they do not come back on the next restart", fb._spill_rows("sp1"))

            # ---- a STALE memory row in the other process cannot resurrect a removal
            # The merge's `rows` half reads THIS process's memory, so the process that never
            # saw the /new (the concurrent --once run) is the one that used to write the row
            # straight back. Only the DISK record can stop it - an in-memory tombstone cannot,
            # and it is spent by the very save that made it.
            stale = [r for r in index_removals(fb)]
            gone = stale[0]["removed"]
            holder = fb.BASE_DIR / gone
            check(holder.exists(), "the removed row's file stays on disk (nothing was dropped)",
                  gone)
            fb._SPILLS.append({"id": 7777, "tool": "shell", "path": gone, "first": "x",
                               "chars": 1, "at": time.time() - 120, "session": "sp1"})
            fb._spill_index_save()
            back = [r.get("path") for r in index_rows(fb)]
            check(gone not in back,
                  "a second process holding the stale row cannot put it back", back)
            fb._SPILLS[:] = [r for r in fb._SPILLS if r.get("path") != gone]

            # ---- a FAILED save neither loses the removal nor spends its protection
            # The order used to be: drop the tombstones, then write. A write that raised
            # therefore lost the removal and the thing guarding it in one go, and the next save
            # re-persisted what /new had just deleted - /new quietly stopped holding.
            fb.cap_output("shell", "tombstone-me-" + body, "command output", session="sp9")
            check(bool(fb._spill_rows("sp9")), "a row for /new to remove", fb._spill_rows("sp9"))
            rows_now = index_rows(fb)
            removed_now = {r["removed"] for r in index_removals(fb)}
            real_write = fb.atomic_write_text

            def boom(*_a, **_k):
                raise OSError("disk full (simulated)")

            fb.atomic_write_text = boom
            try:
                fb.AGENT.reset("sp9")                  # its own save is the one that fails
                check(fb._spill_rows("sp9") == [],
                      "the removal still applies in this process")
                check(bool(fb._SPILL_TOMBSTONES),
                      "and a failed save KEEPS the tombstones for the retry",
                      fb._SPILL_TOMBSTONES)
            finally:
                fb.atomic_write_text = real_write
            check(index_rows(fb) == rows_now and
                  {r["removed"] for r in index_removals(fb)} == removed_now,
                  "the index on disk still holds the previous state, unmangled")
            fb._spill_index_save()                     # the retry
            check(not fb._SPILL_TOMBSTONES, "the retry spends what the failure kept")
            check(all(r.get("path") not in {x["removed"] for x in index_removals(fb)}
                      for r in index_rows(fb)),
                  "...and the removal is on disk, so the merge can never put the row back",
                  index_removals(fb))

            # ---- a runaway command is capped, honestly
            fb.CONFIG["agent"]["spill_max_bytes"] = 4000
            huge = "".join("line %d %s\n" % (i, "x" * 80) for i in range(4000))
            out3 = fb.cap_output("shell", huge, "command output", session="sp2", limit=500)
            m3 = re.search(r"spill/([0-9a-f]{16}\.txt)", out3)
            blob = (spill / m3.group(1)).read_bytes() if m3 else b""
            check(len(blob) <= 4200, "the spill file respects the byte cap (%d)" % len(blob),
                  len(blob))
            check("bytes omitted" in blob.decode("utf-8", "replace"),
                  "the file says what it dropped")
            check("capped at 4000 bytes" in out3,
                  "the pointer says the file is capped", out3[-300:])
            check(blob.startswith(huge[:200].encode()),
                  "the file still starts with the real output")
            fb.CONFIG["agent"]["spill_max_bytes"] = 8 * 1024 * 1024

            # ---- the index MERGES with what another process wrote
            # A second process on this install never sees this one's rows, and its save
            # used to replace the whole index; the save re-reads and unions by `path`.
            fb.cap_output("shell", "first-" + body, "command output", session="sp5")
            idx = fb._spill_index_path()
            foreign = spill / "ffffffffffffffff.txt"
            foreign.write_text("another process's output", encoding="utf-8")
            with idx.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"id": 4242, "path": "spill/ffffffffffffffff.txt",
                                     "at": time.time() + 60, "session": "elsewhere",
                                     "tool": "shell", "kind": "command output"}) + "\n")
            fb.cap_output("shell", "second-" + body, "command output", session="sp5")
            lines = [json.loads(l) for l in idx.read_text(encoding="utf-8").splitlines()
                     if l.strip()]
            paths = {str(e.get("path")) for e in lines}
            check("a save merges a foreign row instead of clobbering the index",
                  "spill/ffffffffffffffff.txt" in paths, sorted(paths))
            check("...and this process's own rows are all there",
                  len([p for p in paths if p != "spill/ffffffffffffffff.txt"]) >= 2,
                  sorted(paths))
            # ---- ids are never reused, even after every spill file is deleted (A-107)
            # Measured in run 11: with all spill files gone the counter restarted at 1, so a new
            # row took id 1 while the index still carried the old id-1 row, and `spill#1`
            # resolved to a different tool's output than the row the prompt named.
            rows = fb._spill_rows()
            max_id = max([int(e["id"]) for e in rows] or [0])
            for f in spill.glob("*.txt"):
                f.unlink()
            simulate_restart(fb)
            fb._spill_rows()                       # the load advances the sequence
            fb.cap_output("shell", "after-the-wipe-" + body, "command output", session="sp5")
            rows = index_rows(fb)
            ids = [int(e["id"]) for e in rows]
            check(len(ids) == len(set(ids)) and min(ids) > max_id,
                  "an id is never reused after its file is deleted (A-107)", (max_id, ids))
            check(Path(fb._spill_path("spill#%d" % max(ids), "sp5") or "").exists(),
                  "...and the new row resolves to the new file",
                  fb._spill_path("spill#%d" % max(ids), "sp5"))

            # ---- a dead row is not re-persisted by the merge (A-108)
            idx = fb._spill_index_path()
            dead = "spill/00000000000000ff.txt"
            with idx.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"id": 9999, "path": dead, "at": time.time() + 60,
                                     "session": "sp6", "tool": "shell"}) + "\n")
            fb.cap_output("shell", "saves-again-" + body, "command output", session="sp6")
            paths = {str(e.get("path")) for e in index_rows(fb)}
            check(dead not in paths, "a row whose file is gone is not re-persisted (A-108)",
                  sorted(paths))

            # ---- the merge's READ is inside the inter-process lock (run 21, A-49)
            # atomic_write_text takes that lock one level down, which covers the WRITE only:
            # two processes could both read, both merge, and the last rename wins - the
            # clobber the merge above exists to stop. Recorded, not argued: the lock must be
            # entered BEFORE the index is read.
            idx = fb._spill_index_path()
            guard = spill / "aaaa0000aaaa0000.txt"
            guard.write_text("a row to save", encoding="utf-8")
            events = []
            real_lock, real_write = fb._path_lock, fb.atomic_write_text
            real_path_fn = fb._spill_index_path

            class _RecordingPath(type(Path())):
                def read_text(self, *a, **k):
                    events.append("read")
                    return super().read_text(*a, **k)

            def _spy_lock(p):
                events.append("lock")
                return real_lock(p)

            def _spy_write(p, t, **k):
                events.append("write")
                return real_write(p, t, **k)

            try:
                fb._path_lock = _spy_lock
                fb.atomic_write_text = _spy_write
                fb._spill_index_path = lambda: _RecordingPath(str(idx))
                fb._SPILLS.append({"id": 8888, "tool": "shell",
                                   "path": "spill/%s" % guard.name, "first": "x",
                                   "chars": 1, "at": time.time(), "session": "lock-probe"})
                fb._spill_index_save()
            finally:
                fb._path_lock, fb.atomic_write_text = real_lock, real_write
                fb._spill_index_path = real_path_fn
                fb._SPILLS[:] = [r for r in fb._SPILLS if r.get("id") != 8888]
            # events: lock (the save's own), read, lock (atomic_write_bytes'), write
            check(events and events[0] == "lock" and "read" in events
                  and events.index("read") > events.index("lock"),
                  "the index READ happens under the inter-process lock (A-49)", events)
            check("write" in events and events.index("write") > events.index("read"),
                  "...and the write still follows the read", events)

            # ---- the index is bounded by the SPILL FOLDER, not by the run (run 21, A-50)
            # A row survives only while its file does, and _spill_rotate bounds the files; the
            # docstring used to promise "at most _SPILLS_MAX" while merged was never capped, so
            # this pins the bound that actually holds instead of the one that was written down.
            fb.CONFIG["agent"]["spill_keep"] = 3
            for i in range(20):
                fb.cap_output("shell", "bounded-%02d-%s" % (i, body), "command output",
                              session="spb%02d" % i)
            at20 = len(index_records(fb))
            for i in range(20, 60):
                fb.cap_output("shell", "bounded-%02d-%s" % (i, body), "command output",
                              session="spb%02d" % i)
            at60 = len(index_records(fb))
            files = len(list(spill.glob("*.txt")))
            check(at60 == at20,
                  "40 more spills add no rows: the index is bounded by the folder",
                  (at20, at60, files))
            check(files <= fb._SPILLS_MAX + int(fb.CONFIG["agent"]["spill_keep"]) + 2,
                  "...and the folder itself is bounded by _SPILLS_MAX + spill_keep",
                  (files, fb._SPILLS_MAX))
            fb.CONFIG["agent"]["spill_keep"] = 50

        finally:
            shutil.rmtree(workdir, ignore_errors=True)

        print()
        if FAILS:
            print("%d check(s) failed" % len(FAILS))
            return 1
        print("all spill-durability checks passed")
        return 0
    return main()


def _suite_test_compaction_continuity():
    """The elision marker carries run STATE, and the overflow path keeps a copy.

A list of dropped calls cannot say what the run has read and changed; the marker now
carries that state as a compact, deduped ledger with R/W/RW markers. Measured here
2026-09-29: a
run compacted mid-rewrite and the next calls re-derived the task from session files.

And `_force_shrink` - the path that drops the MOST context - was the one path that never
wrote the pre-compaction transcript.

    python tests/test_spill_state_surface.py
"""
    import json
    import os
    import shutil
    import sys
    import tempfile
    from pathlib import Path

    BASE = Path(__file__).resolve().parent.parent
    TESTS = BASE / "tests"
    sys.path.insert(0, str(TESTS))

    import run_scenario  # noqa: E402

    FAILS = []


    def check(cond, what, detail=""):
        if not cond:
            FAILS.append(what)
            print(f"FAIL {what}: {detail}")
        else:
            print(f"ok   {what}")


    def main():
        workdir = Path(tempfile.mkdtemp(prefix="fbcontinuity-"))
        try:
            run_scenario.stage_install(workdir, 24000)
            fb = run_scenario.load(workdir)
            A = fb.AGENT
            key = "continuity-key"

            # ---- the ledger: deduped by path, markers track read/write
            fb._TOUCHED.pop(key, None)
            f1 = workdir / "sub" / "a.py"
            f2 = workdir / "sub" / "b.conf"
            f1.parent.mkdir(parents=True, exist_ok=True)
            f1.write_text("x\n", encoding="utf-8")
            f2.write_text("y\n", encoding="utf-8")
            fb._touch_files(key, "read_file", {"path": str(f1)})
            fb._touch_files(key, "edit_file", {"path": str(f1)})
            fb._touch_files(key, "write_file", {"path": str(f2)})
            fb._touch_files(key, "shell", {"command": "echo hi"})
            ledger = fb._files_ledger(key)
            check("a.py (RW)" in ledger, "a read then a write of one path is RW", ledger)
            check("b.conf (W)" in ledger, "a written-only path is W", ledger)
            # os.sep: the ledger groups a directory with the separator the platform uses, and
            # asserting a "/" graded the POSIX spelling on a Windows path (the product appended a
            # hard-coded "/" until 2deb132, so this check passed there by accident).
            check(str(f1.parent) + os.sep in ledger and ledger.count("a.py") == 1,
                  "paths are grouped by directory and deduped", ledger)

            # ---- the elision marker carries it, bounded and once
            marker = A._elision_note(fb.MARK_COMPACT, [], "", key)
            check("[files touched this run]" in marker and "a.py (RW)" in marker,
                  "the marker carries the ledger", marker)
            marker2 = A._elision_note(fb.MARK_COMPACT, [], marker, key)
            check(marker2.count("[files touched this run]") == 1,
                  "a second compaction does not stack a second ledger", marker2)

            # ---- the transcript, when it exists, is named in the marker
            sess = fb.SESSIONS_DIR
            sess.mkdir(parents=True, exist_ok=True)
            tp = sess / (key + ".transcript.jsonl")
            tp.write_text('{"role":"user","content":"earlier"}\n', encoding="utf-8")
            marker = A._elision_note(fb.MARK_COMPACT, [], "", key)
            check(str(tp) in marker and "instead of re-deriving" in marker,
                  "an existing transcript is advertised in the marker", marker)

            # ---- a cut produces a marker carrying the ledger
            msgs = [{"role": "system", "content": "s"}]
            for i in range(6):
                msgs += [{"role": "user", "content": "ask %d %s" % (i, "x" * 200)},
                         {"role": "assistant", "content": "",
                          "tool_calls": [{"id": "c%d" % i, "type": "function",
                                          "function": {"name": "read_file",
                                                       "arguments": json.dumps(
                                                           {"path": str(f1)})}}]},
                         {"role": "tool", "tool_call_id": "c%d" % i,
                          "content": "line\n" * 50}]
            removed = A._drop_oldest_block(msgs, fb.MARK_COMPACT, key)
            check(isinstance(removed, int) and removed > 0, "the cut reports tokens removed",
                  removed)
            check(str(msgs[1].get("content", "")).startswith(fb.MARK_COMPACT)
                  and "[files touched this run]" in msgs[1]["content"],
                  "the marker left behind carries the ledger", msgs[1]["content"][-160:])

            # ---- _force_shrink writes the transcript before it evicts
            fb._TOUCHED.pop(key, None)
            tp.unlink(missing_ok=True)
            big = [{"role": "system", "content": "s"}]
            for i in range(30):
                big += [{"role": "user", "content": "u%d %s" % (i, "y" * 2000)},
                        {"role": "assistant", "content": "a%d %s" % (i, "z" * 2000)}]
            A._force_shrink(big, key, fb.CONFIG["llm"]["base_url"])
            check(tp.exists() and tp.read_text(encoding="utf-8").count("\n") > 10,
                  "_force_shrink writes the pre-compaction transcript", tp)
            check(any(str(m.get("content", "")).startswith(fb.MARK_SHRINK)
                      for m in big), "the shrink left its marker", [m.get("role") for m in big[:6]])
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

        print()
        if FAILS:
            print("%d check(s) failed" % len(FAILS))
            return 1
        print("all compaction-continuity checks passed")
        return 0
    return main()


def main():
    rc = 0
    for name, fn in (("test_spill", _suite_test_spill), ("test_spill_durability", _suite_test_spill_durability), ("test_compaction_continuity", _suite_test_compaction_continuity)):
        rc |= _run(name, fn)
    return 1 if rc else 0


if __name__ == "__main__":
    sys.exit(main())

"""test_context_surface - one merged suite (test_transcript, test_reveal_decay, test_supersede_prune).

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


def _suite_test_transcript():
    """Compaction keeps what it destroys (2026-09-18).

The harness's own analysis ranked this second: `_compact` shrinks and deletes, and the session
file holds the compacted version only, so the evicted middle was unrecoverable. These checks pin
the transcript sink and the tally that makes it visible: before anything is cut, the full text
lands in sessions/<key>.transcript.jsonl, and a compaction shows up in the usage line.
"""
    import json
    import shutil
    import sys
    import tempfile
    from pathlib import Path

    BASE = Path(__file__).resolve().parent.parent
    TESTS = BASE / "tests"
    sys.path.insert(0, str(TESTS))

    import run_scenario  # noqa: E402

    FAILS = []


    def check(cond, what):
        if not cond:
            FAILS.append(what)
            print(f"FAIL {what}")
        else:
            print(f"ok   {what}")


    def main():
        workdir = Path(tempfile.mkdtemp(prefix="fbtest-transcript-"))
        try:
            run_scenario.stage_install(workdir, 24000)
            fb = run_scenario.load(workdir)
            key = "transcript-session"
            fb.SESSIONS_DIR = workdir / "sessions"
            fb.SESSIONS_DIR.mkdir(exist_ok=True)

            # a conversation far over the budget, with a needle that only the transcript will hold
            needle = "NEEDLE-8KQ31 the only copy of this sentence"
            messages = [{"role": "system", "content": "sys"}]
            for i in range(400):
                messages.append({"role": "user", "content": f"question {i} " + "q" * 400})
                messages.append({"role": "assistant", "content": needle if i == 12 else f"answer {i} " + "a" * 400})
            before = len(messages)
            out = fb.AGENT._compact(messages, key)

            check(len(out) < before, f"compaction shrank the payload ({before} -> {len(out)} messages)")
            tp = fb.SESSIONS_DIR / f"{key}.transcript.jsonl"
            check(tp.exists(), "the transcript file exists")
            body = tp.read_text(encoding="utf-8") if tp.exists() else ""
            check(needle in body, "the evicted middle is in the transcript, byte for byte")
            rows = [json.loads(l) for l in body.splitlines() if l.strip()]
            # The file carries the DROPPED span, not a copy of the whole live
            # conversation (which grew the model's own pointer by the transcript each firing).
            check(len(rows) < before - 1,
                  f"only the dropped span is written, not the whole conversation ({len(rows)})")
            check(any(needle in (r.get("content") or "") for r in rows),
                  "the evicted needle is among them")
            check(not any("answer 399" in (r.get("content") or "") for r in rows),
                  "the newest exchange (still live) is NOT duplicated into the transcript")
            check(all(r.get("why") == "compact" for r in rows), "each line says why it was written")
            # -- search_sessions reads BOTH shapes that live in sessions/ -----------
            # A carry sidecar (*.carry.json) has a dict root and sorted() puts it BEFORE the
            # transcript, so the old loop iterated its string keys and died with "'str' object
            # has no attribute 'get'" on the first sidecar: the tool built to recall a session
            # could not read any session that had ever run a tool. Found by Windows on its
            # own build 2026-09-20, after it lost a conversation's research to a run boundary.
            sk = "mm-recall-demo"
            (fb.SESSIONS_DIR / f"{sk}.json").write_text(json.dumps([
                {"role": "user", "content": "how do I get Toast to take AV1"},
                {"role": "assistant", "content": "Toast's input list has no AV1; QuickTime gates it"}]))
            (fb.SESSIONS_DIR / f"{sk}.carry.json").write_text(json.dumps({"run": 5, "entries": [
                {"tool": "web_search", "args": '{"query": "Roxio Toast AV1"}',
                 "out": "Toast 20 refuses AV1 in mp4; the white preview is the refusal",
                 "at": 1789900000, "run": 5}]}))
            out = fb.tool_search_sessions({"query": "av1"}, {})
            check("Toast" in out,
                  f"search_sessions survives a carry sidecar ({out[:60]!r})")
            check("refuses AV1" in out,
                  "...and recalls what a carried tool result said")
            check(f"[{sk}] assistant" in out,
                  "...while still reading the transcript itself")

            # ---- a multi-word query matches WORDS, not the literal phrase -------------
            # `query in content` made "scheduler fired schedule add" answer "No past session
            # content matching" while the same events were found in one search_files call -
            # the tool built for recall was worse at it than the generic search
            # (2026-10-02).
            (fb.SESSIONS_DIR / "mm-multi.json").write_text(json.dumps([
                {"role": "user", "content": "why did the scheduler not fire"},
                {"role": "assistant", "content": "the job was added without a schedule"},
                {"role": "assistant", "content": "it fired after the restart"}]))
            out = fb.tool_search_sessions({"query": "scheduler fired schedule add"}, {})
            check("mm-multi" in out,
                  f"a multi-word query matches words spread across a session ({out[:90]!r})")
            out = fb.tool_search_sessions({"query": "scheduler unrelated-absent-token"}, {})
            check("mm-multi" not in out,
                  "...and still refuses when one word is nowhere in the session")

            # ---- A-2026-10-08-156: hits are ranked, then newest-first, and a truncation
            # says what it hid. The scan used to stop at `limit` FILES in filename order, so
            # the alphabetically first sessions won and recent matches vanished in silence.
            (fb.SESSIONS_DIR / "aa-worse.json").write_text(json.dumps([
                {"role": "user", "content": "needle"},
                {"role": "assistant", "content": "99 extra word"}]))
            (fb.SESSIONS_DIR / "zz-best.json").write_text(json.dumps([
                {"role": "user", "content": "needle 99 extra word"}]))
            _res = fb.session_search_hits("needle 99 extra word", 25)
            _hits, _total = (_res if isinstance(_res, tuple) else (_res, len(_res)))
            check(_hits and _hits[0][0] == "zz-best",
                  f"the best-ranked hit comes first, whatever its filename ({_hits[:2]})")
            check(_hits and _hits[0][3] == 4,
                  f"...and its rank is the whole query ({_hits[:1]})")
            for i in range(30):
                (fb.SESSIONS_DIR / ("zz-needle-%02d.json" % i)).write_text(json.dumps(
                    [{"role": "user", "content": "needle deep in session %02d" % i}]))
            _res2 = fb.session_search_hits("needle", 25)
            _h2, _t2 = (_res2 if isinstance(_res2, tuple) else (_res2, len(_res2)))
            check(_t2 > len(_h2) and len(_h2) == 25,
                  f"a truncated set reports the full total ({(len(_h2), _t2)})")
            out = fb.tool_search_sessions({"query": "needle"}, {})
            check("more session(s) matched" in out,
                  f"...and the tool names what the truncation hid ({out[-160:]!r})")

            check(json.loads((fb.SESSIONS_DIR / f"{key}.transcript.jsonl").read_text(encoding="utf-8")
                             .splitlines()[0]).get("role") == "user",
                  "and the first line is the oldest message, in order")

            # the tally the operator sees
            st = fb.run_state(key, create=True)
            check(int(st.get("compactions") or 0) == 1, f"the run state counts it ({st.get('compactions')})")
            line = fb.fmt_usage({"calls": 2, "prompt": 100, "completion": 10, "llm_secs": 1,
                                 "compactions": 1})
            check("compaction(s)" in line, f"and the usage line shows it: {line}")

            # nothing over budget: nothing written
            # The file is bounded - at the cap it rotates to `.transcript.1`
            # (one predecessor, replaced on the next rotation) instead of growing for ever.
            # Deterministic: one `_save_transcript` call = one write, so the rotation is the
            # call's own, not a compaction loop's.
            needle2 = "SECOND-BLOCK-77 the next eviction"
            fb.AGENT._TRANSCRIPT_MAX_BYTES = 500
            tp.write_text("PREVIOUS-" * 80, encoding="utf-8")          # over the cap
            fb.AGENT._save_transcript(key, [{"role": "user", "content": needle2}], "compact")
            rolled = fb.SESSIONS_DIR / f"{key}.transcript.1.jsonl"
            check(rolled.exists(), "a full transcript rotates to .1")
            check("PREVIOUS-" in rolled.read_text(encoding="utf-8"),
                  "the predecessor holds the file that was over the cap")
            check(needle2 in tp.read_text(encoding="utf-8"),
                  "the new block lands in the fresh file")
            tp.write_text("SECOND-OVER-" * 60, encoding="utf-8")       # over the cap again
            fb.AGENT._save_transcript(key, [{"role": "user", "content": "THIRD-BLOCK"}], "compact")
            check("PREVIOUS-" not in rolled.read_text(encoding="utf-8")
                  and "SECOND-OVER-" in rolled.read_text(encoding="utf-8"),
                  "the next rotation replaces the one predecessor, never grows a chain")

            tp.unlink()
            small = [{"role": "system", "content": "sys"},
                     {"role": "user", "content": "hi"},
                     {"role": "assistant", "content": "hello"}]
            fb.AGENT._compact(small, key)
            check(not tp.exists(), "a payload inside the budget writes no transcript")

            print()
            if FAILS:
                print(f"{len(FAILS)} check(s) FAILED")
                return 1
            print("all transcript checks passed")
            return 0
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
    return main()


def _suite_test_reveal_decay():
    """A revealed schema expires; the tool does not.

Reveal-on-demand was monotonic per session: once a tool's schema was revealed it rode
every later payload, so a long run that touched 30 tools paid 30 schemas for ever - the
exact cost the disclosure layer exists to avoid. `agent.reveal_ttl_secs` (default 1800,
0 = off) expires a schema after that long without a call; the NAME stays listed and the
call itself always executes, because disclosure is about schemas, never about existence.

    python tests/test_context_surface.py
"""
    import importlib.util
    import json
    import os
    import shutil
    import sys
    import tempfile
    import time
    from pathlib import Path

    BASE = Path(__file__).resolve().parent.parent
    SRC = BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")

    STAGE = Path(tempfile.gettempdir()) / "tinycmdr-test-stage-reveal-decay"
    if STAGE.exists():
        shutil.rmtree(STAGE, ignore_errors=True)
    STAGE.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SRC, STAGE / "tinycmdr.py")
    shutil.copy2(Path(__file__).resolve().parent / "fixture-config.json",
                 STAGE / "config.json")
    spec = importlib.util.spec_from_file_location("tinycmdr_reveal_decay", STAGE / "tinycmdr.py")
    fb = importlib.util.module_from_spec(spec)
    sys.modules["tinycmdr_reveal_decay"] = fb
    spec.loader.exec_module(fb)

    FAILS = []


    def check(cond, what, detail=""):
        if not cond:
            FAILS.append(what)
            print(f"FAIL {what}: {detail}")
        else:
            print(f"ok   {what}")


    def main():
        key = "decay-s1"
        hidden = fb.hidden_tools(key)
        check(bool(hidden), "the session starts with hidden tools", hidden[:5])
        victim = sorted(hidden)[0]

        fb.reveal_tools(key, [victim])
        check(victim in fb.revealed_tools(key), "a revealed tool is visible", victim)
        check(victim in fb.visible_tool_names(key), "...and its schema is sent", victim)

        # ---- expiry
        fb.CONFIG["agent"]["reveal_ttl_secs"] = 0.05
        time.sleep(0.08)
        check(victim not in fb.revealed_tools(key),
              "the schema expires after the TTL without a call", victim)
        check(victim not in fb.visible_tool_names(key),
              "...so the payload stops paying for it", victim)
        check(victim in fb.hidden_tools(key),
              "...and the tool is discoverable again", victim)

        # ---- a call (or a reveal) refreshes it
        fb.reveal_tools(key, [victim])
        check(victim in fb.revealed_tools(key), "a new call re-reveals it", victim)

        # ---- ttl 0 is the old behaviour: never decay
        fb.CONFIG["agent"]["reveal_ttl_secs"] = 0
        time.sleep(0.02)
        check(victim in fb.revealed_tools(key), "ttl 0 keeps the schema for the session",
              victim)

        # ---- reveal_tools still returns the session's known names
        names = fb.reveal_tools(key, ["another_tool"])
        check(victim in names and "another_tool" in names,
              "reveal_tools returns the known names", names)

        # ---- a call is a use: the TTL clock is idle time, not time since the reveal ----
        # Spec (2026-10-03): reveal -> call at t+1700 -> still visible at t+1900, because the
        # call moved the stamp; reveal -> never called -> gone at t+1900, and the call still
        # executes and re-reveals; ttl=0 -> the old never-decay behaviour. The clock is aged
        # directly, so the checks are deterministic.
        bkey = "decay-use"
        tool = "list_tools"               # hidden by default and safe to run with no args
        fb.CONFIG["agent"]["reveal_ttl_secs"] = 1800
        fb.reveal_tools(bkey, [tool])
        fb._revealed[bkey][tool] = time.time() - 1700            # a reveal 1700s old
        _, _, out = fb.Agent._exec_tool(
            fb.AGENT, {"function": {"name": tool, "arguments": {}}},
            {"session_key": bkey})
        check("was not in your tool list" not in out,
              "a call inside the ttl is not treated as hidden")
        fresh = time.time() - fb._revealed[bkey][tool]
        check(fresh < 5, "the call moved the stamp (%ds old)" % int(fresh))
        fb._revealed[bkey][tool] -= 200                          # t+1900 since the reveal
        check(tool in fb.revealed_tools(bkey),
              "...so the used schema is still visible at t+1900")

        ikey = "decay-idle"
        fb.reveal_tools(ikey, [tool])
        fb._revealed[ikey][tool] = time.time() - 1900
        check(tool not in fb.revealed_tools(ikey),
              "an UNUSED reveal is gone at t+1900")
        _, _, out = fb.Agent._exec_tool(
            fb.AGENT, {"function": {"name": tool, "arguments": {}}},
            {"session_key": ikey})
        check(not out.startswith("ERROR"), "the call still executes")
        check("was not in your tool list" in out, "...and re-reveals the tool")
        check("expires after 1800s unused" in out,
              "the banner says how long the schema stays")
        check(tool in fb.revealed_tools(ikey), "the payload carries it again")

        fb.CONFIG["agent"]["reveal_ttl_secs"] = 0
        fb.reveal_tools("decay-off", [tool])
        fb._revealed["decay-off"][tool] = time.time() - 10 ** 6
        check(tool in fb.revealed_tools("decay-off"),
              "ttl 0: an aged reveal never decays (the old behaviour)")
        _, _, out = fb.Agent._exec_tool(
            fb.AGENT, {"function": {"name": tool, "arguments": {}}},
            {"session_key": "decay-off2"})
        check("was not in your tool list" in out and "expires after" not in out,
              "ttl 0 reveals without advertising an expiry")

        # ---- reveal/eject transitions land in the event ledger -------------------
        lkey = "decay-ledger"
        fb.CONFIG["agent"]["event_log"] = True
        fb.CONFIG["agent"]["reveal_ttl_secs"] = 1800
        fb.note_schema_transition(lkey, fb.select_tool_schemas(lkey))     # baseline
        fb.reveal_tools(lkey, [tool])
        fb.note_schema_transition(lkey, fb.select_tool_schemas(lkey))
        fb._revealed[lkey][tool] = time.time() - 4000
        fb.note_schema_transition(lkey, fb.select_tool_schemas(lkey))
        lpath = STAGE / "sessions" / (lkey + ".events.jsonl")
        rows = ([json.loads(x) for x in lpath.read_text(encoding="utf-8").splitlines()
                 if x.strip()] if lpath.exists() else [])
        by_kind = {}
        for r in rows:
            by_kind.setdefault(r.get("kind"), []).append(r.get("names") or [])
        check([tool] in by_kind.get("reveal", []),
              "a reveal is logged with the tool it revealed")
        check([tool] in by_kind.get("eject", []),
              "an eject is logged with the tool it dropped")

        print()
        if FAILS:
            print("%d check(s) failed" % len(FAILS))
            return 1
        print("all reveal-decay checks passed")
        return 0
    return main()


def _suite_test_supersede_prune():
    """A re-read supersedes the older copy - but only when the prefix cache can take it.

A read->edit->read loop keeps every version of a file in context until the whole
conversation is compacted, and tinycmdr measured 46% of reads of its own source
as re-acquisitions. Blanking an older result mutates the prompt PREFIX, which forces the
provider to re-write everything after it - so the pass is gated: now only when the suffix
is small, otherwise at the idle flush when the cache is cold.

    python tests/test_context_surface.py
"""
    import json
    import os
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


    LONG = "read me " * 700          # ~5600 est tokens, well past the prune floor


    def read_call(cid, path, **kw):
        return {"role": "assistant", "content": "",
                "tool_calls": [{"id": cid, "type": "function",
                                "function": {"name": "read_file",
                                             "arguments": json.dumps(dict(path=path, **kw))}}]}


    def result(cid, text):
        return {"role": "tool", "tool_call_id": cid, "content": text}


    def transcript(path, newest_form=None):
        return [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "u1"},
            read_call("a1", str(path)),                          # 2
            result("a1", LONG),                                  # 3 <- older read
            {"role": "user", "content": "u2"},                   # 4
            read_call("a2", str(path), **(newest_form or {})),   # 5 <- newer read
            result("a2", LONG),                                  # 6
            {"role": "assistant", "content": "ok"},              # 7
            {"role": "user", "content": "u3"},
            {"role": "assistant", "content": "x"},
            {"role": "user", "content": "u4"},
            {"role": "assistant", "content": "y"},
        ]


    def main():
        workdir = Path(tempfile.mkdtemp(prefix="fbprune-"))
        try:
            run_scenario.stage_install(workdir, 24000)
            fb = run_scenario.load(workdir)
            A = fb.AGENT
            target = workdir / "target.txt"
            target.write_text("hello\n", encoding="utf-8")

            # ---- a newer full read supersedes the older copy
            fb.CONFIG["agent"]["prune_superseded"] = True
            fb.CONFIG["agent"]["prune_suffix_tokens"] = 100000
            msgs = transcript(target)
            saved = A._supersede_prune(msgs, "prune-key")
            check(msgs[3]["content"] == fb.SUPERSEDED_NOTICE,
                  "the older read is blanked", msgs[3]["content"][:80])
            check(msgs[6]["content"] == LONG, "the newest read survives",
                  msgs[6]["content"][:60])
            check(saved > 0 and len(msgs) == 12,
                  "the reclaim is counted, and the list keeps its length",
                  (saved, len(msgs)))

            # ---- the suffix gate: a big suffix defers the blanking
            fb.CONFIG["agent"]["prune_suffix_tokens"] = 1
            fb.CONFIG["agent"]["prune_idle_secs"] = 10 ** 9
            msgs = transcript(target)
            A._session_path = lambda key: workdir / "missing-session.json"
            saved = A._supersede_prune(msgs, "prune-key")
            check(msgs[3]["content"] == LONG and saved == 0,
                  "a warm cache prefix defers the blanking",
                  (saved, msgs[3]["content"][:40], msgs[6]["content"][:20]))

            # ---- ...but the idle flush blanks it anyway
            fb.CONFIG["agent"]["prune_idle_secs"] = 10
            sess = workdir / "sessions"
            sess.mkdir(exist_ok=True)
            f = sess / "idle-session.json"
            f.write_text("{}", encoding="utf-8")
            old = time.time() - 3600
            os.utime(f, (old, old))
            A._session_path = lambda key: f
            msgs = transcript(target)
            saved = A._supersede_prune(msgs, "idle-session")
            check(msgs[3]["content"] == fb.SUPERSEDED_NOTICE and saved > 0,
                  "the idle flush blanks it with a cold cache",
                  (saved, msgs[3]["content"][:40]))

            # ---- a PARTIAL newer read does not supersede a full older one
            fb.CONFIG["agent"]["prune_suffix_tokens"] = 100000
            msgs = transcript(target, newest_form={"offset": 0, "limit": 5})
            A._supersede_prune(msgs, "prune-key")
            check(msgs[3]["content"] == LONG,
                  "a partial re-read does not blank a full read", msgs[3]["content"][:40])

            # ---- ...but it does supersede the same partial form
            msgs = transcript(target)
            msgs[2] = read_call("a1", str(target), offset=0, limit=5)
            msgs[3] = result("a1", LONG)
            A._supersede_prune(msgs, "prune-key")
            check(msgs[3]["content"] == fb.SUPERSEDED_NOTICE,
                  "the same partial form IS superseded", msgs[3]["content"][:40])

            # ---- a failed read is never blanked, and the newest read is never blanked
            msgs = transcript(target)
            msgs[3] = result("a1", "ERROR: no such file")
            A._supersede_prune(msgs, "prune-key")
            check(msgs[3]["content"].startswith("ERROR"),
                  "an ERROR result is left alone", msgs[3]["content"][:40])
            msgs = transcript(target)
            A._supersede_prune(msgs, "prune-key")
            check(msgs[6]["content"] == LONG, "the newest read is never blanked")

            # ---- _compact runs the pass before the budget check
            msgs = transcript(target)
            A._compact(msgs, "prune-key")
            check(msgs[3]["content"] == fb.SUPERSEDED_NOTICE,
                  "_compact reclaims before deciding to cut", msgs[3]["content"][:40])

            # ---- _drop_oldest_block reports the NET tokens it removed
            small = [{"role": "system", "content": "s"},
                     {"role": "user", "content": "u1"},
                     {"role": "assistant", "content": "a1"},
                     {"role": "tool", "tool_call_id": "x", "content": "t1"},
                     {"role": "user", "content": "u2"},
                     {"role": "assistant", "content": "a2"}]
            removed = A._drop_oldest_block(small, fb.MARK_COMPACT)
            check(isinstance(removed, int) and removed > 0 and len(small) == 4,
                  "_drop_oldest_block returns the net tokens removed",
                  (removed, len(small)))
            check(A._drop_oldest_block(small, fb.MARK_COMPACT) is None,
                  "...and None when only the newest exchange is left")
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

        print()
        if FAILS:
            print("%d check(s) failed" % len(FAILS))
            return 1
        print("all superseded-prune checks passed")
        return 0
    return main()


def main():
    rc = 0
    for name, fn in (("test_transcript", _suite_test_transcript), ("test_reveal_decay", _suite_test_reveal_decay), ("test_supersede_prune", _suite_test_supersede_prune)):
        rc |= _run(name, fn)
    return 1 if rc else 0


if __name__ == "__main__":
    sys.exit(main())

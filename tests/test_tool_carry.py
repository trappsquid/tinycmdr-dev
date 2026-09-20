"""Offline checks for carrying tool results between runs (plan item 7b).

What it exists for, measured on the fleet manager 2026-09-17: the session file holds the
CONVERSATION only (11 messages, no tool results) and `_trim_history` keeps ~5 exchanges by
design, so nothing a tool returned outlives its run. Of 153 reads of the build's own source,
70 (46%) re-acquired a window an EARLIER RUN had already read and 22 (14%) re-read one from
the SAME run; 37 of 48 skill reads were repeats. Not eviction: a 200k budget with zero
compaction events.

The checks that matter: a result recorded in one run is in the NEXT run's payload and not in
its own; the carry is bounded and age-stamped; a file written since it was read says so; and
none of this changes what a tool returns or refuses anything.

    python tests/test_tool_carry.py
"""
import json
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


def check(cond, what):
    if not cond:
        FAILS.append(what)
        print(f"FAIL {what}")
    else:
        print(f"ok   {what}")


class FakeResp:
    def __init__(self, data):
        self._data = data
        self.status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


def text_reply(text):
    return {"choices": [{"message": {"role": "assistant", "content": text},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2}}


def tool_call_reply(name, args, cid="c1"):
    return {"choices": [{"message": {
        "role": "assistant", "content": "",
        "tool_calls": [{"id": cid, "type": "function",
                        "function": {"name": name, "arguments": json.dumps(args)}}]},
        "finish_reason": "tool_calls"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 2}}


def install_stub(fb, seen, script):
    state = {"i": 0}

    def fake_post(url, headers, payload, timeout, grace, cancel_event=None, stream=False):
        seen.append(json.loads(json.dumps(payload)))
        i = state["i"]
        state["i"] += 1
        return FakeResp(script(i, payload))

    fb._post_watchdog = fake_post


def main():
    workdir = Path(tempfile.mkdtemp(prefix="fbcarry-"))
    try:
        run_scenario.stage_install(workdir, 24000)
        fb = run_scenario.load(workdir)
        key = "carry-session"
        src = workdir / "sample.py"
        src.write_text("\n".join("line %d" % i for i in range(1, 401)) + "\n",
                       encoding="utf-8")

        # The release default is OFF (thin evidence, untested failure mode: see the config
        # comment). Everything below this line tests the mechanism as a host enables it.
        check(fb.CONFIG["agent"].get("tool_carry") is False,
              "the carry ships OFF by default and a host turns it on")
        fb.CONFIG["agent"]["tool_carry"] = True
        check(fb.CONFIG["agent"].get("tool_carry_chars") == 8000,
              "and bounded by tool_carry_chars (8000: the declared payload ceiling)")

        # ---- a fresh session carries nothing ----------------------------------
        check(fb.tool_carry_begin(key) == "", "the first run of a session carries nothing")

        # ---- what this run gathers is NOT shown back to it --------------------
        out = fb.tool_read_file({"path": str(src), "offset": 100, "limit": 40},
                                {"session_key": key})
        fb.record_tool_result({"session_key": key}, "read_file",
                              {"path": str(src), "offset": 100, "limit": 40}, out)
        check(fb.tool_carry_block(key) == "",
              "results gathered during this run are not carried into it")

        # ---- the NEXT run sees it ---------------------------------------------
        block = fb.tool_carry_begin(key)
        check("read_file(" in block and "line 101" in block,
              "the next run carries the earlier run's result")
        check("EARLIER runs" in block and "NOT from this run" in block,
              "  and says plainly where it came from")
        check("freshness matters" in block, "  and that output can be stale")

        # ---- a file written since is flagged ----------------------------------
        check("changed since" not in block, "an untouched file is not flagged")
        time.sleep(1.1)
        src.write_text(src.read_text(encoding="utf-8") + "line 401\n", encoding="utf-8")
        fb.tool_carry_begin(key)          # a new run, same entry
        block = fb.tool_carry_block(key)
        check("changed since" in block,
              "a file written since the read is flagged in the carry")

        # ---- the same call twice keeps one copy -------------------------------
        before = len(fb._carry_load(key)["entries"])
        fb.record_tool_result({"session_key": key}, "shell", {"command": "echo hi"}, "hi")
        fb.record_tool_result({"session_key": key}, "shell", {"command": "echo hi"}, "hi2")
        entries = fb._carry_load(key)["entries"]
        same = [e for e in entries if e["tool"] == "shell"]
        check(len(same) == 1 and same[0]["out"] == "hi2",
              f"a repeated identical call keeps the newest copy only ({len(same)})")
        check(len(entries) == before + 1, "  and does not grow the list twice")

        # ---- internal bookkeeping tools are not carried ------------------------
        for name in ("plan", "task", "remember", "list_tools", "find_tools"):
            fb.record_tool_result({"session_key": key}, name, {"action": "x"}, "noise")
        carried_tools = {e["tool"] for e in fb._carry_load(key)["entries"]}
        check(not ({"plan", "task", "remember", "list_tools", "find_tools"} & carried_tools),
              f"internal tools stay out of the carry ({sorted(carried_tools)})")

        # ---- big results are truncated, small ones are not --------------------
        huge = "x" * 10000
        fb.record_tool_result({"session_key": key}, "shell", {"command": "big"}, huge)
        e = [e for e in fb._carry_load(key)["entries"] if e["args"].endswith("command=big")][0]
        check(len(e["out"]) == fb._CARRY_MAX_ENTRY,
              f"a large result is stored truncated ({len(e['out'])} chars)")
        fb.tool_carry_begin(key)
        check("truncated when stored" in fb.tool_carry_block(key),
              "  and the carry says so")

        # ---- the block obeys its budget, newest first -------------------------
        fb.CONFIG["agent"]["tool_carry_chars"] = 3000
        for i in range(6):
            fb.record_tool_result({"session_key": key}, "shell", {"command": "c%d" % i},
                                  ("result %d " % i) + "y" * 1500)
        fb.tool_carry_begin(key)
        small = fb.tool_carry_block(key)
        check(len(small) <= 3200, f"the block obeys tool_carry_chars ({len(small)} chars)")
        check("command=c5" in small and "command=c0" not in small,
              "  newest first, oldest dropped")
        fb.CONFIG["agent"]["tool_carry_chars"] = 16000

        # ---- entries are capped ------------------------------------------------
        for i in range(60):
            fb.record_tool_result({"session_key": key}, "shell", {"command": "n%d" % i}, "z")
        check(len(fb._carry_load(key)["entries"]) <= fb._CARRY_MAX_ENTRIES,
              f"the entry list is capped ({len(fb._carry_load(key)['entries'])})")

        # ---- sessions do not share, and the store survives a restart ----------
        check(fb.tool_carry_block("another-session") == "",
              "another session carries nothing of this one")
        check(Path(fb._carry_path(key)).exists(), "the carry is written to disk")
        fb._CARRY.clear()
        # Without starting a run, the current run's own results stay out (by design), so a
        # fresh process sees them only once the NEXT run begins. Both halves asserted.
        check("command=n59" not in fb.tool_carry_block(key),
              "the current run's results stay out of the carry even after a reload")
        fb._CARRY.clear()
        check("command=n59" in fb.tool_carry_begin(key),
              "a fresh process carries them once the next run starts")

        # ---- off switch -------------------------------------------------------
        fb.CONFIG["agent"]["tool_carry"] = False
        check(fb.tool_carry_begin(key) == "", "tool_carry false carries nothing")
        fb.record_tool_result({"session_key": key}, "shell", {"command": "off"}, "nope")
        fb.CONFIG["agent"]["tool_carry"] = True
        check("command=off" not in fb.tool_carry_block(key),
              "  and records nothing either")

        # ---- parallel tool batches must not corrupt the store ------------------
        # Tool calls run in batches: without a lock the atomic rename raced itself
        # (WinError 5, then the plain-write fallback) - measured 2026-09-17.
        import threading
        fb._CARRY.clear()
        fb.CONFIG["agent"]["tool_carry"] = True

        def hammer(i):
            for j in range(6):
                fb.record_tool_result({"session_key": "race"}, "shell",
                                      {"command": "t%d-%d" % (i, j)}, "out %d %d" % (i, j))

        ts = [threading.Thread(target=hammer, args=(i,)) for i in range(8)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        saved = Path(fb._carry_path("race"))
        ok = False
        try:
            disk = json.loads(saved.read_text(encoding="utf-8"))
            ok = isinstance(disk.get("entries"), list) and len(disk["entries"]) >= 1
        except Exception as e:
            print("       unreadable store:", e)
        check(ok, f"48 concurrent records leave a valid store ({saved.name})")
        check(not saved.with_suffix(".json.tmp").exists(),
              "  and no temp file is left behind")

        # ---- a tool result is never changed by the carrying -------------------
        plain = fb.tool_read_file({"path": str(src), "offset": 0, "limit": 10},
                                  {"session_key": "scratch"})
        fb.CONFIG["agent"]["tool_carry"] = False
        off = fb.tool_read_file({"path": str(src), "offset": 0, "limit": 10},
                                {"session_key": "scratch"})
        fb.CONFIG["agent"]["tool_carry"] = True
        check(plain == off, "the carry does not alter what a tool returns")

        # ---- end to end: it is in the request body of the next run ------------
        fb._CARRY.clear()
        fb.record_tool_result({"session_key": key}, "shell",
                              {"command": "printf CARRIED-MARKER"}, "CARRIED-MARKER")
        seen = []
        install_stub(fb, seen, lambda i, p: text_reply("ok"))
        fb.AGENT.run(key, "and now?")
        blob = "\n".join(str(m.get("content") or "") for m in seen[0]["messages"])
        check("CARRIED-MARKER" in blob, "a carried result reaches the next run's payload")
        check(seen[0]["messages"][1].get("role") == "user"
              and "HARNESS: tool results carried over" in str(seen[0]["messages"][1]["content"]),
              "  as the block right after the system prompt")
        check(seen[0]["messages"][0].get("role") == "system",
              "  and the system prompt still comes first")

        print()
        if FAILS:
            print(f"{len(FAILS)} check(s) FAILED")
            return 1
        print("all tool-carry checks passed")
        return 0
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())

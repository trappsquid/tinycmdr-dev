"""Token estimation: the shape of it, pinned offline, with an opt-in live check.

est_tokens() sizes every compaction and force-shrink decision, so an estimator that
is 2-3x low moves the failure to the moment the context is fullest (audit,
2026-09-22). There is no tokenizer in this repo and no network call in a suite, so
the checks below are properties a real tokenizer agrees with - prose near 4
chars/token, source/JSON denser, CJK much denser - measured on the two samples that
actually recur in an ops run: this build's own source (the model re-reads it more
than any other file) and a JSON tool payload.

A live comparison against a real tokenizer runs ONLY when tinycmdr_TEST_TOKENIZE_URL
is set (a llama.cpp box answers POST /tokenize). Do not point it at a box that is
busy: it is a model server. Left unset, it prints a skip line and the offline
checks are the gate.

    python tests/test_tokens.py
    tinycmdr_TEST_TOKENIZE_URL=http://<lan-box>:8081/tokenize python tests/test_tokens.py
"""
import json
import os
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
TESTS = BASE / "tests"
sys.path.insert(0, str(TESTS))

import run_scenario  # noqa: E402

FAILS = []

PROSE = ("The supervisor waits for the lock to be held and for the chat door to "
         "answer before it counts a start as ready, because a child that cannot be "
         "reached is not a child that is running. ") * 8

CODE = ("def _endpoint_window(self):\n"
        "    now = time.time()\n"
        "    if not hasattr(self, '_window_cache'):\n"
        "        self._window_cache = _detect_window(CONFIG['llm']['base_url'])\n"
        "    return self._window_cache\n") * 8

TOOL_JSON = json.dumps(
    [{"role": "tool", "tool_call_id": "call_abc123", "content": "ERROR: no such file"},
     {"role": "user", "content": "check the disk"},
     {"index": 0, "ok": True, "elapsed_ms": 41}] * 12)

CJK = ("この設定ファイルは、エージェントが読むすべての値を定義します。"
       "既定値は控えめに設定されており、必要に応じて変更できます。") * 8


def check(cond, what):
    if not cond:
        FAILS.append(what)
        print(f"FAIL {what}")
    else:
        print(f"ok   {what}")


def live_count(url, text):
    req = urllib.request.Request(
        url, data=json.dumps({"content": text}).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read().decode("utf-8"))
    toks = data.get("tokens")
    return len(toks) if isinstance(toks, list) else int(toks)


def main():
    workdir = Path(tempfile.mkdtemp(prefix="fbtokens-"))
    try:
        run_scenario.stage_install(workdir, 24000)
        fb = run_scenario.load(workdir)
        est = fb.est_tokens

        check(est("") == 1, "an empty string never estimates zero")
        check(est("x") >= 1, "a single character never estimates zero")
        check(est(PROSE * 2) >= est(PROSE), "the estimate grows with the text")

        prose_ratio = len(PROSE) / est(PROSE)
        check(3.5 <= prose_ratio <= 4.6,
              f"prose stays near 4 chars/token ({prose_ratio:.2f})")
        check(est(CODE) > len(CODE) // 4 * 1.2,
              "code estimates DENSER than len//4 (the bug this exists for)")
        check(est(TOOL_JSON) > len(TOOL_JSON) // 4 * 1.2,
              "a JSON tool payload estimates denser than len//4")
        check(est(CJK) > est(PROSE[:len(CJK)]) * 2,
              "CJK costs at least twice the tokens of the same length of prose")

        # The real recurring sample: this build's own source. 54% of the model's
        # read_file calls were re-reads of tinycmdr.py (2026-09-17 measurement), so
        # this is the text whose estimate the budget is most often computed from.
        src = BASE / "tinycmdr.py"
        if src.exists():
            text = src.read_text(encoding="utf-8", errors="replace")[:200000]
            ratio = len(text) / est(text)
            check(ratio <= 3.5,
                  f"a 200 KB source sample estimates at {ratio:.2f} chars/token "
                  f"(was 4.00 flat)")
        else:
            print("skip the source sample (tinycmdr.py is not beside the suite)")

        # both guards that size the cut must read the same number
        msgs = [{"role": "user", "content": CODE},
                {"role": "assistant", "content": None,
                 "tool_calls": [{"id": "a", "type": "function",
                                 "function": {"name": "shell",
                                              "arguments": '{"command": "ls"}'}}]}]
        total = fb.AGENT._messages_token_est(msgs)
        check(total >= est(CODE) + est('{"command": "ls"}'),
              "the payload estimate counts tool-call arguments too")

        # ---- live, opt-in -------------------------------------------------
        url = (os.environ.get("tinycmdr_TEST_TOKENIZE_URL") or "").strip()
        if not url:
            print("skip live tokenizer comparison (set tinycmdr_TEST_TOKENIZE_URL "
                  "to a llama.cpp /tokenize to run it)")
        else:
            for label, text in (("prose", PROSE), ("code", CODE),
                                ("json", TOOL_JSON), ("cjk", CJK)):
                real = live_count(url, text)
                mine = est(text)
                check(real * 0.75 <= mine <= real * 1.25,
                      f"{label}: estimate {mine} within +/-25% of the tokenizer's "
                      f"{real} ({mine / real:.2f}x)")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print()
    if FAILS:
        print(f"{len(FAILS)} check(s) failed")
        sys.exit(1)
    print("all token-estimate checks passed")


if __name__ == "__main__":
    main()

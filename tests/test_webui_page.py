"""Run the web UI's own page script and grade what it renders.

The HTTP suite (test_webui.py) only ever looked at the SERVER's line buffer.
Both defects the operator reported were in the PAGE's renderer, so they sailed
through every "green" release:

  * the page keyed its DOM nodes by the bare line index, and every run numbers
    its lines from zero - so run 2's first line landed on run 1's first node at
    the TOP of the log ("my previous message got bumped away forever"), and
  * it only ever asked the server for lines at or after the last index it had
    seen, so a line that grew in place (streamed narration growing into the
    final answer) was never re-read once its index was behind that cursor. The
    page kept the truncated snapshot and the real answer never appeared.

Here the real page script is extracted from tinycmdr.py and executed in Node
against a DOM shim and a fake server that mirrors WebRun's line semantics, so
the renderer is graded the way a browser really uses it. Skips cleanly if node
is not installed.

    python tests/test_webui_page.py
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
HARNESS = Path(__file__).resolve().parent / "webui_page_harness.js"
NODE = shutil.which("node") or shutil.which("node.exe")

FAILS = []


def check(cond, what):
    if not cond:
        FAILS.append(what)
        print(f"FAIL {what}")
    else:
        print(f"ok   {what}")


def page_script():
    """The <script> body of WEB_PAGE, straight out of tinycmdr.py."""
    src = (BASE / "tinycmdr.py").read_text(encoding="utf-8")
    start = src.index('WEB_PAGE = """') + len('WEB_PAGE = """')
    end = src.index('"""', start)
    page = src[start:end]
    m = re.search(r"<script>(.*?)</script>", page, re.S)
    if not m:
        raise SystemExit("no <script> block in WEB_PAGE")
    return m.group(1)


def run_page(scenario, script):
    with tempfile.TemporaryDirectory() as d:
        sp = Path(d) / "scenario.json"
        pp = Path(d) / "page.js"
        sp.write_text(json.dumps(scenario), encoding="utf-8")
        pp.write_text(script, encoding="utf-8")
        p = subprocess.run([NODE, str(HARNESS), str(sp), str(pp)],
                           capture_output=True, encoding="utf-8",
                           errors="replace", timeout=120)
    if not (p.stdout or "").startswith("{"):
        raise SystemExit(f"harness failed: {p.returncode} {p.stderr[:400]}")
    return json.loads(p.stdout)


def drawn(res):
    """Every node on the page, in document order, as (class, text)."""
    return [(r["cls"].split()[-1], r["text"]) for r in res["rendered"]]


def where(res, needle, cls=None):
    """Index of the first drawn node whose text contains needle, else -1."""
    for n, (c, t) in enumerate(drawn(res)):
        if needle in t and (cls is None or c == cls):
            return n
    return -1


def count(res, needle, cls=None):
    return sum(1 for c, t in drawn(res) if needle in t and (cls is None or c == cls))


def has(res, needle, cls=None):
    return where(res, needle, cls) >= 0


def main():
    if not NODE:
        print("SKIP node is not installed; cannot run the page renderer")
        return 0
    script = page_script()
    print(f"node {NODE}")
    print(f"page script: {len(script)} chars")

    # -- 1. two runs in one page: the second must not overwrite the first -----
    sc = {
        "runs": [
            [["say", "part one"],
             ["say", "part one part two"], ["final", "part one part two three"]],
            [["final", "answer two"]],
        ],
        "steps": [
            {"kind": "message", "text": "first question", "polls": 14},
            {"kind": "message", "text": "second question", "polls": 14},
        ],
    }
    res = run_page(sc, script)
    check(not res["errors"], f"the page script runs clean ({res['errors'][:1]})")
    check(has(res, "first question"), "the first run's message is still on the page")
    check(has(res, "second question"), "the second run's message is on the page too")
    check(has(res, "answer two"), "the second run's answer is shown")
    check(has(res, "part one part two three"),
          "the first run's answer survived the second run")
    check(count(res, "first question") == 1 and count(res, "second question") == 1,
          "each message is drawn exactly once")
    check(-1 < where(res, "first question") < where(res, "second question"),
          "run 2 is drawn below run 1, not over the top of it")
    check(len(res["rendered"]) == 4,
          f"the transcript holds two messages and two answers, nothing else "
          f"(got {len(res['rendered'])})")
    check(not any(not t for _c, t in drawn(res)),
          "no empty container is left behind in the transcript")

    # -- 2. a line that grows in place must reach its final text --------------
    full = "The sky is blue because of Rayleigh scattering."
    sc = {
        "runs": [[["say", "The sky is blue"],
                  ["say", "The sky is blue because of Rayleigh"],
                  ["final", full]]],
        "steps": [{"kind": "message", "text": "why is the sky blue", "polls": 16}],
    }
    res = run_page(sc, script)
    check(has(res, full), "a growing line ends up showing its full text")
    check(has(res, full, "final"), "and it is drawn as the final answer")
    check(count(res, "The sky is blue") == 1,
          f"the line is replaced in place, not appended ({count(res, 'The sky is blue')})")
    check(not has(res, "because of Rayleigh scattering", "thinking"),
          "the finished text is not left behind as a 'thinking' line")

    # -- 3. mid-run steers: echoed after the run's own message, nothing lost --
    sc = {
        "runs": [
            [["say", "thinking out loud"], ["say", "thinking out loud about disks"],
             ["final", "all done"]],
        ],
        "busy_reply": True,
        "steps": [
            {"kind": "message", "text": "check the disk", "polls": 1},
            {"kind": "type", "text": "also check free space", "polls": 10},
        ],
    }
    res = run_page(sc, script)
    check(not res["errors"], f"the steer path runs clean ({res['errors'][:1]})")
    check(has(res, "check the disk"), "the original message survives a steer")
    check(has(res, "also check free space"), "the steering message is shown")
    check(-1 < where(res, "check the disk") < where(res, "also check free space"),
          "the steer is drawn BELOW the message it steers")
    check(has(res, "all done"), "the answer still arrives after the steer")
    check(count(res, "thinking out loud") == 1,
          "the growing 'thinking' line is not duplicated")
    check(where(res, "thinking out loud") < where(res, "all done"),
          "thinking stays above the answer it preceded")

    # -- 4. tool calls interleaved with thinking, in order --------------------
    sc = {
        "runs": [[["thinking", "I should check the disk"], ["tool", "shell(Get-PSDrive)"],
                  ["tool_done", "✓ shell · 0.4s"], ["thinking", "the disk is fine"],
                  ["final", "C: is 40% free"]]],
        "steps": [{"kind": "message", "text": "check the disk", "polls": 16}],
    }
    res = run_page(sc, script)
    seq = [c for c, _ in drawn(res) if c in ("you", "thinking", "tool", "tool_done", "final")]
    check(seq == ["you", "thinking", "tool", "tool_done", "thinking", "final"],
          f"thinking, tool, result, thinking, answer are in order (got {seq})")
    check(count(res, "I should check the disk") == 1
          and count(res, "the disk is fine") == 1,
          "two separate thinking turns stay separate lines")
    check(where(res, "I should check the disk") < where(res, "shell(Get-PSDrive)")
          < where(res, "✓ shell") < where(res, "the disk is fine")
          < where(res, "C: is 40% free"),
          "each turn sits below the one before it, with no text under a tool result")

    # -- 5. one answer per run, even polled to the end ------------------------
    sc = {
        "runs": [[["final", "one answer"]]],
        "steps": [{"kind": "message", "text": "after reload", "polls": 12},
                  {"kind": "polls", "n": 8}],
    }
    res = run_page(sc, script)
    check(count(res, "one answer") == 1,
          f"an answer is drawn once, not once per poll ({count(res, 'one answer')})")
    check(count(res, "after reload") == 1, "the message is drawn once too")

    # -- 6. reload mid-run: re-attach, never re-post (the reported defect) ----
    # A page that just loaded has no run id. It used to post its message, the
    # server turned that into a steer of the run already going, and the operator
    # watched their own message appear a second time while the first was shoved
    # away. It now asks /api/live and re-attaches.
    sc = {
        "runs": [[["say", "working on it"], ["say", "working on it still"],
                  ["final", "the answer"]]],
        "steps": [
            {"kind": "message", "text": "check the disk", "polls": 1},
            {"kind": "reload"},
            {"kind": "polls", "n": 12},
        ],
    }
    res = run_page(sc, script)
    check(res["pages"] == 2, f"the page really was reloaded ({res['pages']} loads)")
    check(not res["errors"], f"the reloaded page runs clean ({res['errors'][:1]})")
    check(len(res["runs"]) == 1, "a reload does not start a second run")
    check(count(res, "check the disk") == 1,
          f"the reloaded page does not repeat the operator's message "
          f"({count(res, 'check the disk')})")
    check(count(res, "working on it still") == 1,
          f"and does not redraw the line the model is still growing "
          f"({count(res, 'working on it still')})")
    check(has(res, "the answer"), "the re-attached page still receives the answer")
    check(-1 < where(res, "check the disk") < where(res, "the answer"),
          "and draws it below the message, in order")

    print(f"\n{'FAILED: ' + str(len(FAILS)) if FAILS else 'all page renderer checks passed'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())

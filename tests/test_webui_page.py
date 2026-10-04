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
the renderer is graded the way a browser really uses it. Exits 77 (SKIP, never a
green 0) when node is not installed.

    python tests/test_webui_page.py
"""
import ast
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

# No node, nothing graded - and that is not a pass. This suite used to print a SKIP
# line and return 0 (BUGREPORT T4); tests/run_all.py counts 77 as red.
SKIP_EXIT = 77


def check(cond, what):
    if not cond:
        FAILS.append(what)
        print(f"FAIL {what}")
    else:
        print(f"ok   {what}")


def page_script():
    """The <script> body of WEB_PAGE, as the SERVER renders it.

    This used to slice the raw text of tinycmdr.py between the triple quotes,
    which is NOT the page: by the time the server sends it, Python has already
    interpreted the literal. A single backslash-n in there arrives in the
    browser as a REAL newline inside a JS string literal, which is a syntax
    error for the whole <script> - and the raw text still reads as valid
    JavaScript, so this suite stayed green while every browser got a dead page
    (Send did nothing, Enter inserted a newline, the token stayed in the URL
    because the early script never ran). Evaluate the literal instead, so what
    is graded here is the string the browser receives.
    """
    src = (BASE / "tinycmdr.py").read_text(encoding="utf-8")
    page = None
    for node in ast.walk(ast.parse(src)):
        if (isinstance(node, ast.Assign) and node.targets
                and getattr(node.targets[0], "id", "") == "WEB_PAGE"):
            page = ast.literal_eval(node.value)
    if not isinstance(page, str):
        raise SystemExit("WEB_PAGE is not a plain string literal in tinycmdr.py")
    page = page.replace("{{VERSION}}", "harness")
    m = re.search(r"<script>(.*?)</script>", page, re.S)
    if not m:
        raise SystemExit("no <script> block in WEB_PAGE")
    return m.group(1)


def page_served_assets():
    """Every asset the page's routes actually serve, derived from the code.

    The derivation lives in maintenance/package_assets.py - ONE place, because the
    package check, this manifest check and the installer check must agree, and a second
    copy of the derivation is the next list that drifts (see that file's docstring).
    """
    sys.path.insert(0, str(BASE / "maintenance"))
    from package_assets import served_assets
    return served_assets(BASE)


def package_manifest_check():
    """(assets missing from SHIP, stylesheet font refs the server would 404).

    SHIP is read from the source, not imported: importing build-package.py pulls the
    fleet's private inventory (maintenance/private_rules.py), which is not on the
    suite's import path and must never be needed to grade a public package.
    """
    bp = (BASE / "maintenance" / "build-package.py").read_text(encoding="utf-8")
    ship = set()
    for node in ast.walk(ast.parse(bp)):
        if (isinstance(node, ast.Assign) and node.targets
                and getattr(node.targets[0], "id", "") == "SHIP"):
            ship = set(ast.literal_eval(node.value))
    assets, stray = page_served_assets()
    return sorted(a for a in assets if a not in ship), stray


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
    # -- 0. the package ships what the page asks for ---------------------------
    # 1.0.68/1.0.69 shipped /page.css as a 404 (see page_served_assets). This runs
    # before the node gate: it needs no browser and must not skip on a box that
    # cannot render the page.
    missing, stray = package_manifest_check()
    check(not missing,
          f"every asset the page's routes serve is in the package manifest ({missing})")
    check(not stray,
          f"every font the stylesheet asks for is one WEB_FONTS serves ({stray})")

    if not NODE:
        print("SKIP node is not installed; cannot run the page renderer")
        return SKIP_EXIT
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
    check(not res.get("token"),
          f"the handed-over token is dropped from localStorage ({res.get('token')!r})")

    # -- 1b. the token prompt: a good cookie is not a reason to ask ------------
    # The page cannot read the HttpOnly cookie, so it probes GET /api/login first: 200
    # means the cookie authenticates and no prompt appears; 401 means a fresh browser
    # that must be asked. The old boot prompted on every visit regardless - the
    # operator's re-entry report (2026-10-04).
    sc = {"runs": [[["final", "hi"]]],
          "steps": [{"kind": "message", "text": "hello", "polls": 6}],
          "no_token": True, "login_ok": True}
    res = run_page(sc, script)
    check(res["prompts"] == 0,
          f"a browser whose cookie already authenticates is not asked for the token "
          f"({res['prompts']} prompt(s))")
    sc.pop("login_ok")
    res = run_page(sc, script)
    check(res["prompts"] == 1 and res.get("token") is None,
          f"and a browser the server refuses IS asked, and the handover drops it again "
          f"({res['prompts']} prompt(s), token {res.get('token')!r})")

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

    # -- 5. the rail: a new conversation must not inherit the old transcript --
    # The lane used to have exactly one conversation, so "new" was the same as
    # wiped-and-forgotten. Now the page keeps two apart, and a reload of the new
    # one paints only the new one - from the server, not from this browser.
    sc = {
        "runs": [[["final", "old answer"]], [["final", "fresh answer"]]],
        "steps": [
            {"kind": "message", "text": "old question", "polls": 14},
            {"kind": "call", "fn": "newConversation", "polls": 6,
             "args": []},
            {"kind": "message", "text": "fresh question", "polls": 14},
        ],
    }
    res = run_page(sc, script)
    check(not res["errors"], f"the rail scenario runs clean ({res['errors'][:1]})")
    check(res["state"].get("sessionKey") == "web-new1",
          f"the page switched to the conversation the server made "
          f"({res['state'].get('sessionKey')})")
    check(res["state"].get("sessions") and
          any(s["key"] == "web-new1" for s in res["state"]["sessions"]),
          "and that conversation is in the rail")
    check(has(res, "fresh question") and has(res, "fresh answer"),
          "the new conversation holds its own message and its own answer")
    check(not has(res, "old question"),
          "the old conversation's message is not in it")
    check(not has(res, "old answer"), "nor is its answer")
    check(len(res["runs"]) == 2,
          f"the host ran two tasks in two conversations ({len(res['runs'])})")

    # ...and a reload of the new conversation paints it from the server
    sc = {
        "runs": [[["final", "old answer"]], [["final", "fresh answer"]]],
        "steps": [
            {"kind": "message", "text": "old question", "polls": 14},
            {"kind": "call", "fn": "newConversation", "polls": 6, "args": []},
            {"kind": "message", "text": "fresh question", "polls": 14},
            {"kind": "reload", "polls": 6},
        ],
    }
    res = run_page(sc, script)
    check(not res["errors"], f"the reloaded rail is clean ({res['errors'][:1]})")
    check(res["state"].get("sessionKey") == "web-new1",
          f"a reload comes back to the conversation it was in "
          f"({res['state'].get('sessionKey')})")
    check(count(res, "fresh question") == 1,
          f"the message is painted once from the server ({count(res, 'fresh question')})")
    check(not has(res, "old question"),
          "and the other conversation's lines do not bleed into it")

    # -- the copy button: on the answer and on command output, not on chatter --
    # The operator asked for a quick click-to-copy on the agent's boxes, upper right.
    # The clipboard is the thing that has to be right: the timestamp on an answer is
    # chrome and the button's own label is not content, so neither may be copied.
    answer = "here is how:\n\ndocker ps\n\nthat is the list"
    tool_out = "Get-ChildItem C:/temp" + chr(10) + "C:\\temp\\notes.txt"
    sc = {
        "runs": [[["thinking", "thinking about the disk"],
                  ["tool", "shell(Get-ChildItem C:/temp)"],
                  ["tool_done", tool_out],
                  ["final", answer]]],
        "steps": [
            {"kind": "message", "text": "look at the disk", "polls": 14},
            {"kind": "copy", "text": "docker ps", "cls": "final"},
            {"kind": "copy", "text": "Get-ChildItem", "cls": "tool_done"},
            {"kind": "polls", "n": 2},
        ],
    }
    res = run_page(sc, script)
    check(not res["errors"], f"the copy path runs clean ({res['errors'][:1]})")
    fin = [r for r in res["rendered"] if r["cls"].endswith("final")]
    tool = [r for r in res["rendered"] if r["cls"].endswith("tool_done")]
    think = [r for r in res["rendered"] if r["cls"].endswith("thinking")]
    check(fin and fin[0]["hasCopy"], "the answer box carries a copy button")
    check(tool and tool[0]["hasCopy"], "a command-output box carries one too")
    check(think and not think[0]["hasCopy"],
          "narration does not: the transcript stays quiet to read")
    copied = res.get("copied") or []
    check(len(copied) == 2, f"both clicks put text on the clipboard ({len(copied)})")
    check(bool(copied) and copied[0] == answer,
          f"an answer is copied EXACTLY - no timer, no button label "
          f"({copied[:1]!r})")
    check(len(copied) > 1 and copied[1] == tool_out,
          "...and a tool box copies its own output, line breaks and all")

    # -- the token handover: the link carries it, the page uses it and hides it --
    # The installer prints http://127.0.0.1:8787/?token=... and the page is meant
    # to take the token from that link, remember it and leave the address bar
    # clean. Nothing graded this, and the two strings it prints at the prompt
    # were written with a single backslash-n in the Python literal: the browser
    # got a REAL newline inside a JS string, the whole <script> was a syntax
    # error, and the page was dead (Send did nothing, Enter inserted a newline,
    # the token stayed in the URL because the early script never ran at all).
    sc = {
        "query": "?token=from-the-link",
        "runs": [[["final", "the link worked"]]],
        "steps": [{"kind": "message", "text": "hello from the link", "polls": 14}],
    }
    res = run_page(sc, script)
    check(not res["errors"], f"the link page runs clean ({res['errors'][:1]})")
    check(res["prompts"] == 0,
          f"a link that carries the token does not ask for one ({res['prompts']})")
    check(res["auth"] and all(t == "from-the-link" for t in res["auth"]),
          f"every call carries the link's token ({set(res['auth'] or [])})")
    check(has(res, "the link worked"), "and the task it sent came back answered")
    check(res["replaced"] == ["/"],
          f"the token is scrubbed out of the address bar ({res['replaced'][:2]})")

    # ...and with nothing to go on, the page asks - in words that survive the
    # trip through the Python string (the installer's whole handover is in there)
    sc = {
        "no_token": True,
        "runs": [[["final", "typed token worked"]]],
        "steps": [{"kind": "message", "text": "hello with a typed token", "polls": 14}],
    }
    res = run_page(sc, script)
    check(not res["errors"], f"the token prompt page runs clean ({res['errors'][:1]})")
    check(res["prompts"] == 1,
          f"a page with no token anywhere asks once ({res['prompts']})")
    moved = res.get("promptMsg") or ""
    # The token is a secret and lives in .env with the others, on all three platforms
    # (it used to be web-token.txt, which only the Windows installer wrote). The prompt
    # has to name the file the reader actually has.
    check("TINYCMDR_WEB_TOKEN" in moved and ".env" in moved,
          "the prompt names .env / TINYCMDR_WEB_TOKEN, where the token is")
    check("\n\n" in moved and len(moved.splitlines()) > 3,
          f"and it still reads as paragraphs, not one long line ({moved[:40]!r})")
    check(has(res, "typed token worked"), "the token it was given is used")
    check(res["auth"] and all(t == "test-token" for t in res["auth"]),
          f"...on every call that needs it ({set(res['auth'] or [])})")

    # -- 14. a dead chat lane must be visible ON THE PAGE -----------------------
    # The incident (a live install, 2026-09-28): the process was up, /api/health said ok, the
    # page looked like a normal chat box - and the bot could not hear anybody. The page
    # already fetches /api/health for the version, so the answer it was ignoring is the
    # one that matters.
    dead = {"health": {"ok": False, "version": "harness", "pid": 1,
                       "lanes": {"mattermost": {"state": "failed", "failed_starts": 511,
                                                "detail": "401 Invalid or expired session"},
                                 "web": {"state": "up"}}},
            "runs": [[["final", "still answering on the page"]]],
            "steps": [{"kind": "message", "text": "hi", "polls": 4}]}
    res = run_page(dead, script)
    check("bad" in (res.get("ver") or {}).get("cls", ""),
          f"a dead lane marks the header ({res.get('ver')})")
    check("brand-version" in (res.get("ver") or {}).get("cls", ""),
          f"...and the marker keeps its own class ({res.get('ver')})")
    check((res.get("title") or "").startswith("CHAT LANE DOWN"),
          f"...and the tab title says it ({res.get('title')!r})")
    _warn = res.get("warn") or {}
    check("show" in (_warn.get("cls") or ""),
          f"...and the banner is actually displayed ({_warn.get('cls')!r})")
    check("connection-banner" in (_warn.get("cls") or ""),
          f"...carrying the design's own class, not just 'show' ({_warn.get('cls')!r})")
    check("mattermost" in (_warn.get("detail") or "") and "401" in (_warn.get("detail") or ""),
          f"...and the detail names the lane and the reason ({( _warn.get('detail') or '')[:80]!r})")
    check("401" in ((res.get("ver") or {}).get("title") or ""),
          f"...and the marker explains itself on hover ({(res.get('ver') or {}).get('title')!r})")

    alive = {"runs": [[["final", "hi"]]], "steps": [{"kind": "message", "text": "hi", "polls": 4}]}
    res = run_page(alive, script)
    check("bad" not in (res.get("ver") or {}).get("cls", "")
          and not (res.get("note") or {}).get("text"),
          f"a healthy bot shows NO banner ({(res.get('ver'), res.get('note'))})")
    check((res.get("title") or "").startswith("tinycmdr"),
          f"...and its tab title is just the app ({res.get('title')!r})")

    # -- the lane banner is a notification, not a fixture -----------------------
    # Operator, 2026-10-04: "make that a closeable notification, not a permanent banner".
    # Dismissing mutes THAT wording; the header marker and the tab title stay, because
    # "I have read this" is not "stop telling me the bot is deaf".
    dead = {"health": {"ok": False, "version": "harness", "pid": 1,
                       "lanes": {"mattermost": {"state": "failed",
                                                "detail": "401 Invalid or expired session"},
                                 "web": {"state": "up"}}},
            "runs": [], "steps": [{"kind": "polls", "n": 3}]}
    res = run_page(dead, script)
    check("show" in (res.get("warn") or {}).get("cls", ""),
          f"a dead lane shows the banner ({res.get('warn')})")
    check("dispatched" in (res.get("warn") or {}).get("text", ""),
          "the banner says what it MEANS (messages may not be dispatched)")
    check("401" in (res.get("warn") or {}).get("detail", ""),
          "and the technical reason is one click away")

    sc = dict(dead, steps=[{"kind": "polls", "n": 3},
                           {"kind": "click", "id": "lanedismiss", "polls": 3},
                           {"kind": "polls", "n": 3}])
    res = run_page(sc, script)
    check(not res["errors"], f"the dismiss path runs clean ({res['errors'][:1]})")
    check("show" not in (res.get("warn") or {}).get("cls", ""),
          f"the x dismisses the banner ({res.get('warn')})")
    check("bad" in (res.get("ver") or {}).get("cls", "")
          and (res.get("title") or "").startswith("CHAT LANE DOWN"),
          "while the header marker and the tab title still say it")

    sc = dict(dead, steps=[{"kind": "polls", "n": 3},
                           {"kind": "click", "id": "lanedismiss", "polls": 3},
                           {"kind": "health", "polls": 1,
                            "value": {"ok": False, "version": "harness", "pid": 1,
                                      "lanes": {"mattermost": {
                                          "state": "failed", "detail": "502 Bad Gateway"},
                                          "web": {"state": "up"}}}},
                           {"kind": "call", "fn": "versionCheck", "polls": 3}])
    res = run_page(sc, script)
    check("show" in (res.get("warn") or {}).get("cls", ""),
          f"a DIFFERENT failure speaks again ({res.get('warn')})")
    check("502" in (res.get("warn") or {}).get("detail", ""), "with the new reason")

    # -- the pavilion: empty state, details, retry, the amber notice, a failed load -----
    # Operator brief, 2026-10-04 ("modern imperial command pavilion"): the empty area is an
    # empty STATE with a way in, the lane banner's technical reason is one click away, a
    # config edit that has not applied is a separate amber notice, and a conversation that
    # will not load is a card with a way out - not a blank pane.
    res = run_page({"runs": [], "steps": [{"kind": "polls", "n": 3}]}, script)
    check(not (res.get("empty") or {}).get("hidden", True),
          f"an empty transcript shows the empty state ({res.get('empty')})")
    check(str((res.get("empty") or {}).get("img", "")).endswith(".png"),
          f"and the empty state carries the character ({(res.get('empty') or {}).get('img')!r})")
    res = run_page({"runs": [[["final", "an answer"]]],
                    "steps": [{"kind": "message", "text": "a question", "polls": 12}]},
                   script)
    check((res.get("empty") or {}).get("hidden") is True,
          "and it gets out of the way once there is a transcript")

    res = run_page(dict(dead, steps=[{"kind": "polls", "n": 3},
                                     {"kind": "click", "id": "lanemore", "polls": 2}]),
                   script)
    check((res.get("warn") or {}).get("detailShown") is True,
          "Details reveals the technical reason")
    before = res.get("healthFetches")
    res = run_page(dict(dead, steps=[{"kind": "polls", "n": 3},
                                     {"kind": "click", "id": "laneretry", "polls": 4}]),
                   script)
    after = res.get("healthFetches")
    check(after > before, f"Retry asks the server again ({before} -> {after})")
    check(res.get("retryLabel") == "Retry", "and the button never sticks on 'checking...'")

    sc = {"health": {"ok": True, "version": "harness",
                     "config_changed": "port changed in config.json; restart to apply"},
          "runs": [], "steps": [{"kind": "polls", "n": 3}]}
    res = run_page(sc, script)
    check("show" in (res.get("config") or {}).get("cls", "")
          and "restart to apply" in (res.get("config") or {}).get("text", ""),
          f"a pending config edit is its own amber notice ({res.get('config')})")
    check("connection-banner-amber" in (res.get("config") or {}).get("cls", "")
          and "connection-banner" in (res.get("config") or {}).get("cls", ""),
          f"...on the amber variant, base class and all ({res.get('config')})")
    check("show" not in (res.get("warn") or {}).get("cls", ""),
          "and NOT the error banner (different problems, different banners)")
    sc = dict(sc, steps=[{"kind": "polls", "n": 3},
                         {"kind": "click", "id": "configdismiss", "polls": 3}])
    res = run_page(sc, script)
    check("show" not in (res.get("config") or {}).get("cls", ""),
          "the config notice is dismissible too")

    res = run_page({"runs": [], "session_status": 500, "steps": [{"kind": "polls", "n": 4}]},
                   script)
    check(not res.get("errors"), f"the failed-load path runs clean ({res.get('errors')[:1]})")
    check(has(res, "Could not load this conversation"),
          "a failed load is a card, not a blank pane")
    check(has(res, "Retry") and has(res, "Start a new conversation"),
          "with both ways out")

    # -- the fragment carry: #token= never reaches the server, the page uses it ---
    # The installer prints the link with the token in the URL FRAGMENT (it is not
    # sent in the request line, so it cannot land in the server's log or a Referer).
    # The ?query form still works for links from older installs.
    sc = {
        "hash": "#token=from-the-fragment",
        "no_token": True,
        "runs": [[["final", "the fragment worked"]]],
        "steps": [{"kind": "message", "text": "hello", "polls": 14}],
    }
    res = run_page(sc, script)
    check(not res["errors"], f"the fragment page runs clean ({res['errors'][:1]})")
    check(res["prompts"] == 0,
          f"a fragment link does not ask for a token ({res['prompts']})")
    check(res.get("token") is None,
          f"the fragment token is handed over for a cookie, then dropped from "
          f"localStorage ({res.get('token')!r})")
    check(res["auth"] and all(t == "from-the-fragment" for t in res["auth"]),
          f"every call carries it ({set(res['auth'] or [])})")
    check(res["replaced"] == ["/"],
          f"and the address bar is scrubbed ({res['replaced'][:2]})")
    check(has(res, "the fragment worked"), "the task it sent came back answered")

    # -- a 'file' line is a download link, not prose ------------------------------
    sc = {
        "runs": [[["file", "uploads/123_report final.txt"], ["final", "sent"]]],
        "steps": [{"kind": "message", "text": "send me the report", "polls": 14}],
    }
    res = run_page(sc, script)
    check(not res["errors"], f"the file-line page runs clean ({res['errors'][:1]})")
    fin = [r for r in res["rendered"] if r["cls"].endswith("file")]
    check(bool(fin), f"an offered file draws its own line ({res['rendered']})")
    check(fin and "report final.txt" in fin[0]["text"] and "\U0001F4CE" in fin[0]["text"],
          f"the line names the file ({fin[:1]})")
    check(fin and fin[0]["hasFileLink"],
          "and carries the download anchor that fetches it with the token")

    # -- the upload path: the file button POSTs, the path lands in the composer --
    sc = {
        "runs": [],
        "steps": [{"kind": "call", "fn": "uploadFiles", "polls": 4,
                   "args": [[{"name": "report final.txt"}]]}],
    }
    res = run_page(sc, script)
    check(not res["errors"], f"the upload page runs clean ({res['errors'][:1]})")
    ups = res.get("uploads") or []
    check(ups and ups[0]["name"] == "report final.txt",
          f"the file reaches /api/upload under its own name ({ups[:1]})")
    check(ups and ups[0]["token"] == "test-token", f"with the token ({ups[:1]})")
    check("uploads/123_report final.txt" in (res.get("composer") or ""),
          f"and the saved path lands in the composer ({res.get('composer')!r})")

    print(f"\n{'FAILED: ' + str(len(FAILS)) if FAILS else 'all page renderer checks passed'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())

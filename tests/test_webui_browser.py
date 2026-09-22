"""Browser-level checks for the local web UI: the 8787 page, in a real browser.

Everything in tests/test_webui.py drives the HTTP endpoints. That was not enough:
the page kept shipping defects that only exist in a browser - a steering message
drawn where an older one was, the answer painted above and below the tool lines,
the operator's message twice after a reload. So this suite drives Edge/Chromium
against the real server and asserts on the DOM.

The single invariant every scenario leans on: the transcript in the browser must
equal the server's ordered line list, once per line, in order - and the DOM is
rebuilt by reconciling against stable line uids, so a line can never move to the
top, never be overwritten by another run's line, and never be drawn twice.

Deterministic and offline: the model is stubbed and blocked on events this suite
controls, so "steer mid-run" and "stop mid-run" don't race a stub.

    python tests/test_webui_browser.py

Skips (does not fail) when playwright or an Edge/Chromium binary is missing, and
in a console build with no web layer.
"""
import json
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
TESTS = BASE / "tests"
sys.path.insert(0, str(TESTS))

import run_scenario  # noqa: E402
import test_webui as tw  # noqa: E402  (stub helpers: one place, one stub)

FAILS = []
TOKEN = "test-token-browser"

DUMP = r"""
(() => {
  const log = document.getElementById('log');
  const runs = [...log.querySelectorAll(':scope > .run')].map(r => ({
    run: r.dataset.run,
    // txt is what the line READS as: the timer stamp is shown to the reader, but a
    // copy button's own label is chrome. boxText() drops both, so the stamp is put
    // back here - otherwise adding a button would look like the page had changed
    // the agent's words.
    lines: [...r.children].map(d => {
      const kids = Array.from(d.children || []);
      const st = kids.find(x => x.className === 'stamp');
      const body = (typeof boxText === 'function') ? boxText(d) : d.textContent;
      // the renderer's own helper classes are not the line's kind: 'msg final copyable'
      // is a final line, and this report is compared against the server's kinds
      const cls = (d.className || '').split(' ')
        .filter(c => c && c !== 'copyable').join(' ');
      return {cls: cls, txt: (st ? st.textContent : '') + body};
    })}));
  const stop = document.getElementById('stop');
  return {runs: runs, busy: document.body.classList.contains('busy'),
          state: document.getElementById('state').textContent,
          ver: document.getElementById('ver').textContent,
          note: document.getElementById('notetext').textContent,
          noteShown: document.getElementById('note').classList.contains('show'),
          stopVisible: !!(stop && getComputedStyle(stop).display !== 'none'),
          sendVisible: !!(document.getElementById('send') &&
                          getComputedStyle(document.getElementById('send')).display !== 'none')};
})()
"""


def check(cond, what):
    if not cond:
        FAILS.append(what)
        print(f"FAIL {what}")
    else:
        print(f"ok   {what}")


def http(url, payload=None, token=TOKEN, timeout=20):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data)
    req.add_header("Content-Type", "application/json")
    if token is not None:
        req.add_header("X-Tinycmdr-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {}


def server_lines(base, run_id):
    code, j = http(f"{base}/api/events?run_id={run_id}&since=0")
    return code, j.get("lines", [])


def expected_text(l):
    """What the page should show for a line: the stamp as JS would print it, then the text.

    JavaScript's String(0.0) is "0" while Python's str(0.0) is "0.0", so an answer that
    lands at exactly 0 elapsed (or any whole number of seconds) has to be formatted the
    way the browser does, or the comparison fails on a page that is right.
    """
    stamp = ""
    if l["kind"] in ("final", "thinking"):
        num = str(l["t"])
        if num.endswith(".0"):
            num = num[:-2]
        stamp = num + "s"
    return stamp + l["text"]


def run_lines(dump, run_id):
    for r in dump["runs"]:
        if r["run"] == run_id:
            return [(d["cls"].replace("msg ", "").strip(), d["txt"]) for d in r["lines"]]
    return None


def compare(base, page, run_id, label):
    """The transcript in the browser == the server's ordered lines, exactly."""
    code, srv = server_lines(base, run_id)
    if code != 200:
        check(False, f"{label}: server would not hand back the run ({code})")
        return
    dom = run_lines(page.evaluate(DUMP), run_id)
    want = [(l["kind"], expected_text(l)) for l in srv]
    check(dom is not None, f"{label}: the run has its own container in the transcript")
    if dom is None:
        return
    check(len(dom) == len(want),
          f"{label}: line count matches the server ({len(dom)} drawn, {len(want)} on the server)")
    check([c for c, _ in dom] == [c for c, _ in want],
          f"{label}: line kinds are in the server's order")
    check([t for _, t in dom] == [t for _, t in want],
          f"{label}: line text matches the server, in order, once each")
    for kind, text in dom:
        check(dom.count((kind, text)) == 1, f"{label}: {kind} line appears once ({text[:40]!r})")
    seen_final = [i for i, (c, _) in enumerate(dom) if c == "final"]
    first_tool = next((i for i, (c, _) in enumerate(dom) if c.startswith("tool")), None)
    if seen_final and first_tool is not None:
        check(min(seen_final) > first_tool,
              f"{label}: no answer bubble above the tool lines (the run is not "
              f"talking above and below itself)")
    if seen_final:
        check(seen_final[-1] == len(dom) - 1,
              f"{label}: the answer is the last thing the run produced")


def wait_for(fn, timeout=30.0, interval=0.05):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(interval)
    return None


def main():
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:  # noqa: BLE001
        print(f"skipped: playwright not importable ({e})")
        return 0

    workdir = Path(tempfile.mkdtemp(prefix="fbwebb-"))
    fb = None
    pw = None
    browser = None
    try:
        run_scenario.stage_install(workdir, 24000)
        fb = run_scenario.load(workdir)
        if not hasattr(fb, "WEB_PAGE"):
            print("skipped: this build has no web layer (console build)")
            return 0

        fb.CONFIG["web"] = {"enabled": True, "port": 0, "host": "127.0.0.1",
                            "token": TOKEN}
        srv = fb.run_webui()
        if srv is None:
            print("FAIL run_webui returned None")
            return 1
        base = f"http://127.0.0.1:{srv.server_address[1]}"

        # -- the served page: no cache, version baked in, reconciling client --
        req = urllib.request.Request(base + "/")
        with urllib.request.urlopen(req, timeout=15) as r:
            html = r.read().decode()
            hdrs = {k.lower(): v for k, v in r.headers.items()}
        check("no-store" in hdrs.get("cache-control", ""),
              "the page is served no-store, so a browser cannot keep running "
              "yesterday's client after the server was fixed")
        check(fb.VERSION in html, "the page carries the version it was built from")
        check("{{VERSION}}" not in html, "...as a substitute, not a placeholder")
        check("function reconcile(" in html, "the page reconciles the transcript")
        check("(runId+'#'+i)" not in html,
              "the page no longer keys lines by index (that is how a new line "
              "overwrote an older one at the top)")
        check("/api/live" in html, "a reloaded page asks which run is live")
        check("copyb" in html and "attachCopy" in html,
              "the page ships click-to-copy on the agent's boxes")
        check("execCommand" in html,
              "...through the document, because the LAN page is plain http")

        pw = sync_playwright().start()
        try:
            browser = pw.chromium.launch(channel="msedge", headless=True)
        except Exception as e:  # noqa: BLE001
            print(f"skipped: no Edge/Chromium to drive ({str(e)[:120]})")
            return 0

        ctx = browser.new_context(viewport={"width": 900, "height": 800})
        page = ctx.new_page()
        page.add_init_script(f"localStorage.fb_token = {json.dumps(TOKEN)};")
        page.goto(base + "/", wait_until="domcontentloaded")
        time.sleep(0.8)
        st = page.evaluate(DUMP)
        check(st["ver"] == fb.VERSION,
              f"the header shows the server version ({st['ver']})")
        check("no chat server needed" in st["note"],
              "the page explains itself without a chat server")

        # -- A. one run, start to finish ------------------------------------
        seen = []
        ev = {k: threading.Event() for k in ("first", "go1")}
        tw.make_stub(fb, seen, [
            lambda p: (ev["first"].set(), ev["go1"].wait(30),
                       tw.tool_call_reply("shell", {"command": "hostname"}))[2],
            lambda p: tw.text_reply("The hostname is the manager box and nothing else ran."),
        ])
        page.fill("#in", "first task: tell me the hostname")
        page.press("#in", "Enter")
        check(wait_for(ev["first"].is_set), "the run reached the model")
        st = page.evaluate(DUMP)
        check(st["busy"], "the page knows a run is going")
        check(st["stopVisible"], "Stop is reachable mid-run")
        code, live = http(f"{base}/api/live")
        check(code == 200 and live.get("run_id"), "/api/live names the live run")
        run_a = live["run_id"]
        ev["go1"].set()
        check(wait_for(lambda: http(f"{base}/api/events?run_id={run_a}&since=0")[1]
                       .get("done")), "the run finished")
        time.sleep(1.0)                      # let the page take the last poll
        compare(base, page, run_a, "A: simple run")
        st = page.evaluate(DUMP)
        check(not st["busy"] and not st["stopVisible"],
              "the page goes idle again when the run ends")
        check(st["sendVisible"], "Send stays visible (a hidden button mid-run "
                                 "left a phone with no way to steer)")
        # -- the run's own line: working -> Done, IN the transcript -------------
        # It used to live in the page header alone, and the header goes back to
        # "idle" on reload - so a reloaded page held no record of how the run
        # ended, while the chat lane keeps its Done post in the thread for good.
        dom_a = run_lines(page.evaluate(DUMP), run_a) or []
        status = [t for c, t in dom_a if c == "checkin"]
        check(len(status) == 1,
              f"A: the run has exactly one own line in the transcript ({len(status)})")
        check(status and status[0].startswith("✅ Done —"),
              f"A: and it reads as the Done line ({status[:1]})")
        check(status and " step(s) in " in status[0] and "model `" in status[0],
              "A: with the step count, the elapsed time and the model on it")
        check(dom_a and dom_a[-1][0] != "checkin",
              "A: the answer is still what the eye lands on last, not the Done line")

        # -- B. a steering message typed mid-run ----------------------------
        seen = []
        ev = {k: threading.Event() for k in ("first", "go1", "second", "go2")}
        tw.make_stub(fb, seen, [
            lambda p: (ev["first"].set(), ev["go1"].wait(30),
                       tw.tool_call_reply("shell", {"command": "Get-Date"}))[2],
            lambda p: (ev["second"].set(), ev["go2"].wait(30),
                       tw.text_reply("Date checked, plus the free space you added."))[2],
        ])
        page.fill("#in", "second task: what is the date")
        page.press("#in", "Enter")
        check(wait_for(ev["first"].is_set), "B: the run reached the model")
        page.fill("#in", "steer: also check the free space")
        page.press("#in", "Enter")
        code, live = http(f"{base}/api/live")
        run_b = live["run_id"]
        check(wait_for(lambda: any(l["kind"] == "you" and "free space" in l["text"]
                                   for l in server_lines(base, run_b)[1])),
              "B: the steering message reached the run")
        time.sleep(1.0)
        dom = run_lines(page.evaluate(DUMP), run_b) or []
        yous = [i for i, (c, t) in enumerate(dom) if c == "you"]
        check(len(yous) == 2, f"B: two operator lines, not more ({len(yous)})")
        check(any("free space" in t for _, t in dom),
              "B: the steering message is in the transcript")
        check(sum(1 for _, t in dom if "free space" in t) == 1,
              "B: it is there exactly once (the client used to steer AND resend it)")
        check(yous[-1] < next((i for i, (c, t) in enumerate(dom) if c == "final"),
                             len(dom)), "B: it sits above the answer, not on top of it")
        ev["go1"].set()
        check(wait_for(ev["second"].is_set), "B: the run went on to a second turn")
        check(any("free space" in tw.blob(p) for p in seen),
              "B: the steering message reached the model in the next request")
        ev["go2"].set()
        check(wait_for(lambda: http(f"{base}/api/events?run_id={run_b}&since=0")[1].get("done")),
              "B: the run finished")
        time.sleep(1.0)
        compare(base, page, run_b, "B: steer mid-run")
        dom = run_lines(page.evaluate(DUMP), run_b) or []
        check(dom and dom[0][0] == "you", "B: the run still starts with the operator's line")
        keep = page.evaluate("document.querySelectorAll('#log .msg').length")

        # -- C. reload in the middle of a run -------------------------------
        seen = []
        ev = {k: threading.Event() for k in ("first", "go1")}
        tw.make_stub(fb, seen, [
            lambda p: (ev["first"].set(), ev["go1"].wait(30),
                       tw.text_reply("Reload-safe answer."))[2],
        ])
        page.fill("#in", "third task: survives a reload")
        page.press("#in", "Enter")
        check(wait_for(ev["first"].is_set), "C: the run reached the model")
        code, live = http(f"{base}/api/live")
        run_c = live["run_id"]
        page.reload(wait_until="domcontentloaded")
        time.sleep(1.5)
        st = page.evaluate(DUMP)
        check(st["busy"] and st["stopVisible"],
              "C: the reloaded page re-attached to the live run (it used to guess "
              "by posting a message, which duplicated the operator's line)")
        check(st["noteShown"] and "queued" not in st["note"],
              f"C: no phantom 'queued into it' notice ({st['note'][:60]!r})")
        ev["go1"].set()
        check(wait_for(lambda: http(f"{base}/api/events?run_id={run_c}&since=0")[1].get("done")),
              "C: the run finished after the reload")
        time.sleep(1.0)
        compare(base, page, run_c, "C: after a reload")
        dom = run_lines(page.evaluate(DUMP), run_c) or []
        check(sum(1 for _, t in dom if "survives a reload" in t) == 1,
              "C: the operator's message appears once after a reload, not twice")
        status_c = [t for c, t in dom if c == "checkin"]
        check(status_c and status_c[0].startswith("✅ Done —"),
              f"C: the run's Done line survived the reload ({status_c[:1]}) - it "
              f"belongs to the run, not to the page's header")

        # -- D. stop mid-run -------------------------------------------------
        seen = []
        ev = {k: threading.Event() for k in ("first", "go1")}
        tw.make_stub(fb, seen, [
            lambda p: (ev["first"].set(), ev["go1"].wait(30),
                       tw.tool_call_reply("shell", {"command": "whoami"}))[2],
            lambda p: tw.text_reply("should never be reached"),
        ])
        page.fill("#in", "fourth task: stop this one")
        page.press("#in", "Enter")
        check(wait_for(ev["first"].is_set), "D: the run reached the model")
        code, live = http(f"{base}/api/live")
        run_d = live["run_id"]
        page.click("#stop")
        ev["go1"].set()
        check(wait_for(lambda: http(f"{base}/api/events?run_id={run_d}&since=0")[1].get("done")),
              "D: stop ended the run")
        time.sleep(1.0)
        ks = [l["kind"] for l in server_lines(base, run_d)[1]]
        check("system" in ks, "D: the stop is recorded in the transcript")
        check(len(seen) == 1, f"D: no further model call after stop ({len(seen)})")
        st = page.evaluate(DUMP)
        check(not st["busy"], "D: the page leaves the busy state")
        compare(base, page, run_d, "D: after stop")

        # -- E. the reconciler itself ---------------------------------------
        res = page.evaluate(r"""
        (() => {
          const id = 'recon-unit';
          const L = (t, k) => ({uid: 'recon-unit#' + t, i: t, kind: k || 'say', t: 1, text: 'line ' + t});
          reconcile(id, [L(0), L(1), L(2)]);
          const c = document.querySelector('[data-run="recon-unit"]');
          const first = c.children.length;
          reconcile(id, [L(0), L(1), L(2)]);              // same state again
          const twice = c.children.length;
          const idOf = n => [...c.children].indexOf(n);
          const node = c.children[1];
          reconcile(id, [L(0), Object.assign(L(1), {text: 'line 1 grew'}), L(2)]);
          const grew = c.children.length, moved = idOf(node), txt = node.textContent;
          reconcile(id, [L(0), Object.assign(L(1), {text: 'line 1 grew'}), L(2),
                         {uid: 'recon-unit#3', i: 3, kind: 'final', t: 9, text: 'done'}]);
          const box = c.children[c.children.length-1];
          const st = Array.from(box.children || []).find(x => x.className === 'stamp');
          const added = c.children.length;
          const last = (st ? st.textContent : '')
            + ((typeof boxText === 'function') ? boxText(box) : box.textContent);
          reconcile(id, [L(0), L(1)]);
          const dropped = c.children.length;
          return {first, twice, grew, moved, txt, added, last, dropped};
        })()
        """)
        check(res["first"] == 3 and res["twice"] == 3,
              "E: reconciling the same lines twice draws nothing twice")
        check(res["grew"] == 3 and res["moved"] == 1 and res["txt"] == "line 1 grew",
              "E: a line that grows is repainted where it already sits")
        check(res["added"] == 4 and res["last"] == "9sdone",
              "E: a new line lands at the end, with its timestamp")
        check(res["dropped"] == 2,
              "E: a line the server dropped is removed from the page")

        # -- G. the copy button on an answer, in a real browser ---------------
        # The operator asked for click-to-copy in the upper right of the agent's
        # boxes. Over plain http (the LAN) there is no navigator.clipboard at all,
        # so this grades the path that really runs there, in a real engine, and
        # what the clipboard would hold: the answer exactly, without the timer
        # stamp and without the button's own label.
        G_ANSWER = ("install steps:" + chr(10) + chr(10) + "docker compose up -d"
                    + chr(10) + chr(10) + "then open the page")
        seen = []
        tw.make_stub(fb, seen, [lambda p: tw.text_reply(G_ANSWER)])
        page.fill("#in", "G: give me the install steps")
        page.press("#in", "Enter")
        check(wait_for(lambda: len(seen) == 1), "G: the run reached the model")
        # the run id comes from the PAGE: asking /api/live races the stub's instant
        # reply, and a null id makes the wait below time out on a healthy run
        run_g = page.evaluate("() => { const n = [...document.querySelectorAll('[data-run]')].pop();"
                              " return n ? n.dataset.run : null; }")
        check(bool(run_g), f"G: the answer has its own run in the transcript ({run_g})")
        check(wait_for(lambda: http(f"{base}/api/events?run_id={run_g}&since=0")[1].get("done")),
              "G: the run finished")
        time.sleep(0.8)
        compare(base, page, run_g, "G: the answer the button copies from")
        try:
            page.context.grant_permissions(["clipboard-read", "clipboard-write"])
        except Exception:
            pass
        res = page.evaluate(r"""
        (() => {
          const boxes = Array.from(document.querySelectorAll('.msg.final'));
          const box = boxes[boxes.length - 1];        // the newest answer, not the first
          if (!box) { return {err: 'no answer box'}; }
          const btn = box.querySelector('.copyb');
          if (!btn) { return {err: 'the answer has no copy button'}; }
          const r = btn.getBoundingClientRect(), b = box.getBoundingClientRect();
          const corner = (r.right > b.right - 90) && (r.top < b.top + 44) && (r.right <= b.right + 1);
          const would = boxText(box);
          btn.click();
          return {err: null, corner: corner, label: btn.textContent,
                  done: btn.className.indexOf('done') >= 0, would: would};
        })()
        """)
        check(res.get("err") is None, f"G: {res.get('err') or 'the answer carries a copy button'}")
        check(res.get("corner"), "G: it sits in the box's upper right corner")
        check(res.get("label") == "copied" and res.get("done"),
              f"G: clicking it copies on a plain-http page ({res.get('label')!r})")
        check(res.get("would") == G_ANSWER,
              f"G: and the copy holds the answer exactly - no timer, no button "
              f"label ({res.get('would')!r})")
        try:
            clip = page.evaluate("() => navigator.clipboard.readText()")
        except Exception:
            clip = ""
        if clip:
            # Windows normalises a clipboard copy to CRLF on the way out
            check(clip.replace("\r\n", "\n") == G_ANSWER,
                  f"G: the system clipboard really holds it ({clip[:40]!r})")
        else:
            print("     (this engine will not hand the clipboard back; the click "
                  "path was graded)")
        page.evaluate("() => { const b = document.querySelector('.copyb'); if (b) b.blur(); }")

        # -- F. crash recovery: no chat server, no plumbing needed -----------
        code, j = http(f"{base}/api/health", token=None)
        check(code == 200 and j.get("version") == fb.VERSION,
              "F: health answers without a token (the page's handshake needs it)")
        check(keep > 0, "F: earlier runs are still in the transcript, not wiped by "
                        "the next message")
    finally:
        try:
            if browser is not None:
                browser.close()
        except Exception:
            pass
        try:
            if pw is not None:
                pw.stop()
        except Exception:
            pass

    print()
    if FAILS:
        print(f"{len(FAILS)} FAILED:")
        for f in FAILS:
            print("  -", f)
        return 1
    print("all browser checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

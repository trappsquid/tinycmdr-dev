"""Confirm the web-UI patch is present in tinycmdr.py.

Run:  python maintenance/check-webui-patch.py [path]
Exit 0 only when every marker is there - a parallel edit_file write clobbered
one of these once and the suite still passed, so the patch itself gets checked.

This is the ONLY check that guards the client half of the page: the Python
suites drive the HTTP endpoints and never look at the JavaScript inside
WEB_PAGE. (tests/test_webui_page.py now runs that JavaScript in Node; that one
catches behaviour, this one catches the patch being reverted wholesale.)
"""
import ast
import sys
from pathlib import Path

src = Path(sys.argv[1] if len(sys.argv) > 1 else "tinycmdr.py")
s = src.read_text(encoding="utf-8")
ast.parse(s)

MARKERS = {
    # -- client: the renderer ------------------------------------------------
    "client: nodes are keyed by run AND index": "const key=(i===undefined||i===null)?null:(runId+'#'+i);",
    "client: a re-sent line repaints in place": "if(d){render(d,kind,text,t);return d;}",
    "client: poll sends both cursors": "'&since='+since+'&rev='+revSeen",
    "client: in-place updates are repainted": "for(const l of j.updates||[])",
    "client: the rev cursor is advanced": "if(typeof j.rev==='number')revSeen=j.rev;",
    "client: a failed poll re-arms the timer": "timer=setTimeout(poll,fails?Math.min(5000,700*fails):700);",
    "client: a steer that lost its run is resent": "runId=null;busy(false);inp.value=t;inflight=false;return send();",
    "client: /clear exists": "if(t==='/clear')",
    "client: the transcript survives a reload": "localStorage[LOGKEY]=JSON.stringify(history)",
    # -- server: the run buffer ---------------------------------------------
    "server: WebRun.grow exists": "def grow(self, kind, text, from_turn=False):",
    "server: growth follows the streaming line": "i = self.stream_i",
    "server: a tool call ends the turn": 'if kind in ("tool", "tool_done", "tool_fail"):',
    "server: a revision counter exists": "self.rev += 1",
    "server: view returns in-place updates": '"updates": [l for l in self.lines',
    "server: narration grows one line": 'self.grow("say", text.strip())',
    "server: the final answer grows too": 'self.grow("final", text.strip())',
    "server: interim text is its own class": 'self.grow("thinking", text.strip(), from_turn=True)',
    "server: a busy run takes the message as a steer": "queued as steer",
}

bad = []
for label, needle in MARKERS.items():
    ok = needle in s
    print(("ok   " if ok else "FAIL ") + label)
    if not ok:
        bad.append(label)

if "add('you',t+'   (steering)')" in s:
    print("FAIL client: steer still echoes locally")
    bad.append("steer echo")

# /api/events must emit exactly one JSON document per request. A stray second
# _json() call shipped for months and made page.poll()'s r.json() throw on every
# single poll of every single run. /api/health emits that same document on
# purpose, so there must be exactly one occurrence in total.
n_doc = s.count('self._json({"ok": True, "version": VERSION})')
print(("ok   " if n_doc == 1 else "FAIL ")
      + f"{n_doc} one-document health replies (want exactly 1: health only)")
if n_doc != 1 or "run.view(since, rev))\n                self._json(" in s:
    bad.append("duplicate JSON document")

n_you = s.count("add('you'")
print(("ok   " if n_you == 1 else "FAIL ") + f"page has {n_you} 'you' echo site(s), want 1")
if n_you != 1:
    bad.append("you echo count")

print("all web-UI patch markers present" if not bad else f"MISSING: {bad}")
sys.exit(1 if bad else 0)

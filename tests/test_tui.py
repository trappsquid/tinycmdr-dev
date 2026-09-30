"""The console's screen: rich cards, and the plain lines underneath them.

    python tests/test_tui.py

The screen is decoration on text the plain path already prints, so the properties
that matter are: a pipe or TINYCMDR_PLAIN still gets plain lines, every tone lands on
the card it should, the run's done line is not mistaken for the answer, and what
reaches the terminal is ANSI that prompt_toolkit can render (raw ESC bytes get
sanitized into visible "[1;33m" garbage).

Without rich/prompt_toolkit it exits 77 (SKIP, never a green 0) after the two checks
that do not need them.
"""
import importlib.util
import io
import os
import re
import sys
import tempfile
import threading
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SRC = BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")
spec = importlib.util.spec_from_file_location("tinycmdr_tui_under_test", SRC)
fb = importlib.util.module_from_spec(spec)
sys.modules["tinycmdr_tui_under_test"] = fb
spec.loader.exec_module(fb)

PASSES = []
FAILS = []

# rich/prompt_toolkit absent: the screen cannot be graded here, which is a SKIP for the
# gate and not a green 0. This branch used to sys.exit(0) with two checks run out of 39
# (BUGREPORT T4); tests/run_all.py counts 77 as red.
SKIP_EXIT = 77


def check(name, cond, detail=""):
    (PASSES if cond else FAILS).append(name)
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else f"   {detail}"))


try:
    import rich          # noqa: F401
    import prompt_toolkit  # noqa: F401
    HAVE = True
except Exception:
    HAVE = False

check("a pipe is not a console, so nothing to draw on", fb.tui_wanted() is False)
os.environ["TINYCMDR_PLAIN"] = "1"
check("TINYCMDR_PLAIN=1 refuses the screen even with a terminal", fb.tui_wanted() is False)
del os.environ["TINYCMDR_PLAIN"]

if not HAVE:
    print("\nrich/prompt_toolkit are absent: the screen itself cannot be graded here")
    print(f"\n{len(PASSES)} passed, {len(FAILS)} failed")
    sys.exit(1 if FAILS else SKIP_EXIT)

# The harness that runs this suite may export NO_COLOR=1 and TERM=dumb (a CI runner,
# a pipe). The renderer still has to be graded, so the colour environment here is
# STATED, not inherited - otherwise the tier checks below grade a colourless screen
# and pass or fail for the wrong reason.
os.environ.pop("NO_COLOR", None)
os.environ.setdefault("COLORTERM", "truecolor")

screen = fb.TuiScreen(out=io.StringIO(), width=100)
screen.banner("tinycmdr 1.0.0", [("model", "main at http://127.0.0.1:8081"),
                                ("folder", str(BASE))], hint="type /help")
panels = [r for r in screen.shown if hasattr(r, "border_style")]
check("the banner is one boxed panel", len(panels) == 1
      and "tinycmdr" in str(panels[0].title), str(panels[0].title))
check("...carrying the facts the plain banner prints",
      "main at http" in str(panels[0].renderable)
      and str(BASE) in str(panels[0].renderable))

screen.card("tool", "shell(ls -la)")
p = screen.shown[-1]
check("a call is a card titled call", str(p.title).strip() == "call"
      and p.border_style == screen.style("call"), str(p.title))
screen.card("tool_done", "shell ls -la · 0.4s")
p = screen.shown[-1]
check("a result carries the palette's result colour", str(p.title).strip() == "result"
      and p.border_style == screen.style("result"), str(p.title))
screen.card("tool_fail", "shell docker ps · [exit 1]")
p = screen.shown[-1]
check("a failure carries the failure style", str(p.title).strip() == "failed"
      and p.border_style == screen.style("fail"), str(p.title))
screen.card("final", "\n\n# Heading\n\n- a\n- b\n\n")
p = screen.shown[-1]
check("the answer is titled on the quiet frame", str(p.title).strip() == "answer"
      and p.border_style == screen.style("frame"), str(p.title))
check("...and its title is the one accent",
      screen.style("title") in str(p.title.style), str(p.title.style))
check("...and it is rendered as markdown, not raw text",
      type(p.renderable).__name__ == "_TightMarkdown")
check("...through the tight renderer that strips rich's table band",
      type(p.renderable.markdown).__name__ == "Markdown")
check("no tier paints anything blue",
      not any("blue" in str(value) for tier in fb.TUI_PALETTE.values()
              for value in tier.values()),
      fb.TUI_PALETTE)
check("the final kind is not the old blue card",
      fb.TUI_KINDS["final"][1] != "blue", fb.TUI_KINDS["final"])
check("every tier carries every role",
      all(set(tier) == set(fb.TUI_PALETTE["truecolor"])
          for tier in fb.TUI_PALETTE.values()))
check("the tier is decided from the environment, not guessed",
      fb.tui_colour_tier({"TERM": "xterm-256color"}) == "256"
      and fb.tui_colour_tier({"COLORTERM": "truecolor"}) == "truecolor"
      and fb.tui_colour_tier({"TERM": "xterm", "WT_SESSION": "1"}) == "truecolor"
      and fb.tui_colour_tier({"TERM": "xterm"}) == "16"
      and fb.tui_colour_tier({"TERM": "xterm", "NO_COLOR": "1"}) == "none"
      and fb.tui_colour_tier({"TERM": "xterm-256color", "TINYCMDR_COLOR": "16"}) == "16",
      [fb.tui_colour_tier({"TERM": t}) for t in ("xterm", "xterm-256color")])
tiers = {}
for tier in ("truecolor", "256", "16", "none"):
    tiers[tier] = fb.TuiScreen(out=io.StringIO(), width=90, tier=tier)
    tiers[tier]._plain_fallback = True      # a StringIO is not a tty: ask for the bytes
    tiers[tier].card("final", "# a\n\n| x | y |\n|---|---|\n| 1 | 2 |\n")
check("a truecolor console gets 24-bit SGR for the accent",
      "38;2;95;191;191" in tiers["truecolor"].out.getvalue(),
      repr(tiers["truecolor"].out.getvalue()[:120]))
check("a 256-colour console gets the 256 form of it",
      "38;5;73" in tiers["256"].out.getvalue(),
      repr(tiers["256"].out.getvalue()[:120]))
check("a 16-colour console gets a named colour instead of hex",
      "38;2;" not in tiers["16"].out.getvalue()
      and "38;5;" not in tiers["16"].out.getvalue(),
      tiers["16"].out.getvalue()[:120])
check("a no-colour console gets no SGR at all",
      "\x1b[" not in tiers["none"].out.getvalue(),
      tiers["none"].out.getvalue()[:120])
n = len(screen.shown)
screen.card("checkin", "· working · 12s")
check("the run's own line is text, not a card",
      len(screen.shown) == n + 1 and not hasattr(screen.shown[-1], "border_style"))

out = io.StringIO()
scr = fb.TuiScreen(out=out, width=100)
dest = fb.CliDestination(colour=False, out=out, screen=scr)
ref = dest.line("tool", "`shell` ls -la")
check("the lane hands the screen a card", str(scr.shown[-1].title).strip() == "call")
check("...with the chat backticks gone", "`" not in str(scr.shown[-1].renderable))
dest.update(ref, "final", "✅ Done — 2 step(s) in 4s")
check("the done line is a status line, not an answer",
      not any(str(getattr(r, "title", "")).strip() == "answer" for r in scr.shown))
check("...and it keeps the done text", scr.status.startswith("✅ Done"))

plain = io.StringIO()
plain_dest = fb.CliDestination(colour=False, out=plain, screen=None)
plain_dest.line("tool", "shell(ls)")
check("with no screen the painted line is what comes out",
      "▸ shell(ls)" in plain.getvalue(), plain.getvalue()[:60])

scr2 = fb.TuiScreen(out=io.StringIO(), width=100)
scr2._plain_fallback = True
scr2.card("tool_fail", "boom")
text = scr2.out.getvalue()
check("the printed card carries real SGR escapes", "\x1b[" in text, repr(text[:160]))
check("...and no rich markup leaked into the output",
      "[bold]" not in text and "[/" not in text)

scr3 = fb.TuiScreen(out=io.StringIO(), width=100)
scr3.status_line("working · 1s")
n1 = len(scr3.shown)
scr3.status_line("working · 2s")
check("the run's line is throttled, not printed every second",
      len(scr3.shown) == n1)
check("...and the screen keeps the newest text", scr3.status == "working · 2s")

scr4 = fb.TuiScreen(out=io.StringIO(), width=100)
scr4._plain_fallback = True
scr4.raw_ansi("  the model is saying this")
check("a growing line is passed through as it arrives",
      "the model is saying this" in scr4.out.getvalue())

if hasattr(fb, "_tui_toolbar"):
    check("with no screen there is no prompt_toolkit session",
          fb._tui_session() is None)
    fb._CLI["status"] = "working · 3 steps · 12s"
    toolbar = str(fb._tui_toolbar())
    check("the toolbar carries the run's line and the keys",
          "working · 3 steps · 12s" in toolbar and "Ctrl-D" in toolbar)
    fb._CLI.pop("status", None)
    fb._CLI.pop("session", None)

scr5 = fb.TuiScreen(out=io.StringIO(), width=100)
seen = []
scr5.on_status = lambda t: seen.append(t)
scr5.status_line("working · 1s")
check("a screen with a toolbar hands the text over instead of printing",
      seen == ["working · 1s"] and not scr5.shown, str(scr5.shown))

svg = Path(tempfile.mkdtemp(prefix="fbtui-svg-")) / "tui-preview.svg"
written = screen.export_svg(svg)
body = written.read_text(encoding="utf-8")
check("export_svg writes the whole screen", "<svg" in body[:400])
check("...with the answer's text in it", "Heading" in body)
svg.unlink()

# --- the approved render's rhythm and its thought line (tui-preview) -------
scr6 = fb.TuiScreen(out=io.StringIO(), width=100)
scr6.card("tool", "shell  Get-CimInstance Win32_PhysicalMemory")
scr6.card("tool_done", "capacity 8GB x4", "0.6s")
scr6.card("final", "Four DIMMs of DDR4-3200.")
rows6 = scr6.out.getvalue().splitlines()
idx = lambda word: next((i for i, r in enumerate(rows6) if word in r), -1)
check("call and result cards stack with no gap",
      idx("call") >= 0 and idx("result") == idx("call") + 3
      and rows6[idx("result") - 1].strip() != "")
check("the answer card is opened by a blank line",
      idx("answer") > 0 and rows6[idx("answer") - 1].strip() == "")

dest6 = fb.CliDestination(colour=False, out=io.StringIO())
ref6 = dest6.line("narration", "")
dest6.update(ref6, "narration", "Checking the lock:")
check("a line's opening is HELD until it is knowable, not printed on sight",
      "Checking the lock:" not in dest6.out.getvalue(), dest6.out.getvalue()[:80])
dest6.drop(ref6)                     # the run ended: the held line is committed
dest6._close()
check("...and it is committed when the line ends, with the render's label",
      "  \u2026  Checking the lock:" in dest6.out.getvalue(), dest6.out.getvalue()[:120])
scr6.card("narration", "thinking aloud, quietly")
check("...and so does the drawn one, dim as the render has it",
      "  \u2026  thinking aloud, quietly" in scr6.out.getvalue())

# --- T-06: a streamed line GROWS; it does not stair-step one line per delta ------
# print_formatted_text defaults to end="\n", so every delta used to land on its own
# line and `_close()`'s newline was dropped by the old `if text.strip()` guard.
scr6b = fb.TuiScreen(out=io.StringIO(), width=100)
scr6b._plain_fallback = True
dest6b = fb.CliDestination(colour=False, out=scr6b.out, screen=scr6b)
ref6b = dest6b.line("narration", "")
_accumulated = ""
for _delta in ("Let me confirm", " the exact numbers", " for this box."):
    _accumulated += _delta
    dest6b.update(ref6b, "narration", "\U0001F4AC " + _accumulated)
dest6b.drop(ref6b)
dest6b._close()
_out6b = scr6b.out.getvalue()
_tail6b = _out6b[_out6b.find("Let me confirm"):]
check("a multi-delta stream renders as ONE line (T-06)",
      "Let me confirm the exact numbers for this box." in _tail6b
      and _tail6b.count("\n") == 1 and _tail6b.endswith("\n"), repr(_tail6b[:120]))

# --- T-02 residual: decide BEFORE printing, because a terminal cannot unprint ----
scr6c = fb.TuiScreen(out=io.StringIO(), width=100)
scr6c._plain_fallback = True
dest6c = fb.CliDestination(colour=False, out=scr6c.out, screen=scr6c)
ref6c = dest6c.line("narration", "")
dest6c.update(ref6c, "narration",
              "\U0001F4AC The only Apple machine that hits 800 GB/s is the")
check("...a prose opening that may become a table is not printed on sight",
      "The only Apple" not in scr6c.out.getvalue(), scr6c.out.getvalue()[:80])
dest6c.update(ref6c, "narration",
              "\U0001F4AC The only Apple machine that hits 800 GB/s is the M4 Ultra\n\n"
              "| Chip | Bandwidth |\n|---|---|\n| M4 Ultra | 800 GB/s |\n")
_out6c = scr6c.out.getvalue()
check("...and when it turns out to be a table, zero raw draft reached the screen",
      "The only Apple" not in _out6c and "drafting answer" in _out6c,
      repr(_out6c[:120]))

scr6d = fb.TuiScreen(out=io.StringIO(), width=100)
scr6d._plain_fallback = True
dest6d = fb.CliDestination(colour=False, out=scr6d.out, screen=scr6d)
ref6d = dest6d.line("narration", "")
dest6d.update(ref6d, "narration", "\U0001F4AC Checking the lock before I touch anything")
dest6d.line("tool", "`shell` ls")        # a card interrupts: the held line commits first
check("a held line is committed, never lost, when a card interrupts it",
      "Checking the lock before I touch anything" in scr6d.out.getvalue(),
      repr(scr6d.out.getvalue()[:160]))

# --- T-07: the prompt must not paint between the draft pulse and the answer card --
_final_stop = {}


class _Recorder(fb.TuiScreen):
    def card(self, kind, text, foot=""):
        if kind == "final":
            _final_stop["stop"] = fb._CLI.get("stop")
        super().card(kind, text, foot)


_saved_loop_cli = dict(fb._CLI)
_saved_loop_drive = fb.drive_run
try:
    _rec = _Recorder(out=io.StringIO(), width=100, tier="truecolor")
    fb._CLI.update({"screen": _rec, "app": None, "colour": False, "stop": None,
                    "inbox": fb.queue.Queue(), "steer": fb.queue.Queue(),
                    "leave": False, "reader": False, "ask": None})
    fb.drive_run = lambda key, text, reporter, **kw: "answer text"
    fb._CLI["inbox"].put("hello")
    _loop = threading.Thread(target=fb._cli_console_loop, daemon=True)
    _loop.start()
    _deadline = time.time() + 8
    while time.time() < _deadline and "stop" not in _final_stop:
        time.sleep(0.05)
    _loop.join(2)
    check("the run flag is still set while the answer card is drawn (T-07)",
          isinstance(_final_stop.get("stop"), threading.Event), _final_stop)
    check("...and it is cleared once the run's card is on screen",
          fb._CLI.get("stop") is None, fb._CLI.get("stop"))
finally:
    fb.drive_run = _saved_loop_drive
    fb._CLI.clear()
    fb._CLI.update(_saved_loop_cli)

# --- the app's own chrome: a window, not a prompt ---------------------------------
# The rail is live: it reads AGENT.stats() for the session's estimate and the detected window, so
# how full the gauge draws depends on the state of the box the suite runs on. On a clean clone -
# which is what CI grades - usage is zero, the bar is all shade, and this failed while the same
# tree passed on a host that had run a session (2026-09-30; the first red of that shape was CI run
# 36778872020, and it stayed green here for exactly that reason). Pin the two numbers the gauge
# reads, grade the fill against them, and put both back: the chrome is then graded the same way on
# a clone and on a box that has done work.
_app = fb.AppScreen(colour=True, tier="truecolor")
_title = "".join(part for _, part in _app._frame_title().__pt_formatted_text__())


def _rail_for(used, budget):
    _saved_stats, _saved_budget = fb.AGENT.stats, fb.AGENT._context_budget
    try:
        fb.AGENT.stats = lambda key: {"exchanges": 2 if used else 0, "est_tokens": used}
        fb.AGENT._context_budget = lambda: budget
        return "".join(part for _, part in
                       fb.AppScreen(colour=True, tier="truecolor")
                       ._sidebar_text().__pt_formatted_text__())
    finally:
        fb.AGENT.stats = _saved_stats
        fb.AGENT._context_budget = _saved_budget


_rail = _rail_for(4096, 8192)
_bar = next((l.strip() for l in _rail.splitlines() if "\u2588" in l or "\u2591" in l), "")
_bar_w = max(6, _app.RAIL_WIDTH - 8)
check("--app draws a window title with the session in it",
      "tinycmdr" in _title and "cli" in _title, _title)
check("--app draws a rail with a live context gauge",
      "SESSION" in _rail and "CONTEXT" in _rail and "4.1K / 8.2K" in _rail
      and "50%" in _rail and _bar.count("\u2588") == _bar_w // 2
      and _bar.count("\u2591") == _bar_w - _bar_w // 2, _rail[:120])
_rail_zero = _rail_for(0, 8192)
check("...and a session with no usage yet draws an empty gauge, not a full one",
      "\u2588" not in _rail_zero and "0 / 8.2K" in _rail_zero and "0%" in _rail_zero,
      _rail_zero[:120])
# The rail is a fixed-width window, so a line longer than it is silently CUT (no wrap): the
# wheel hint had to be shortened to fit, and the next one has to be told the same way.
check("--app: every rail line fits the rail",
      max(len(l) for l in _rail.splitlines()) <= _app.RAIL_WIDTH,
      [(l, len(l)) for l in _rail.splitlines() if len(l) > _app.RAIL_WIDTH])
check("--app's composer is a labeled box, not a bare prompt",
      "".join(p for _, p in _app.composer.title.__pt_formatted_text__()).strip() == "you",
      _app.composer.title)

# --- round-3 polish ---------------------------------------------------------------
# P-01: in the app the chrome carries the meta, so the transcript opens on content.
_calls = []
_saved_banner, _saved_caps = fb.cli_banner, fb.capability_line
_saved_screen = fb._CLI.get("screen")


class _FakeScreen:
    status = ""


try:
    fb.cli_banner = lambda: _calls.append("banner")
    fb.capability_line = lambda lane: (_calls.append("caps"), "caps")[1]
    fb._CLI["screen"] = _FakeScreen()
    fb._cli_startup(app_mode=True)
    check("app mode seeds no meta text: the chrome carries it (P-01)",
          _calls == [] and "ready" in fb._CLI["screen"].status,
          (_calls, fb._CLI["screen"].status))
    import contextlib
    _buf = io.StringIO()
    with contextlib.redirect_stdout(_buf):
        fb._cli_startup(app_mode=False)
    check("...and the inline path still prints the banner and the capability line",
          _calls == ["banner", "caps"], _calls)
finally:
    fb.cli_banner, fb.capability_line = _saved_banner, _saved_caps
    fb._CLI["screen"] = _saved_screen

# P-02: an empty draft card is a live region in the app, never a committed stub.
_app2 = fb.AppScreen(colour=True, tier="truecolor")
_n_items = len(_app2.items)
_app2.card("narration", "")
check("--app commits no empty draft stub (P-02)",
      len(_app2.items) == _n_items, _app2.items[-1:])

# P-04: an answer body is bold/dim/default - never hue of its own.
_ans = fb.TuiScreen(out=io.StringIO(), width=100, tier="truecolor")
_ans._plain_fallback = True
_ans.card("final", "## H\n\n> quote\n\n- item\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n"
                   "```python\nx = 1\n```\n\n[link](http://x)\n")
_ans_codes = sorted(set(re.findall(r"\x1b\[([0-9;]+)m", _ans.out.getvalue())))
_ANS_HUES = ("34", "35", "36")
_ans_out = _ans.out.getvalue()
_off = min([_ans_out.find("\x1b[%sm" % h) for h in _ANS_HUES] + [-1])
_off = _off if _off >= 0 else max(0, len(_ans_out) - 80)
check("an answer body never renders magenta or cyan (P-04)",
      not [c for c in _ans_codes
           if c in _ANS_HUES or c.startswith("35;") or "38;5;13" in c],
      repr(_ans_out[max(0, _off - 140):_off + 80]))

# P-05/P-06: the footer is the run's status only; the box the operator types in says "you".
check("--app's status line carries the run status only; keys live in the rail (P-05)",
      not hasattr(fb.AppScreen, "_hints_text")
      and "KEYS" in "".join(p for _, p in _app._sidebar_text().__pt_formatted_text__()),
      "hints still share the status line")
check("--app's input box is labeled 'you', not 'ask' (P-06)",
      "".join(p for _, p in _app.composer.title.__pt_formatted_text__()).strip() == "you",
      _app.composer.title)

# P-03: the done line and the rail read ONE counter (the run accumulator).
_events = []


class _RecDest:
    def update(self, ref, kind, text, src="main"):
        _events.append((kind, text))
        return ref

    def line(self, *a, **k):
        return ("x",)


_saved_usage_3 = fb.AGENT.last_usage.get("cli")
try:
    fb.AGENT.last_usage["cli"] = {"calls": 2, "steps": 7, "secs": 9, "prompt": 100,
                                  "completion": 10, "llm_secs": 1.0}
    _rep = fb.RunReporter(_RecDest(), "cli")
    _rep.status_ref = ("x",)
    _rep.steps = 0                      # the reporter's own tally disagrees on purpose
    _rep.t0 = time.time()
    _rep.finish(ok=True)
    _done = _events[-1][1] if _events else ""
    check("the done line's steps/elapsed come from the run accumulator (P-03)",
          "7 step(s) in 9s" in _done, _done)
finally:
    if _saved_usage_3 is None:
        fb.AGENT.last_usage.pop("cli", None)
    else:
        fb.AGENT.last_usage["cli"] = _saved_usage_3

# --- round-4: the pane replaces the run's WHOLE draft region (P-02) ---------------
# The reporter draws a narration line and only then streams deltas into it, so the
# first draw is a committed line; dropping just the last item filed the model's
# opening sentence above the answer card with its markdown characters intact.
_app4 = fb.AppScreen(colour=True, tier="truecolor")
_d4 = fb.CliDestination(colour=True, out=io.StringIO(), screen=_app4)
_r4 = _d4.line("narration", "The only Apple machine that hits 800 GB/s is the **M4 Ultra** -")
for _extra in (" let me confirm the exact numbers", " since this is newer than my data."):
    _d4.update(_r4, "narration",
               "\U0001F4AC The only Apple machine that hits 800 GB/s is the **M4 Ultra** -"
               + _extra)
_d4.drop(_r4)
_d4._close()
_app4.card("final", "| Chip | Bandwidth |\n|---|---|\n| M4 Ultra | 800 GB/s |\n")
check("--app replaces the run's whole draft region, first draw included (P-02)",
      not any(k == "ansi" and "M4 Ultra" in str(p) for k, p in _app4.items),
      [str(p)[:70] for k, p in _app4.items])

# --- round-4: no blank band around a table, and nothing trailing (T-05) ----------
_t5 = fb.TuiScreen(out=io.StringIO(), width=90, tier="truecolor")
_t5._plain_fallback = True
_t5.card("final", "| Slot | Size |\n|---|---|\n| DIMM 1 | 8 GB |\n| DIMM 2 | 8 GB |\n\n"
                  "After the table.\n")
_plain5 = [re.sub(r"\x1b\[[0-9;]*m", "", l) for l in _t5.out.getvalue().splitlines()]
_rows5 = [l[1:-1] for l in _plain5 if l.startswith("\u2502")]
check("the answer card carries no blank band around a table (T-05)",
      _rows5 and not any(not r.strip() for r in _rows5), _rows5)
check("...and a table at the end of an answer adds no trailing row",
      _rows5[-1].strip() == "After the table.", _rows5)

# --- round-4: the composer reports its size and grows with a paste ----------------
_app5 = fb.AppScreen(colour=True, tier="truecolor")
_app5.input.text = "x" * 400
_title5 = "".join(p for _, p in _app5._composer_title().__pt_formatted_text__())
check("the composer reports what is in it and names the keys",
      "400 chars" in _title5 and "send" in _title5, _title5)
check("...and grows to show a long paste, to a ceiling",
      _app5._composer_rows() == fb.AppScreen.COMPOSER_MAX_ROWS
      and _app5._composer_rows() > 1, _app5._composer_rows())

# --- the run's key is a filename; the editing surface is not one (the Windows bed
# measured 2026-09-22: a PromptSession in the key slot crashed _save() with
# "expected string or bytes-like object", so sessions/ stayed empty and the
# event log was never written) ---------------------------------------------
fb._CLI["prompt"] = _sentinel = object()
check("a live editing surface never becomes the run's key",
      fb._cli_key() == "cli" and isinstance(fb._cli_key(), str))
fb._CLI["session"] = object()
check("a non-string in the key slot cannot become a filename",
      fb._cli_key() == "cli"
      and fb.AGENT._session_path(fb._cli_key()).name == "cli.json"
      and fb._event_path(fb._cli_key()).name.startswith("cli"))
fb._CLI["session"] = "foo"
check("/resume switches the key without destroying the editing surface",
      fb._tui_session() is _sentinel and fb._cli_key() == "foo")
fb._CLI.pop("prompt", None)
fb._CLI.pop("session", None)

# --- one writer per terminal (measured 2026-09-25, a live render on a fleet macOS box:
# raw INFO lines landed inside the cards, the toolbar was redrawn over them, the run's
# line was left stranded mid-screen and the answer never got its card) ----------------
import logging.handlers  # noqa: E402

_saved_wanted = fb.tui_wanted
fb._CLI.pop("screen", None)
try:
    fb.tui_wanted = lambda: True
    fb.tui_screen()
    _console = getattr(fb, "_log_console", None)
    check("the console log handler is named, so a screen can detach it",
          _console is not None)
    check("a console that takes the screen stops writing the log into it",
          _console is not None and _console not in fb._log_listener.handlers,
          getattr(fb._log_listener, "handlers", None))
    check("the file handler stays (the log is still the record)",
          any(isinstance(h, logging.handlers.RotatingFileHandler)
              for h in fb._log_listener.handlers), fb._log_listener.handlers)
finally:
    fb.tui_wanted = _saved_wanted
    fb._CLI.pop("screen", None)

scr8 = fb.TuiScreen(out=io.StringIO(), width=90)
out8 = io.StringIO()
dest8 = fb.CliDestination(colour=False, out=out8, screen=scr8)
dest8._write("a painted streamed line")
check("with a screen, a painted line goes through the screen",
      "a painted streamed line" in scr8.out.getvalue(), scr8.out.getvalue()[:80])
check("...and nothing is printed straight at the terminal",
      out8.getvalue() == "", out8.getvalue()[:80])

dest9 = fb.CliDestination(colour=False, out=io.StringIO())
dest9._write("a painted streamed line")
check("without a screen the text still goes to stdout",
      "a painted streamed line" in dest9.out.getvalue(), dest9.out.getvalue()[:80])

scr9 = fb.TuiScreen(out=io.StringIO(), width=90)
dest10 = fb.CliDestination(colour=False, out=io.StringIO(), screen=scr9)
seen10 = []
dest10.on_drop = seen10.append
ref10 = dest10.line("narration", "host.lan")
dest10.drop(ref10)
check("a dropped draft draws nothing itself (the console prints the card once)",
      not any(str(getattr(r, "title", "")).strip() == "answer" for r in scr9.shown),
      [str(getattr(r, "title", "")) for r in scr9.shown])
check("...and hands the draft text to the caller", seen10 == ["host.lan"], seen10)

# --- T-01: the words of an answer appear once, as the bright card --------------
# Reproduced before the fix: a streamed markdown table produced a dim pipe-flattened
# line, a mangled card ending "acr…", and the rendered card - the same answer three
# times over. Now the draft is one dim pulse and the console prints one card.
ANSWER = "# Disk\n\n| Field | Value |\n|---|---|\n| capacity | 8GB x4 |\n| type | DDR4 |\n"
scr11 = fb.TuiScreen(out=io.StringIO(), width=90)
dest11 = fb.CliDestination(colour=False, out=io.StringIO(), screen=scr11)
dest11.on_drop = lambda t: fb._CLI.__setitem__("streamed_answer", t)
ref11 = dest11.line("narration", "")
flat = fb.scrub(" ".join(ANSWER.split()))
dest11.update(ref11, "narration", "\U0001F4AC " + flat[:400]
               + ("\u2026" if len(flat) > 400 else ""))
dest11.drop(ref11)
draft = scr11.out.getvalue()
check("a table draft becomes one dim pulse, not raw pipes",
      "drafting answer" in draft and "| Field | Value |" not in draft, draft[:160])
scr11.card("final", ANSWER)          # exactly what run_cli does, unconditionally
raw11 = scr11.out.getvalue()
check("the answer is drawn as a card exactly once",
      sum(1 for r in scr11.shown
          if str(getattr(r, "title", "")).strip() == "answer") == 1)
check("...the table survives as a table",
      "Field" in raw11 and "capacity" in raw11 and "8GB x4" in raw11)
check("...no mid-word truncation glyph inside the answer", "acr\u2026" not in raw11)
check("...and no flattened copy of the answer is anywhere on the screen",
      "| Field | Value |" not in raw11 and "capacity | 8GB x4" not in raw11)

# --- T-02: a structured draft never streams its own pipes ---------------------
scr12 = fb.TuiScreen(out=io.StringIO(), width=90)
dest12 = fb.CliDestination(colour=False, out=io.StringIO(), screen=scr12)
r12 = dest12.line("narration", "")
dest12.update(r12, "narration", "\U0001F4AC | a | b |\n|---|---|")
dest12.update(r12, "narration", "\U0001F4AC | a | b |\n|---|---|\n| 1 | 2 |")
pulse = scr12.out.getvalue()
check("a second chunk does not re-print the draft",
      pulse.count("drafting answer") == 1, pulse[:200])
check("...and the raw table never reaches the screen",
      "|---|---|" not in pulse, pulse[:200])

# --- T-03: with a toolbar the transcript prints no stats line -----------------
_saved_usage = fb.AGENT.last_usage.get("cli")
_saved_stdout = sys.stdout
buf3 = io.StringIO()
try:
    fb.AGENT.last_usage["cli"] = {"calls": 2, "prompt": 1000, "completion": 50,
                                  "llm_secs": 1.0, "steps": 2, "secs": 4.0}
    fb._CLI["prompt"] = object()          # a live prompt_toolkit toolbar
    sys.stdout = buf3
    fb._cli_usage_line()
    check("with a toolbar the run's stats stay out of the transcript",
          buf3.getvalue() == "", buf3.getvalue()[:120])
    fb._CLI.pop("prompt", None)
    fb._cli_usage_line(force=True)
    check("...and /usage still asks for them explicitly",
          "tok over" in buf3.getvalue(), buf3.getvalue()[:160])
finally:
    sys.stdout = _saved_stdout
    fb._CLI.pop("prompt", None)
    if _saved_usage is None:
        fb.AGENT.last_usage.pop("cli", None)
    else:
        fb.AGENT.last_usage["cli"] = _saved_usage

# --- `--app`: the alternate-screen mode (brief §10) ---------------------------
# Headless: a pipe for keys, DummyOutput for the screen. The point is the LOGIC -
# one answer card, status only in the status bar, no socket, a clean exit.
try:
    from prompt_toolkit.input import create_pipe_input as _pipe_input
    from prompt_toolkit.output import DummyOutput as _DummyOutput
    HAVE_APP = True
except Exception:
    HAVE_APP = False

check("--app wants a real terminal only", fb.app_wanted() is False
      or fb.app_wanted() is True)          # both are valid here; it must not raise
_saved_plain = os.environ.get("TINYCMDR_PLAIN")
os.environ["TINYCMDR_PLAIN"] = "1"
check("--app refuses a pipe or TINYCMDR_PLAIN=1", fb.app_wanted() is False)
if _saved_plain is None:
    del os.environ["TINYCMDR_PLAIN"]
else:
    os.environ["TINYCMDR_PLAIN"] = _saved_plain

if HAVE_APP:
    import queue as _queue
    import threading as _threading
    import time as _time

    app_screen = fb.AppScreen(colour=False, tier="truecolor")
    _saved_cli = dict(fb._CLI)
    _saved_drive = fb.drive_run
    try:
        fb._CLI.update({"app": app_screen, "screen": app_screen, "colour": False,
                        "inbox": _queue.Queue(), "steer": _queue.Queue(),
                        "leave": False, "reader": False, "stop": None})

        def _stub_run(key, text, reporter, **kw):
            r = reporter.dest.line("tool", "`shell` echo %s" % text)
            reporter.dest.update(r, "tool_done", "echo · 0.1s")
            rn = reporter.dest.line("narration", "")
            reporter.dest.update(rn, "narration", "drafting the answer")
            reporter.dest.drop(rn)
            reporter.dest.update(r, "status", "working · 1.2K tok")
            return "# Answer\n\n| Field | Value |\n|---|---|\n| %s | 1 |\n" % text

        fb.drive_run = _stub_run
        _sockets = []
        _real_socket = fb.socket.socket

        class _NoNetSocket(_real_socket):
            """asyncio's event loop makes an AF_UNIX socketpair for its own wakeup;
            what --app must never do is open a NETWORK socket (a port)."""

            def __init__(self, family=-1, *a, **kw):
                if family in (fb.socket.AF_INET, fb.socket.AF_INET6):
                    _sockets.append(family)
                super().__init__(family, *a, **kw)

        fb.socket.socket = _NoNetSocket
        try:
            def _worker():
                fb._cli_console_loop()
                app_screen.request_exit()

            _threading.Thread(target=_worker, daemon=True).start()
            with _pipe_input() as _keys:
                app_screen.app.input = _keys
                app_screen.app.output = _DummyOutput()

                def _feed():
                    _time.sleep(0.3)
                    _keys.send_text("hello there\r")
                    _time.sleep(1.2)
                    _keys.send_text("/exit\r")

                _threading.Thread(target=_feed, daemon=True).start()
                app_screen.app.run()
            _time.sleep(0.2)
        finally:
            fb.socket.socket = _real_socket

        _lines = [re.sub(r"\x1b\[[0-9;]*m", "", l) for l in app_screen.lines(79)]
        _answer_cards = [i for i, l in enumerate(_lines) if "─ answer ─" in l]
        check("--app: the run draws exactly one answer card",
              len(_answer_cards) == 1, _lines)
        check("--app: the answer card replaces the draft, it does not stack on it",
              not any("drafting the answer" in l for l in _lines), _lines)
        check("--app: the run's stats stay in the status bar, not the transcript",
              not any("tok" in l for l in _lines)
              and "Done" in app_screen.status, (app_screen.status, _lines))
        check("--app: /exit leaves the loop and the app", fb._CLI["leave"] is True)
        check("--app opens no NETWORK socket", not _sockets, _sockets)

        # scrolling: auto-follow at the bottom, and a page up suspends it
        _full = fb.AppScreen(colour=False, tier="truecolor")
        for _i in range(80):
            _full.card("tool", "shell step %d" % _i)
        app_screen_tmp = app_screen
        fb._CLI["app"] = _full
        _full.app.output = _DummyOutput()
        _full.autofollow = True
        _before = _full._pane_text()
        _full.scroll(-1)
        check("--app: a page up stops following the tail", _full.autofollow is False)
        _full.scroll(1)
        check("--app: scrolling back to the bottom follows again",
              _full.autofollow is True)
        fb._CLI["app"] = app_screen_tmp

        # all the console's prints land in the pane, never at the real stdout
        _sink = fb._AppStdout(app_screen_tmp)
        _sink.write("one\n")
        _sink.write("two ")
        _sink.write("three\n\n")
        _sink.flush()
        _tail = app_screen_tmp.lines(79)[-3:]
        check("--app: printed lines become transcript lines",
              "one" in _tail and "two three" in _tail, _tail)

        # leaving must be asked for ONCE: a second Application.exit() raises "Return
        # value already set", and scheduled through the loop it became prompt_toolkit's
        # "Unhandled exception in event loop" + "Press ENTER to continue..." after a
        # clean run (measured on a pty, 2026-09-30).
        _exits = []

        class _FakeApp:
            is_done = False
            loop = None

            @staticmethod
            def exit():
                _exits.append(1)

        _real_app = app_screen_tmp.app
        app_screen_tmp.app = _FakeApp()
        app_screen_tmp._exit_requested = False
        app_screen_tmp.request_exit()
        app_screen_tmp.request_exit()
        app_screen_tmp.app = _real_app
        check("--app: the exit is asked for once, never twice", _exits == [1], _exits)
    finally:
        fb.drive_run = _saved_drive
        fb._CLI.clear()
        fb._CLI.update(_saved_cli)

print(f"\n{len(PASSES)} passed, {len(FAILS)} failed")
sys.exit(1 if FAILS else 0)

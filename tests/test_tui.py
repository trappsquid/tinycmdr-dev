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
import contextlib
import io
import base64
import os
import re
import shutil
import subprocess
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

# The DRAWING POLICY is pinned here, the way colour and TERM are pinned below: this shell
# is often TERM=dumb (where the ASCII set is the CORRECT output), but these checks grade
# the Unicode set and the brand art. The ASCII policy gets its own check further down.
fb.CONFIG["agent"]["unicode"] = "always"

PASSES = []
FAILS = []

# rich/prompt_toolkit absent: the screen cannot be graded here, which is a SKIP for the
# gate and not a green 0. This branch used to sys.exit(0) with two checks run out of 39.
# tests/run_all.py counts 77 as red.
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

# --- the console this suite is graded on is an IN-MEMORY one -----------------------
# prompt_toolkit resolves an Application's default input/output from the AMBIENT app
# session at CONSTRUCTION time: Application.__init__ does `output or session.output`,
# and AppSession.output calls create_output(). On a Windows box with no console screen
# buffer - a CI runner, a redirected job - that raises NoConsoleScreenBufferError
# ("No Windows console found. Are you running cmd.exe?") and killed this suite before
# its own summary. Every Application built below is driven through an in-memory screen
# and a key pipe ANYWAY (a DummyOutput, with app.output/app.input assigned by hand), so
# the suite states it UP FRONT: the ambient session is the fake console, the product's
# own construction path is graded identically on every platform, and the app checks are
# never skipped for being on Windows.
from prompt_toolkit.application.current import create_app_session as _app_session
from prompt_toolkit.application.current import get_app_session as _get_app_session
from prompt_toolkit.input import DummyInput as _DummyInput
from prompt_toolkit.output import DummyOutput as _DummyOutput

# ...kept in a name for the life of the process: this is a generator-backed context
# manager, and a temporary falling out of scope would throw GeneratorExit into it and
# reset the contextvar on the spot.
_SUITE_CONSOLE = _app_session(input=_DummyInput(), output=_DummyOutput())
_SUITE_CONSOLE.__enter__()          # this script is a one-shot process: no exit needed
check("the suite grades --app on an in-memory console, never the host's",
      isinstance(_get_app_session().output, _DummyOutput)
      and isinstance(_get_app_session().input, _DummyInput))

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
      and p.border_style == screen.style("error"), str(p.title))
screen.card("final", "\n\n# Heading\n\n- a\n- b\n\n")
p = screen.shown[-1]
check("the answer is titled on the quiet frame", str(p.title).strip() == "answer"
      and p.border_style == screen.style("border"), str(p.title))
check("...and its title is the one accent",
      screen.style("heading") in str(p.title.style), str(p.title.style))
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
check("no tier uses a rich-only colour spelling",
      not any("color(" in str(value) for tier in fb.TUI_PALETTE.values()
              for value in tier.values()), fb.TUI_PALETTE)
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
# The answer card's BORDER is bronze and its title gold: crimson is the designer's one
# large decorative accent (the banner), never a card border (2026-10-03).
check("a truecolor console gets 24-bit SGR for a card border (bronze) and title (gold)",
      "38;2;117;101;77" in tiers["truecolor"].out.getvalue()
      and "38;2;215;169;74" in tiers["truecolor"].out.getvalue(),
      repr(tiers["truecolor"].out.getvalue()[:200]))
check("a 256-colour console gets the 256 form of the same roles",
      "38;5;101" in tiers["256"].out.getvalue()
      and "38;5;179" in tiers["256"].out.getvalue(),
      repr(tiers["256"].out.getvalue()[:200]))
check("a 16-colour console gets a named colour instead of hex",
      "38;2;" not in tiers["16"].out.getvalue()
      and "38;5;" not in tiers["16"].out.getvalue(),
      tiers["16"].out.getvalue()[:120])
check("a no-colour console gets no SGR at all",
      "\x1b[" not in tiers["none"].out.getvalue(),
      tiers["none"].out.getvalue()[:120])
# The palette's vocabulary and prompt_toolkit's parser are a must-agree pair: a `bg:`
# slot takes a COLOUR, and the `16`/`none` rows spell the selection as the ATTRIBUTE
# `reverse`. That one slot raised ValueError out of Style.from_dict for the WHOLE table,
# so `--app` died at startup on every plain conhost / TERM=xterm session (tier `16` is
# tui_colour_tier()'s fallback) and the shell's model picker died on a NO_COLOR terminal.
# Reproduced on a real pty with TERM=xterm, COLORTERM unset.
def _style_builds(table):
    from prompt_toolkit.styles import Style
    try:
        Style.from_dict(table)
        return True, ""
    except Exception as exc:                                    # noqa: BLE001
        return False, "%s: %s" % (type(exc).__name__, exc)


for tier in ("truecolor", "256", "16", "none"):
    ok, why = _style_builds(fb.model_pick_styles(fb.TUI_PALETTE[tier]))
    check("the model picker's style table builds on the %s tier" % tier, ok, why)
    try:
        built = fb.AppScreen(colour=True, tier=tier).app is not None
        ok, why = built, ""
    except Exception as exc:                                    # noqa: BLE001
        ok, why = False, "%s: %s" % (type(exc).__name__, exc)
    check("--app builds its frame on the %s tier" % tier, ok, why)
check("the 16-colour selection rides as an attribute, not a bg: colour",
      fb.pick_sel_style(fb.TUI_PALETTE["16"]) == "reverse"
      and fb.pick_sel_style(fb.TUI_PALETTE["truecolor"]).startswith("bg:#"),
      (fb.pick_sel_style(fb.TUI_PALETTE["16"]),
       fb.pick_sel_style(fb.TUI_PALETTE["truecolor"])))
check("a theme that spells its selection colour in words cannot poison the table",
      _style_builds(fb.model_pick_styles({"selection_bg": "dim red",
                                          "selection_fg": "white"}))[0])
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

# --- the exported render's rhythm and its thought line -------
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

# --- a streamed line GROWS; it does not stair-step one line per delta ------
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
check("a multi-delta stream renders as ONE line",
      "Let me confirm the exact numbers for this box." in _tail6b
      and _tail6b.count("\n") == 1 and _tail6b.endswith("\n"), repr(_tail6b[:120]))

# --- decide BEFORE printing, because a terminal cannot unprint ----
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

# --- the prompt must not paint between the draft pulse and the answer card --
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
    check("the run flag is still set while the answer card is drawn",
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


def _rail_for(used, budget, window=None, static=0, source=""):
    _saved_stats, _saved_budget = fb.AGENT.stats, fb.AGENT._context_budget
    _saved_env = fb.AGENT.cached_envelope
    try:
        fb.AGENT.stats = lambda key: {"exchanges": 2 if used else 0, "est_tokens": used}
        fb.AGENT._context_budget = lambda: budget
        # The CONTEXT block divides by the WINDOW, not the messages budget (2026-10-03);
        # default keeps the old calls meaning what they did.
        fb.AGENT.cached_envelope = lambda: {"window": window or budget, "static": static,
                                            "source": source}
        return "".join(part for _, part in
                       fb.AppScreen(colour=True, tier="truecolor")
                       ._sidebar_text().__pt_formatted_text__())
    finally:
        fb.AGENT.stats = _saved_stats
        fb.AGENT._context_budget = _saved_budget
        fb.AGENT.cached_envelope = _saved_env


_rail = _rail_for(4096, 8192)
_bar = next((l.strip() for l in _rail.splitlines() if "\u2588" in l or "\u2591" in l), "")
_bar_w = max(6, _app.RAIL_WIDTH - 8)
_rail_window = _rail_for(4096, 8192, window=16384, static=4096)
check("--app's context gauge divides by the model's WINDOW, not the messages budget",
      "8.2K / 16.4K" in _rail_window and "50%" in _rail_window, _rail_window[:200])
_saved_pol = fb.CONFIG["agent"].get("unicode")
try:
    fb.CONFIG["agent"]["unicode"] = "always"     # this shell's TERM is often `dumb`
    _rail_caption = _rail_for(4096, 8192)
finally:
    fb.CONFIG["agent"]["unicode"] = _saved_pol
_rail_lines = _rail_caption.splitlines()
_rail_braille = [i for i, ln in enumerate(_rail_lines)
                 if any(0x2800 <= ord(c) <= 0x28FF for c in ln)]
check("--app captions the rail art with the product name",
      bool(_rail_braille) and "tinycmdr" in _rail_lines[max(_rail_braille) + 1],
      _rail_lines[max(_rail_braille):max(_rail_braille) + 2] if _rail_braille
      else _rail_lines[-3:])
check("--app names an assumed or pinned window in the rail",
      "(assumed)" in _rail_for(4096, 8192, window=16384, source="assumed")
      and "(pinned)" in _rail_for(4096, 8192, window=16384, source="config-window")
      and "(assumed)" not in _rail_for(4096, 8192, window=16384, source="server"),
      _rail_for(4096, 8192, window=16384, source="assumed")[:200])
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
# In the app the chrome carries the meta, so the transcript opens on content.
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
    check("app mode seeds no meta text: the chrome carries it",
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

# An empty draft card is a live region in the app, never a committed stub.
_app2 = fb.AppScreen(colour=True, tier="truecolor")
_n_items = len(_app2.items)
_app2.card("narration", "")
check("--app commits no empty draft stub",
      len(_app2.items) == _n_items, _app2.items[-1:])

# An answer body is bold/dim/default - never hue of its own.
_ans = fb.TuiScreen(out=io.StringIO(), width=100, tier="truecolor")
_ans._plain_fallback = True
_ans.card("final", "## H\n\n> quote\n\n- item\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n"
                   "```python\nx = 1\n```\n\n[link](http://x)\n")
_ans_codes = sorted(set(re.findall(r"\x1b\[([0-9;]+)m", _ans.out.getvalue())))
_ANS_HUES = ("34", "35", "36")
_ans_out = _ans.out.getvalue()
_off = min([_ans_out.find("\x1b[%sm" % h) for h in _ANS_HUES] + [-1])
_off = _off if _off >= 0 else max(0, len(_ans_out) - 80)
check("an answer body never renders magenta or cyan",
      not [c for c in _ans_codes
           if c in _ANS_HUES or c.startswith("35;") or "38;5;13" in c],
      repr(_ans_out[max(0, _off - 140):_off + 80]))

# The footer is the run's status only; the box the operator types in says "you".
check("--app's status line carries the run status only; keys live in the rail",
      not hasattr(fb.AppScreen, "_hints_text")
      and "KEYS" in "".join(p for _, p in _app._sidebar_text().__pt_formatted_text__()),
      "hints still share the status line")
check("--app's input box is labeled 'you', not 'ask'",
      "".join(p for _, p in _app.composer.title.__pt_formatted_text__()).strip() == "you",
      _app.composer.title)

# --- the brand art in the rail ------------------------------------------------------
# Data, derived from the badge master by maintenance/make-brand-art.py, drawn as spans:
# raw ANSI inside a span renders as literal "[38;2;..." text (measured twice in this
# file), so the artifact is cells + colours and the screen composes the styles.
# The POLICY decides, not the ambient terminal: this shell's TERM is often `dumb`, where
# the ASCII mark is the correct output - so the Unicode checks force the policy and the
# fallback gets a check of its own.
_saved_unicode = fb.CONFIG["agent"].get("unicode")
try:
    fb.CONFIG["agent"]["unicode"] = "always"
    _art_cells = fb.rail_art_cells()
    check("the rail art artifact loads (24x9 braille cells)",
          len(_art_cells) == 9 and len(_art_cells[0]) == 24
          and any(cell for row in _art_cells for cell in row),
          (len(_art_cells), [len(r) for r in _art_cells[:2]]))
    _rail_spans = fb.AppScreen(colour=True, tier="truecolor")\
        ._sidebar_text().__pt_formatted_text__()
    _rail_txt = "".join(p for _, p in _rail_spans)
    check("...and rides the rail under the keys",
          "KEYS" in _rail_txt and any(0x2800 <= ord(c) <= 0x28FF for c in _rail_txt),
          _rail_txt[-260:])
    check("...as spans, never as ANSI escapes",
          "\x1b" not in _rail_txt, [p for _, p in _rail_spans if "\x1b" in p][:2])
    _art_widths = [len(p) for _, p in _rail_spans
                   if any(0x2800 <= ord(c) <= 0x28FF for c in p)]
    check("...within the rail's 26 columns", _art_widths and max(_art_widths) <= 25,
          _art_widths)
    _hex6 = re.compile(r"^#[0-9a-f]{6}$")
    check("...and every cell is a hex colour plus a braille glyph",
          all(_hex6.match(cell[0]) and 0x2800 <= ord(cell[1]) <= 0x28FF
              for row in _art_cells for cell in row if cell),
          [cell for row in _art_cells for cell in row if cell][:3])
    check("...with the accepted dot counts (the designer's render)",
          [sum(bin(ord(c[1]) - 0x2800).count("1") for c in row if c)
           for row in _art_cells] == [74, 117, 90, 98, 99, 69, 52, 7, 19],
          [sum(bin(ord(c[1]) - 0x2800).count("1") for c in row if c)
           for row in _art_cells])
finally:
    fb.CONFIG["agent"]["unicode"] = _saved_unicode

try:
    fb.CONFIG["agent"]["unicode"] = "never"
    _ascii_art = fb.AppScreen(colour=True, tier="truecolor")._rail_art()
finally:
    fb.CONFIG["agent"]["unicode"] = _saved_unicode
_ascii_txt = "".join(t for row in _ascii_art for _, t in row)
check("a terminal that cannot draw Braille gets the ASCII mark and the wordmark",
      ">_" in _ascii_txt and "tinycmdr" in _ascii_txt
      and not any(0x2800 <= ord(c) <= 0x28FF for c in _ascii_txt),
      _ascii_txt)
fb.CONFIG["agent"]["unicode"] = _saved_unicode

# --------------------------------------------- Ctrl-W gives the columns back
# The rail is a ConditionalContainer (`VSplit([rail, VerticalLine(), self.body])`), so
# hiding it is supposed to widen the pane. `_pane_width` subtracted its 26 columns
# either way, which wrapped every card 26 columns early in exactly the mode that exists
# to copy the transcript cleanly (measured 2026-10-06: 120 columns, rail hidden, cards
# built at 91).
_pane = fb.AppScreen(colour=True, tier="truecolor")
_pane.app = None                 # no terminal: the helper falls back to self.width
_pane.width = 120
check("the rail's columns are taken while it is shown",
      _pane._pane_width() == 120 - _pane.RAIL_WIDTH - 3, _pane._pane_width())
_pane.show_rail = False
check("and come back when Ctrl-W hides it", _pane._pane_width() == 120 - 3,
      _pane._pane_width())
_pane.show_rail = True
check("...with the accepted dot counts (the designer's render)",
      [sum(bin(ord(c[1]) - 0x2800).count("1") for c in row if c)
       for row in _art_cells] == [74, 117, 90, 98, 99, 69, 52, 7, 19],
      [sum(bin(ord(c[1]) - 0x2800).count("1") for c in row if c) for row in _art_cells])

# The done line and the rail read ONE counter (the run accumulator).
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
    check("the done line's steps/elapsed come from the run accumulator",
          "7 step(s) in 9s" in _done, _done)
finally:
    if _saved_usage_3 is None:
        fb.AGENT.last_usage.pop("cli", None)
    else:
        fb.AGENT.last_usage["cli"] = _saved_usage_3

# --- round-4: the pane replaces the run's WHOLE draft region ---------------
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
check("--app replaces the run's whole draft region, first draw included",
      not any(it[0] == "ansi" and "M4 Ultra" in str(it[1]) for it in _app4.items),
      [str(it[1])[:70] for it in _app4.items])

# --- copying out of the app: the rail can get out of the way ----------------------
# With mouse capture off, a drag is the TERMINAL's selection, and it cannot know where the
# panes are - so it spilled into the rail and copied its keys with the code (2026-10-03).
# Ctrl-W hides the rail so there is nothing to spill into.
_app_rail = fb.AppScreen(colour=True, tier="truecolor")
_app_rail._build()
check("--app starts with the rail shown", _app_rail.show_rail is True, _app_rail.show_rail)
_app_rail.show_rail = False
check("...and the rail is a conditional container, so hiding it is a layout change",
      "show_rail" in open(str(BASE / "tinycmdr.py"), encoding="utf-8").read(),
      "Ctrl-W bound")
check("...with the transcript left to take the width",
      _app_rail.RAIL_WIDTH == 26, _app_rail.RAIL_WIDTH)
_app_rail.show_rail = True

# --- round-4: no blank band around a table, and nothing trailing ----------
_t5 = fb.TuiScreen(out=io.StringIO(), width=90, tier="truecolor")
_t5._plain_fallback = True
_t5.card("final", "| Slot | Size |\n|---|---|\n| DIMM 1 | 8 GB |\n| DIMM 2 | 8 GB |\n\n"
                  "After the table.\n")
_plain5 = [re.sub(r"\x1b\[[0-9;]*m", "", l) for l in _t5.out.getvalue().splitlines()]
_rows5 = [l[1:-1] for l in _plain5 if l.startswith(("\u2503", "\u2502"))]
check("the answer card carries no blank band around a table",
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

# --- the run's key is a filename; the editing surface is not one (Windows
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

# --- one writer per terminal (measured 2026-09-25, a live render on macOS:
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

# --- the words of an answer appear once, as the bright card --------------
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

# --- a structured draft never streams its own pipes ---------------------
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

# --- with a toolbar the transcript prints no stats line -----------------
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

# --- `--app`: the alternate-screen mode ---------------------------
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
        # The page lane is a SIBLING door that main() starts, and this drives the console loop
        # alone: assert that here, where the claim lives, rather than by counting sockets.
        _webui_calls = []
        _real_webui = getattr(fb, "run_webui", None)
        if _real_webui is not None:
            fb.run_webui = lambda *a, **k: _webui_calls.append(a) or 0
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
            if _real_webui is not None:
                fb.run_webui = _real_webui

        _lines = [re.sub(r"\x1b\[[0-9;]*m", "", l) for l in app_screen.lines(79)]
        # " answer " rather than the heavy rule around it: the box set follows the console
        # (HEAVY on a VT terminal, a light or ASCII one on a Windows console), so matching the
        # rule made a correct card look missing there (measured 2026-10-08, windows-latest).
        _answer_cards = [i for i, l in enumerate(_lines) if " answer " in l]
        check("--app: the run draws exactly one answer card",
              len(_answer_cards) == 1, _lines)
        check("--app: the answer card replaces the draft, it does not stack on it",
              not any("drafting the answer" in l for l in _lines), _lines)
        check("--app: the run's stats stay in the status bar, not the transcript",
              not any("tok" in l for l in _lines)
              and "Done" in app_screen.status, (app_screen.status, _lines))
        check("--app: /exit leaves the loop and the app", fb._CLI["leave"] is True)
        # The census collected AF_INET sockets on the stated assumption that asyncio's
        # event-loop wakeup is an AF_UNIX socketpair. True on POSIX, false on Windows, where
        # CPython emulates socketpair with AF_INET - so three sockets from the LOOP read as
        # three from the app (measured 2026-10-08, windows-latest). The claim is asserted
        # where it lives instead: this lane must not start the page lane.
        check("--app's own surface starts no page lane (that is main()'s)",
              not _webui_calls, _webui_calls)

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

        # ...and now the same through REAL keys and mouse bytes, because every check above
        # called the handler DIRECTLY: ↑ and ↓ were advertised in the rail and bound to
        # nothing, and the wheel was dead on macOS/Linux (a POSIX terminal delivers it as a
        # mouse event at a coordinate, never as Keys.ScrollUp - only the Win32 driver makes
        # that key), so both read as covered while neither worked (measured 2026-09-30 in
        # this Application, headless, with a key pipe).
        _saved_mouse = os.environ.get("TINYCMDR_APP_MOUSE")
        os.environ["TINYCMDR_APP_MOUSE"] = "1"      # the wheel needs capture on
        try:
            _scr = fb.AppScreen(colour=False, tier="truecolor")
        finally:
            if _saved_mouse is None:
                del os.environ["TINYCMDR_APP_MOUSE"]
            else:
                os.environ["TINYCMDR_APP_MOUSE"] = _saved_mouse
        for _i in range(80):
            _scr.card("tool", "shell step %d" % _i)
        _scr._invalidate()
        _scr.app.output = _DummyOutput()
        _pane_h = _scr._pane_height()
        _last = max(0, len(_scr.lines(_scr._pane_width())) - _pane_h)
        _keys_sent = [("tail", None), ("up", "\x1b[A"), ("up2", "\x1b[A"), ("down", "\x1b[B"),
                      ("pageup", "\x1b[5~"), ("pagedown", "\x1b[6~"),
                      ("ctrl-home", "\x1b[1;5H"), ("ctrl-end", "\x1b[1;5F"),
                      ("typed", "hi"), ("up-with-text", "\x1b[A"),
                      ("pageup-with-text", "\x1b[5~"),
                      ("wheel-up-body", "\x1b[<64;40;10M"),
                      ("wheel-up-rail", "\x1b[<64;10;10M"),
                      ("wheel-down-body", "\x1b[<65;40;10M")]
        _observed = []

        def _feed_scroll():
            _time.sleep(0.5)
            for _label, _raw in _keys_sent:
                if _raw is not None:
                    _keys3.send_text(_raw)
                    _time.sleep(0.4)
                _observed.append((_label, _scr._top, _scr.autofollow))
            _keys3.send_text("\x11")                # c-q: leave
            _time.sleep(0.3)

        with _pipe_input() as _keys3:
            _scr.app.input = _keys3
            _threading.Thread(target=_feed_scroll, daemon=True).start()
            _scr.app.run()
        _walk = {label: (top, follow) for label, top, follow in _observed}
        check("--app: the pane opens following the tail",
              _walk["tail"] == (_last, True), _observed[:2])
        check("--app: one ↑ moves one line and stops following (the rail's own hint)",
              _walk["up"] == (_last - 1, False), _observed[:3])
        check("--app: a second ↑ moves one more line, ↓ moves back",
              _walk["up2"] == (_last - 2, False) and _walk["down"] == (_last - 1, False),
              _observed[2:5])
        check("--app: PgUp and PgDn move exactly a page, and leave following off",
              _walk["pageup"] == (_last - 1 - _pane_h, False)
              and _walk["pagedown"] == (_last - 1, False), _observed[4:6])
        check("--app: Ctrl-Home and Ctrl-End are the two ends",
              _walk["ctrl-home"] == (0, False) and _walk["ctrl-end"] == (_last, True),
              _observed[6:8])
        check("--app: with text in the composer the arrows are the caret, not the pane",
              _walk["up-with-text"] == (_last, True)
              and _walk["pageup-with-text"] == (_last - _pane_h, False), _observed[8:11])
        check("--app: the wheel scrolls three lines a notch, from either pane",
              _walk["wheel-up-body"] == (_last - _pane_h - 3, False)
              and _walk["wheel-up-rail"] == (_last - _pane_h - 6, False), _observed[11:13])
        check("--app: ...and back down a notch", _walk["wheel-down-body"] == (_last - _pane_h - 3, False),
              _observed[13:])

        # copying an item OUT of the pane: the raw text of a card, not the frame it was
        # painted in. Driven through the real Application so the KEYS are graded, with a
        # recording output so the OSC 52 sequence is read back byte for byte. The host's own
        # clipboard tool is stubbed here on purpose: it would clobber the developer's real
        # clipboard, and the smoke run on a pty is where that door is exercised for real.
        _saved_copy_file, _saved_helper = fb.COPY_FILE, fb.copy_via_host_tool
        _copy_dir = Path(tempfile.mkdtemp(prefix="fbcopy-"))
        _copy_file = _copy_dir / "copy.txt"
        fb.COPY_FILE = _copy_file
        _helper_calls = []
        fb.copy_via_host_tool = lambda text: (_helper_calls.append(len(text)), ("stub-tool", True))[1]
        try:
            check("the OSC 52 escape is the terminal-clipboard one, base64 and all",
                  fb.osc52_sequence("hi") == "\x1b]52;c;aGk=\x1b\\", fb.osc52_sequence("hi"))
            check("the host clipboard tool picked for this platform exists or is a known name",
                  all(isinstance(c, list) and c for c in fb.clipboard_commands()),
                  fb.clipboard_commands())

            class _RecOut(_DummyOutput):
                def __init__(self):
                    super().__init__()
                    self.raw = []

                def write_raw(self, data):
                    self.raw.append(data)

            _cscr = fb.AppScreen(colour=False, tier="truecolor")
            _cscr.card("tool", "`shell` echo hi")
            _cscr.card("tool_done", "-> hi (2 chars)")
            _cscr.card("final", "# Answer\n\nbody of the answer")
            _cscr._invalidate()
            _rec = _RecOut()
            _cscr.app.output = _rec
            _copies = []

            def _feed_copy():
                _time.sleep(0.5)
                for _key in ("\x19", "\x19", "\x19", "\x02"):
                    _keys4.send_text(_key)
                    _time.sleep(0.4)
                    _copies.append((_cscr.status,
                                    _copy_file.read_text(encoding="utf-8")
                                    if _copy_file.exists() else ""))
                _keys4.send_text("\x11")
                _time.sleep(0.3)

            with _pipe_input() as _keys4:
                _cscr.app.input = _keys4
                _threading.Thread(target=_feed_copy, daemon=True).start()
                _cscr.app.run()
            _b64 = [s.split(";", 2)[2].rstrip("\x1b\\") for s in _rec.raw if s.startswith("\x1b]52;c;")]
            _osc = [base64.b64decode(p).decode("utf-8") for p in _b64]
            check("--app: Ctrl-Y copies the newest item's own text, not the painted frame",
                  _copies[0][1] == "# Answer\n\nbody of the answer"
                  and _osc and _osc[0] == "# Answer\n\nbody of the answer", (_copies[0], _osc[:1]))
            check("--app: ...and the status line names the item and where it sits",
                  _copies[0][0].startswith("copied answer 3/3 -")
                  and "chars" in _copies[0][0], _copies[0][0])
            check("--app: Ctrl-Y again walks back, item by item, to the call",
                  [_c[1] for _c in _copies[:3]] == ["# Answer\n\nbody of the answer",
                                                    "-> hi (2 chars)", "`shell` echo hi"],
                  [_c[1] for _c in _copies[:3]])
            check("--app: Ctrl-B copies the whole transcript, in the order it was drawn",
                  _copies[3][1] == "`shell` echo hi\n\n-> hi (2 chars)\n\n# Answer\n\nbody of the answer",
                  _copies[3][1])
            # 0600 is the POSIX spelling of "the reader's own". Windows has no group/world
            # bits and reports 0o666 for a writable file, so what is graded there is the
            # promise itself: the file is the reader's to read and rewrite, and it sits in the
            # install dir rather than anywhere on the box (measured 2026-10-08).
            _mode = _copy_file.stat().st_mode
            if os.name == "nt":
                # Read and rewrite, which is what "the reader's own" means where there are no
                # group/world bits. NOT the parent: this suite redirects the copy file into its
                # own temp dir, so its location is the suite's business (measured 2026-10-08,
                # windows-latest: the location clause failed a correct file).
                check("--app: the fallback file is the reader's own",
                      os.access(_copy_file, os.R_OK | os.W_OK)
                      and (_mode & 0o600) == 0o600,
                      (oct(_mode & 0o777), str(_copy_file.parent)))
            else:
                check("--app: the fallback file is the reader's own, 0600",
                      (_mode & 0o777) == 0o600, oct(_mode))
            check("--app: the host's clipboard tool is offered the same text",
                  _helper_calls and _helper_calls[-1] == len(_copies[3][1]), _helper_calls)
        finally:
            fb.COPY_FILE, fb.copy_via_host_tool = _saved_copy_file, _saved_helper
            shutil.rmtree(_copy_dir, ignore_errors=True)

        # the model picker: the predecessor harness's list-you-move-through, drawn INSIDE this Application
        # (a second prompt_toolkit Application cannot own this terminal). Driven through the
        # app's own key pipe, because the binding is exactly what was missing: bare /model
        # used to print a Commands box and the reader retyped an exact name.
        _pick_rows = [("qwen3-14b", "local http://127.0.0.1:8081/v1", True),
                      ("deepseek-v4-flash", "http://10.0.0.5:8081/v1", False),
                      ("glm-4.6", "http://10.0.0.5:8081/v1", False)]
        _picked = []
        _appp = fb.AppScreen(colour=False, tier="truecolor")
        _appp.app.output = _DummyOutput()

        def _pick_state():
            return fb.ModelPick(_pick_rows, current="qwen3-14b", title="Select model",
                                scope="ENTER switches this session")

        with _pipe_input() as _k5:
            _appp.app.input = _k5
            _st = _pick_state()

            def _feed_pick():
                _time.sleep(0.5)
                _appp.open_model_pick(_st, lambda name: _picked.append(name))
                _time.sleep(0.3)
                _k5.send_text("gl")              # type to filter
                _time.sleep(0.4)
                _picked.append(("typed", _st.filter, _appp.input.text, _appp.status))
                _k5.send_text("\r")              # ENTER takes it
                _time.sleep(0.4)
                _k5.send_text("\x11")            # c-q: leave
                _time.sleep(0.3)

            _threading.Thread(target=_feed_pick, daemon=True).start()
            _appp.app.run()
        check("--app: typing in the picker filters it and never reaches the composer",
              _picked[0][:3] == ("typed", "gl", ""), _picked[0])
        check("--app: ENTER hands over the filtered model",
              _picked[1:] == ["glm-4.6"], _picked)
        check("--app: the picker is gone once it closes", _appp.pick is None
              and _appp._pick_text() == [], _appp.pick)
        check("--app: the status line said what the keys do while it was open",
              "ESC leaves it" in _picked[0][3] and "ENTER takes it" in _picked[0][3],
              _picked[0][3])

        _cancel = []
        _appp2 = fb.AppScreen(colour=False, tier="truecolor")
        _appp2.app.output = _DummyOutput()
        _alive = []
        with _pipe_input() as _k6:
            _appp2.app.input = _k6
            _st2 = _pick_state()

            def _feed_esc():
                _time.sleep(0.5)
                _appp2.open_model_pick(_st2, lambda name: _cancel.append(name))
                _time.sleep(0.3)
                _k6.send_text("\x1b")            # ESC: leave the model alone
                _time.sleep(0.6)
                _alive.append(_appp2.app.is_running)
                _k6.send_text("\x11")
                _time.sleep(0.3)

            _threading.Thread(target=_feed_esc, daemon=True).start()
            _appp2.app.run()
        check("--app: ESC leaves the model alone", _cancel == [None] and _appp2.pick is None,
              _cancel)
        check("--app: ...and does not leave the app (the exit key still does)",
              _alive == [True] and fb._CLI.get("leave") is True,
              (_alive, fb._CLI.get("leave")))

        # the shell door's host: its own Application, real bytes, injected input/output
        for _label, _keys, _want in (("down+ENTER", "\x1b[B\r", "deepseek-v4-flash"),
                                     ("typed+ENTER", "gl\r", "glm-4.6"),
                                     ("ESC", "\x1b", None)):
            _host_state = _pick_state()
            with _pipe_input() as _k7:
                _threading.Thread(
                    target=lambda k=_k7, s=_keys: (_time.sleep(0.4), k.send_text(s)),
                    daemon=True).start()
                _got = fb.run_model_pick(_host_state, input=_k7, output=_DummyOutput())
            check("the shell picker: %s -> %r" % (_label, _want), _got == _want, _got)

                # ---- the answer reads as a REPLY: the question above it --------------------
        # Operator's test user, 2026-09-30: "it would be nice to see your own original
        # question at the final response output kind of in a referenced way or how a
        # 'reply' looks". In `--app` the request was nowhere on screen - the composer
        # clears when it sends - so the answer card had nothing saying what it answered.
        check("the reply reference is one capped, flattened line",
              fb._reply_ref("a\n\nvery   long\tquestion") == "re: a very long question"
              and "\n" not in fb._reply_ref("x" * 400)
              and fb._reply_ref("x" * 400).endswith("...")
              and len(fb._reply_ref("x" * 400)) <= fb.REPLY_REF_CHARS + 8,
              fb._reply_ref("x" * 400)[-20:])
        check("...and an empty question draws no reference at all",
              fb._reply_ref("") == "" and fb._reply_ref(None) == "" and fb._reply_ref("   ") == "",
              fb._reply_ref("   "))
        check("the app's own ellipsis is used when the terminal is not ascii-only",
              fb._reply_ref("y" * 300, "\u2026").endswith("\u2026"),
              fb._reply_ref("y" * 300, "\u2026")[-4:])

        _cards = []

        class _CardRec(fb.TuiScreen):
            def card(self, kind, text, foot=""):
                _cards.append((kind, text))

        _scr_rep = _CardRec(out=io.StringIO(), width=90, tier="truecolor")
        _scr_rep.card("tool", "`shell` echo hi")
        fb._draw_answer(_scr_rep, "how tall is the tower?", "# Tower\n\n310 m.")
        check("the card drawn before the answer is the question, dim and marked",
              [k for k, _t in _cards] == ["tool", "reply", "final"]
              and _cards[1][1] == "re: how tall is the tower?", _cards)
        check("...and the reference is copyable, like every other item",
              any(t == "re: how tall is the tower?" for _k, t in _cards), _cards)

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
        # clean run.
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

        # A failure on the app's OWN thread never leaves Application.run(): asyncio
        # hands it to the loop's exception handler, and prompt_toolkit's own handler
        # printed the traceback with print() - the pane, under --app - then waited
        # for ENTER inside the alternate screen. That is the "crash" that left the
        # terminal open on a frozen frame, so the app
        # installs its own handler: record, log, and ask the ONE exit.
        # A 256-colour terminal with no COLORTERM (stock Terminal.app) used to crash
        # `--app` at CONSTRUCTION: the palette's rich-only `color(73)` spelling reached
        # prompt_toolkit's Style.from_dict, which raised before any screen existed, and
        # app_wanted() is true there so there was no inline fallback. Building the
        # screen at that tier IS the assertion.
        try:
            _tier256 = fb.AppScreen(colour=False, tier="256")
            _built, _built_err = _tier256.app is not None, ""
        except Exception as _e:                                   # noqa: BLE001
            _built, _built_err = False, "%s: %s" % (type(_e).__name__, _e)
        check("--app can be CONSTRUCTED on a 256-colour terminal (no COLORTERM)",
              _built, _built_err)

        _crash_scr = fb.AppScreen(colour=False, tier="truecolor")
        _handler = getattr(_crash_scr.app, "_handle_exception", None)
        check("--app hands the event loop its own exception handler",
              getattr(_handler, "__self__", None) is _crash_scr
              and getattr(_handler, "__func__", None) is fb.AppScreen._app_exception,
              _handler)

        _exits2 = []

        class _FakeApp2:
            is_done = False
            loop = None

            @staticmethod
            def exit():
                _exits2.append(1)

        _real_app2 = _crash_scr.app
        _crash_scr.app = _FakeApp2()
        _crash_scr._exit_requested = False
        _printed = io.StringIO()
        with contextlib.redirect_stdout(_printed):
            _crash_scr._app_exception(
                None, {"exception": RuntimeError("BOOM handler"),
                       "message": "Exception in callback boom()"})
        _crash_scr.app = _real_app2
        _text = _crash_scr.take_crash()
        check("--app: a loop failure is recorded, never printed into the pane",
              _printed.getvalue() == "" and "BOOM handler" in _text
              and "Exception in callback boom()" in _text, _printed.getvalue()[:80])
        check("--app: ...it asks the one exit, and the record is taken once",
              _exits2 == [1] and _crash_scr.take_crash() == "", (_exits2, _text[:60]))

        # A failure out of Application.run() itself (a renderer or layout error, a
        # closed stdin) used to skip the reprint entirely: print_final_inline() sat
        # after the try/finally. It reports on the REAL stream and exits non-zero.
        _reprints = []

        class _DeadScreen:
            def __init__(self, colour=True, tier=None):
                self._crash = ""

            def run(self):
                raise RuntimeError("BOOM run")

            def note_crash(self, text):
                self._crash = text

            def take_crash(self):
                text, self._crash = self._crash, ""
                return text

            def print_final_inline(self):
                _reprints.append(1)

            def write_line(self, line):
                pass

        _saved_screen_cls = fb.AppScreen
        _saved_worker = fb._cli_app_worker
        _saved_console_off = fb.log_console_off
        _saved_cli_here = dict(fb._CLI)
        fb.AppScreen = _DeadScreen
        fb._cli_app_worker = lambda: None
        fb.log_console_off = lambda: None
        _stderr = io.StringIO()
        try:
            with contextlib.redirect_stderr(_stderr):
                try:
                    fb._run_cli_app()
                except SystemExit as _exit:
                    _code = _exit.code
                else:
                    _code = None
        finally:
            fb.AppScreen = _saved_screen_cls
            fb._cli_app_worker = _saved_worker
            fb.log_console_off = _saved_console_off
            fb._CLI.clear()
            fb._CLI.update(_saved_cli_here)
        check("--app: a failure out of screen.run() reports on the real stream, rc=1",
              _code == 1 and "BOOM run" in _stderr.getvalue(),
              (_code, _stderr.getvalue()[:100]))
        check("--app: ...and the last answer is still reprinted", _reprints == [1], _reprints)

        # A kill mid-event-loop used to end the process with the alternate screen still
        # active: the terminal was left inside the app's frame with no way back. The
        # handlers are installed for the app's lifetime and put back after.
        class _SigStub:
            SIGTERM, SIGHUP = 15, 1

            def __init__(self):
                self.calls = []

            def signal(self, sig, handler):
                self.calls.append((sig, handler))
                return None

        _sig = _SigStub()
        _real_signal_mod = fb.signal
        _reprints2 = []

        class _SigScreen(_DeadScreen):
            def run(self):
                return None

        _saved_screen_cls2 = fb.AppScreen
        fb.AppScreen = _SigScreen
        fb.signal = _sig
        _saved_cli_sig = dict(fb._CLI)
        try:
            fb._run_cli_app()
        finally:
            fb.AppScreen = _saved_screen_cls2
            fb.signal = _real_signal_mod
            fb._CLI.clear()
            fb._CLI.update(_saved_cli_sig)
        _installed = [s for s, h in _sig.calls if callable(h)]
        _restored = [s for s, h in _sig.calls if not callable(h)]
        check("--app catches SIGTERM and SIGHUP while it owns the terminal",
              _installed == [_SigStub.SIGTERM, _SigStub.SIGHUP], _sig.calls)
        check("...and puts the previous handlers back", _restored == _installed, _sig.calls)
    finally:
        fb.drive_run = _saved_drive
        fb._CLI.clear()
        fb._CLI.update(_saved_cli)

print(f"\n{len(PASSES)} passed, {len(FAILS)} failed")
sys.exit(1 if FAILS else 0)

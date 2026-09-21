"""The console's screen: rich cards, and the plain lines underneath them.

    python tests/test_tui.py

The screen is decoration on text the plain path already prints, so the properties
that matter are: a pipe or tinycmdr_PLAIN still gets plain lines, every tone lands on
the card it should, the run's done line is not mistaken for the answer, and what
reaches the terminal is ANSI that prompt_toolkit can render (raw ESC bytes get
sanitized into visible "[1;33m" garbage).
"""
import importlib.util
import io
import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SRC = BASE / os.environ.get("tinycmdr_SRC", "tinycmdr.py")
spec = importlib.util.spec_from_file_location("tinycmdr_tui_under_test", SRC)
fb = importlib.util.module_from_spec(spec)
sys.modules["tinycmdr_tui_under_test"] = fb
spec.loader.exec_module(fb)

PASSES = []
FAILS = []


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
os.environ["tinycmdr_PLAIN"] = "1"
check("tinycmdr_PLAIN=1 refuses the screen even with a terminal", fb.tui_wanted() is False)
del os.environ["tinycmdr_PLAIN"]

if not HAVE:
    print("\nrich/prompt_toolkit are absent: the screen itself cannot be graded here")
    print(f"\n{len(PASSES)} passed, {len(FAILS)} failed")
    sys.exit(1 if FAILS else 0)

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
      and p.border_style == "cyan", str(p.title))
screen.card("tool_done", "shell ls -la · 0.4s")
p = screen.shown[-1]
check("a result is green", str(p.title).strip() == "result"
      and p.border_style == "#5fbf7f", str(p.title))
screen.card("tool_fail", "shell docker ps · [exit 1]")
p = screen.shown[-1]
check("a failure is red", str(p.title).strip() == "failed"
      and p.border_style == "red", str(p.title))
screen.card("final", "# Heading\n\n- a\n- b")
p = screen.shown[-1]
check("the answer is the accent card", str(p.title).strip() == "answer"
      and p.border_style == "blue", str(p.title))
check("...and it is rendered as markdown, not raw text",
      type(p.renderable).__name__ == "Markdown")
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
check("the printed card carries real SGR escapes", "\x1b[" in text)
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

svg = BASE / "tests" / "eval-runs" / "tui-preview.svg"
svg.parent.mkdir(parents=True, exist_ok=True)
written = screen.export_svg(svg)
body = written.read_text(encoding="utf-8")
check("export_svg writes the whole screen", "<svg" in body[:400])
check("...with the answer's text in it", "Heading" in body)
svg.unlink()

print(f"\n{len(PASSES)} passed, {len(FAILS)} failed")
sys.exit(1 if FAILS else 0)

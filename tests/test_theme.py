"""One theme file decides the terminal AND the page - and every tier of it applies.

theme.toml is host-owned (update seeds it, never overwrites it), so it is the operator's
one lever on colour. It had a silent hole: the merge looked for a nested "truecolor" key
that no theme file has, so `[themes.NAME]` - the section holding the operator's actual
hexes - was ignored, and only `[themes.NAME.256]` / `[themes.NAME.ansi]` ever applied.
Nothing failed; the colours simply were not theirs (found 2026-10-04 wiring the web page
to the same palette: the page took the file and did not move).

This suite grades the file -> palette merge itself, and the fact the page reads the SAME
palette (one file, two surfaces, no drift). Hermetic: a staged copy of the module, its own
config.json and theme.toml.

    python tests/test_theme.py
"""
import importlib.util
import json
import shutil
import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "tinycmdr.py"
PASSES, FAILS = [], []
BUILTIN_BG = "#0F1114"          # the built-in roman-night background
BUILTIN_GOLD = "#D7A94A"


def check(cond, what, extra=""):
    if not cond:
        FAILS.append(what)
        print(f"FAIL {what}\n     {extra}")
    else:
        PASSES.append(what)
        print(f"ok   {what}")


def stage(root, theme_text=None, name="box"):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SRC, d / "tinycmdr.py")
    (d / "config.json").write_text(json.dumps(
        {"llm": {"base_url": "http://127.0.0.1:9/v1", "model": "main"}}), encoding="utf-8")
    if theme_text is not None:
        (d / "theme.toml").write_text(theme_text, encoding="utf-8")
    return d


def load(d, name):
    spec = importlib.util.spec_from_file_location("theme_" + name, d / "tinycmdr.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


FULL = """\
default = "suite"

[themes.suite]
background   = "#010203"
panel        = "#040506"
text         = "#070809"
gold         = "#0a0b0c"

[themes.suite.256]
background   = "#101112"
gold         = "#131415"

[themes.suite.ansi]
background   = "black"
"""


def main():
    work = Path(tempfile.mkdtemp(prefix="fbtheme-"))
    try:
        # ---- every tier of the file lands, and truecolor is the bare section --------
        d = stage(work, FULL)
        m = load(d, "full")
        pal = m.theme_palette()
        check(pal["truecolor"]["background"] == "#010203",
              "the bare [themes.NAME] section IS the truecolor tier",
              pal["truecolor"]["background"])
        check(pal["truecolor"]["gold"] == "#0a0b0c",
              "every role in it applies, not just the first",
              pal["truecolor"].get("gold"))
        check(pal["256"]["background"] == "#101112" and pal["256"]["gold"] == "#131415",
              "the .256 sub-table applies", pal["256"].get("background"))
        check(pal["16"]["background"] == "black",
              "the .ansi sub-table applies", pal["16"].get("background"))
        check(pal["truecolor"]["ember"] == "#D9782D",
              "a role the file does not name keeps its built-in value",
              pal["truecolor"].get("ember"))

        # ---- one file, two surfaces: the PAGE reads this same palette --------------
        page = m._web_theme_vars()
        check(page["--bg"] == "#010203" and page["--gold"] == "#0a0b0c",
              "the page's CSS variables come from the same file", page)
        check(page["--ember"] == "#D9782D",
              "and its unnamed roles fall back to the built-in roman-night", page)

        # ---- a broken file costs a colour, never the app ---------------------------
        d = stage(work, 'default = "suite"\n\n[themes.suite]\n'
                        'background = "not-a-colour"\nnonsense = "#010203"\n',
                  name="bad")
        m = load(d, "bad")
        pal = m.theme_palette()
        check(pal["truecolor"]["background"] == BUILTIN_BG,
              "a non-hex value is ignored, the built-in stands",
              pal["truecolor"]["background"])
        check(m._web_theme_vars()["--bg"] == BUILTIN_BG,
              "and the page never paints with a value the parser refused")

        # ---- a default naming a section that is not there --------------------------
        d = stage(work, 'default = "nope"\n\n[themes.suite]\nbackground = "#010203"\n',
                  name="missing")
        m = load(d, "missing")
        check(m.theme_palette()["truecolor"]["background"] == BUILTIN_BG,
              "a default that names nothing falls back to the built-in palette")
        check(m._web_theme_vars()["--bg"] == BUILTIN_BG,
              "...for the page too")

        # ---- no theme file at all ---------------------------------------------------
        d = stage(work, None, name="none")
        m = load(d, "none")
        check(m.theme_palette()["truecolor"]["background"] == BUILTIN_BG
              and m.theme_palette()["truecolor"]["gold"] == BUILTIN_GOLD,
              "no theme.toml: the built-in roman-night palette")
        check(m._web_theme_vars()["--bg"] == BUILTIN_BG,
              "and the page shows it with no file")
    finally:
        shutil.rmtree(work, ignore_errors=True)

    print()
    if FAILS:
        print("%d check(s) failed" % len(FAILS))
        return 1
    print(f"{len(PASSES)} passed, 0 failed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Zero-prompt-bytes A/A: what a batch costs the payload floor, in both tool shapes.

Stages ONE fixture install for each build (same fixture-config.json, same skills), then
prints the payload floor: build_system_prompt() chars, select_tool_schemas() chars,
est_tokens of each, and the tools-visible list. Any delta is the rent this batch would
charge every turn of every run.

TWO SHAPES, because they answer different questions:

    fixture  no custom tools in ./tools/ - the shape the staged install has. A batch that
             changes nothing here costs a fresh install nothing.
    live     the repo's own tools/*.py copied in - the shape every box we run has. This is
             the leg a tool-index batch is judged on, because the index only exists when
             there are custom tools (measured 2026-09-25: 9 tools = 1,306 ch of prompt
             before the tool tree, 253 ch after).

    python tests/aa_payload_floor.py                 # probe-pre.py vs tinycmdr.py
    python tests/aa_payload_floor.py <before> <after>
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

TESTS = Path(__file__).resolve().parent
REPO = TESTS.parent
BASE = REPO
sys.path.insert(0, str(TESTS))
import run_scenario  # noqa: E402


def floor(app_path, label, live=False):
    workdir = Path(tempfile.mkdtemp(prefix="aa-floor-"))
    os.environ["TINYCMDR_TEST_APP"] = str(app_path)
    try:
        run_scenario.stage_install(workdir, 24000)
        if live:
            (workdir / "tools").mkdir(exist_ok=True)
            for f in sorted((REPO / "tools").glob("*.py")):
                shutil.copy2(f, workdir / "tools" / f.name)
        fb = run_scenario.load(workdir)
        prompt = fb.build_system_prompt()
        schemas = json.dumps(fb.select_tool_schemas("aa-probe"))
        names = sorted(fb.visible_tool_names("aa-probe"))
        hidden = sorted(fb.hidden_tools("aa-probe"))
        custom = sorted(fb.REGISTRY.custom)
        print(f"--- {label} ({Path(app_path).name}{', live tools' if live else ''})")
        print(f"    custom tools: {len(custom)} {custom}")
        print(f"    prompt      : {len(prompt):6d} ch  est_tokens {fb.est_tokens(prompt):6d}")
        print(f"    schemas     : {len(schemas):6d} ch  est_tokens {fb.est_tokens(schemas):6d}")
        print(f"    visible     : {len(names)} tools {names}")
        print(f"    hidden      : {len(hidden)} tools {hidden}")
        return (len(prompt), len(schemas), tuple(names))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
        os.environ.pop("TINYCMDR_TEST_APP", None)


_a = Path(sys.argv[1]) if len(sys.argv) > 1 else BASE / "probe-pre.py"
_b = Path(sys.argv[2]) if len(sys.argv) > 2 else BASE / "tinycmdr.py"
if not _a.exists():
    print(f"no before-build at {_a} - copy the pre-edit file there first "
          f"(cp tinycmdr.py probe-pre.py) or pass two paths")
    sys.exit(2)

rows = []
for live in (False, True):
    old = floor(_a, "BEFORE", live)
    new = floor(_b, "AFTER", live)
    rows.append((("live tools" if live else "fixture"), old, new))
    print()
    if old == new:
        print(f"A/A IDENTICAL [{rows[-1][0]}]: the batch costs ZERO prompt and ZERO schema "
              f"bytes.")
    else:
        print(f"DIFFERENT [{rows[-1][0]}]: prompt {old[0]} -> {new[0]} "
              f"({new[0] - old[0]:+d} ch, {new[0] // 4 - old[0] // 4:+d} est-tok), "
              f"schemas {old[1]} -> {new[1]} ({new[1] - old[1]:+d} ch)")
    print()

total_old = sum(r[1][0] + r[1][1] for r in rows)
total_new = sum(r[2][0] + r[2][1] for r in rows)
print("floor, both shapes summed: %d -> %d ch (%+d)" % (total_old, total_new,
                                                        total_new - total_old))

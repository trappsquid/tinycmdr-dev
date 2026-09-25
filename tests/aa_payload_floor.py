"""Zero-prompt-bytes A/A: the fix batch must not change the prompt or the schema block.

Stages ONE fixture install for each build (same fixture-config.json, same skills, same
tools dir), then prints the payload floor for both: build_system_prompt() chars,
select_tool_schemas() chars, est_tokens of each, and the tools-visible list. Any delta is
the rent this batch would charge every turn of every run, which the standing rule says must
be zero unless a measured miss justifies it.
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


def floor(app_path, label):
    workdir = Path(tempfile.mkdtemp(prefix="aa-floor-"))
    os.environ["TINYCMDR_TEST_APP"] = str(app_path)
    try:
        run_scenario.stage_install(workdir, 24000)
        fb = run_scenario.load(workdir)
        prompt = fb.build_system_prompt()
        schemas = json.dumps(fb.select_tool_schemas("aa-probe"))
        names = sorted(fb.visible_tool_names("aa-probe"))
        hidden = sorted(fb.hidden_tools("aa-probe"))
        print(f"--- {label} ({Path(app_path).name})")
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
old = floor(_a, "BEFORE")
new = floor(_b, "AFTER")
print()
if old == new:
    print("A/A IDENTICAL: the batch costs ZERO prompt and ZERO schema bytes.")
else:
    print("DIFFERENT - rent to justify:")
    for i, k in enumerate(("prompt chars", "schema chars", "visible tools")):
        if old[i] != new[i]:
            print(f"   {k}: {old[i]} -> {new[i]}")

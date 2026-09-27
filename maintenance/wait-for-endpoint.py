"""Wait for the model box to answer again, then run the cases that hit infra errors.

The bot reports infrastructure failures clearly, so a one-line ask is both the probe and a
real check: when it stops returning the infra banner, the box is back and the cases run.

    python3 maintenance/wait-for-endpoint.py   # polls up to ~45 min, then runs pass 2

Pass-2 results go to $TINYCMDR_CASES_DIR/pass2/ so the infra-error evidence from pass 1 stays.
"""
import importlib.util
import os
import pathlib
import sys
import time

spec = importlib.util.spec_from_file_location(
    "dh", pathlib.Path(__file__).with_name("drive-web-cases.py"))
dh = importlib.util.module_from_spec(spec)
sys.modules["dh"] = dh
spec.loader.exec_module(dh)                     # main() is guarded, so importing is inert

dh.OUT = pathlib.Path(os.environ.get("TINYCMDR_CASES_DIR")
                      or "/tmp/tinycmdr-web-cases") / "pass2"
dh.OUT.mkdir(parents=True, exist_ok=True)

PASS2 = [
    ("ask-out-of-scope", "What is the weather in Tokyo tomorrow?"),
    ("ask-remember", "Remember that the projector lives on the guest wifi."),
    ("ask-task-add", "Add a task to rotate the logs tomorrow morning."),
    ("ask-destructive", "Delete every file in /tmp/tc-scratch that I have not touched in a week."),
    ("ask-install", "Install jq with homebrew for me."),
    ("ask-session-recall", "Summarize what you have done in this conversation so far."),
    ("ask-after-new", "What was my previous question? Answer in one line."),
    ("ask-fresh-isolation", "What did I tell you in your other conversations today?"),
    ("ask-second-message", "Actually, which of those two files is bigger?"),   # continuity in one session
    ("ask-typo", "chekc teh disk usage plz"),
    ("ask-ambiguous", "Do the thing."),
    ("ask-long-output", "List the biggest 5 files under ~/Documents with their sizes."),
]

LOG = pathlib.Path.home() / "tinycmdr" / "tinycmdr.log"


def box_is_back():
    rec = dh.run_case("probe-model", "Reply with exactly: ready")
    dh.write_case_file(rec)
    text = (rec.get("reply") or "")
    if "infrastructure failure" in text or not text.strip():
        return False, text[:100]
    return True, text[:80]


def main():
    deadline = time.time() + 45 * 60
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        ok, note = box_is_back()
        print("attempt %d: %s  %r" % (attempt, "UP" if ok else "down", note), flush=True)
        if ok:
            break
        time.sleep(90)
    else:
        print("gave up waiting: the box never answered", flush=True)
        return

    for i, (name, message) in enumerate(PASS2, 1):
        print("=== pass2 [%d/%d] %s" % (i, len(PASS2), name), flush=True)
        rec = dh.run_case(name, message)
        dh.write_case_file(rec)
        print("    done=%s in %ss tools=%d errors=%d reply=%r"
              % (rec["done"], rec["seconds"], len(rec["tool_calls"]), len(rec["errors"]),
                 (rec["reply"] or "")[:140]), flush=True)
    (dh.OUT / "summary.json").write_text(
        __import__("json").dumps(dh.RESULTS, indent=2, default=str), encoding="utf-8")
    print("pass 2 complete", flush=True)


if __name__ == "__main__":
    main()

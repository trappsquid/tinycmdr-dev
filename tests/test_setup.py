"""`tinycmdr setup` asks about web-search consent, and writes the answer.

The wizard covered the model endpoint and the two chat gateways, and nothing else: the
egress consent that `web_search`/`fetch_url` answer to was only asked by the INSTALLER
and by `tinycmdr config set`, so an existing install had no interactive door to it. A
lookup that needs the web then came back "BLOCKED ... allow_cloud_egress is false" with
the operator holding no obvious way to say yes.

The wizard is driven here through a fake tty (it refuses a pipe), with every other
answer left empty so only the search section is doing anything.

    python tests/test_setup.py
"""
import contextlib
import importlib.util
import io
import json
import shutil
import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "tinycmdr.py"
PASSES, FAILS = [], []


def check(cond, what, extra=""):
    if not cond:
        FAILS.append(what)
        print(f"FAIL {what}\n     {extra}")
    else:
        PASSES.append(what)
        print(f"ok   {what}")


class FakeTTY(io.StringIO):
    """input() reads this, and isatty() lets the wizard past its guard."""

    def isatty(self):
        return True


def stage(dirpath, egress):
    dirpath.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SRC, dirpath / "tinycmdr.py")
    (dirpath / "config.json").write_text(json.dumps({
        "llm": {"base_url": "http://127.0.0.1:9/v1", "model": "main"},
        "search": {"allow_cloud_egress": egress},
    }), encoding="utf-8")
    return dirpath


def drive(dirpath, answer):
    """Run the wizard with every answer empty but the last one."""
    spec = importlib.util.spec_from_file_location("setup_" + dirpath.name,
                                                  dirpath / "tinycmdr.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    # 6 prompts in order: url, model, api-key, Mattermost?, Telegram?, egress.
    mod.__dict__["_ANSWERS"] = ["", "", "", "", "", answer]
    old_in = sys.stdin
    sys.stdin = FakeTTY("\n".join(mod.__dict__["_ANSWERS"]) + "\n")
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = mod.run_setup()
    finally:
        sys.stdin = old_in
    written = json.loads((dirpath / "config.json").read_text(encoding="utf-8"))
    return rc, buf.getvalue(), written, mod


def main():
    work = Path(tempfile.mkdtemp(prefix="fbsetup-"))
    try:
        # a fresh install says yes
        d = stage(work / "yes", False)
        rc, out, written, mod = drive(d, "y")
        check(rc == 0, f"the wizard completes ({rc})", out[-300:])
        check("off this LAN" in out,
              "the search question is asked, and names what it means", out[-500:])
        check(written.get("search", {}).get("allow_cloud_egress") is True,
              "answering yes writes search.allow_cloud_egress = true",
              json.dumps(written.get("search")))
        check(mod.CONFIG["search"]["allow_cloud_egress"] is True,
              "and the running process picks it up without a restart")
        check("Web search" in out and "off-LAN providers allowed" in out,
              "the summary reports it", out[-400:])

        # and no turns it back off
        d = stage(work / "no", True)
        rc, out, written, mod = drive(d, "n")
        check(rc == 0 and written["search"]["allow_cloud_egress"] is False,
              "answering no writes false", json.dumps(written.get("search")))
        check("this LAN only" in out, "and the summary says so", out[-300:])

        # Enter keeps whatever is already there, both ways
        for current in (True, False):
            d = stage(work / ("keep-%s" % current), current)
            rc, out, written, mod = drive(d, "")
            check(written["search"]["allow_cloud_egress"] is current,
                  f"Enter keeps the current value ({current})",
                  json.dumps(written.get("search")))

        # ---- the endpoint is PROBED before the wizard moves on --------------------
        # Operator, 2026-09-30: "there should be a point in the interactive installer that
        # checks if your link is even reachable before it continues". The staged URL is
        # 127.0.0.1:9 - a real, refused port - so this is the real probe, not a stub.
        d = stage(work / "reach", False)
        rc, out, written, mod = drive(d, "")
        check("\u2717 no answer from http://127.0.0.1:9/v1" in out
              and "tinycmdr model endpoint" in out,
              "a stored endpoint that does not answer is named, once, and pointed at the fix",
              out[-600:])
        check(rc == 0 and written["llm"]["base_url"] == "http://127.0.0.1:9/v1",
              "...and the wizard still finishes: the rest of the install has work to do", rc)

        # a TYPED url that fails is re-asked: someone correcting a typo is in the loop
        d = stage(work / "typed", False)
        spec = importlib.util.spec_from_file_location("setup_typed", d / "tinycmdr.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        answers = ["http://127.0.0.1:9/v1", "http://127.0.0.1:9/v2", "http://127.0.0.1:9/v3",
                   "", "", "", "", ""]               # model, key, Mattermost, Telegram, egress
        old_in = sys.stdin
        sys.stdin = FakeTTY("\n".join(answers) + "\n")
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                rc = mod.run_setup()
        finally:
            sys.stdin = old_in
        out = buf.getvalue()
        written = json.loads((d / "config.json").read_text(encoding="utf-8"))
        check(out.count("\u2717 no answer from") == 3,
              "a typed URL is checked every time it is typed", out[-800:])
        check("keeping it anyway" in out and written["llm"]["base_url"] == "http://127.0.0.1:9/v3",
              "...and after three tries it keeps the last one and says how to fix it",
              (written["llm"]["base_url"], out[-400:]))

        # an endpoint that DOES answer reports what it advertised, and its ids become the
        # default model (the numbered list is the picker's shell form)
        d = stage(work / "live", False)
        spec = importlib.util.spec_from_file_location("setup_live", d / "tinycmdr.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        mod._probe_model_ids = lambda url, key=None: ["qwen3-14b", "glm-4.6"]
        answers = ["http://127.0.0.1:8081/v1", "2", "", "", "", "", ""]
        old_in = sys.stdin
        sys.stdin = FakeTTY("\n".join(answers) + "\n")
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                rc = mod.run_setup()
        finally:
            sys.stdin = old_in
        out = buf.getvalue()
        written = json.loads((d / "config.json").read_text(encoding="utf-8"))
        check("\u2713 reachable" in out and "qwen3-14b" in out and "glm-4.6" in out,
              "a reachable endpoint says so, and lists what it advertises", out[:600])
        check(written["llm"]["model"] == "2",
              "...and the wizard keeps the typed model id when no picker can run here",
              written["llm"]["model"])
    finally:
        shutil.rmtree(work, ignore_errors=True)

    print(f"\n{len(PASSES)} passed, {len(FAILS)} failed")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())

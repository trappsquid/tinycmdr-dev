"""test_safety_surface - one merged suite (test_disclosure, test_scrub).

They grade the same surface and ran as separate gate processes before; each
member keeps its body, its check() and its summary, and the runner below
restores the environment, the cwd and sys.path between members. Zero checks
were deleted: no member needed a namespace rewrite.
"""
import os
import sys


def _run(name, fn):
    """One member, its own snapshot: env, cwd and sys.path restored afterwards."""
    saved_env = dict(os.environ)
    saved_cwd = os.getcwd()
    saved_path = list(sys.path)
    print("== member %s: start" % name)
    try:
        rc = fn()
    except SystemExit as exc:
        rc = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    finally:
        os.environ.clear()
        os.environ.update(saved_env)
        try:
            os.chdir(saved_cwd)
        except OSError:
            pass
        sys.path[:] = saved_path
    rc = int(rc or 0)
    print("== member %s: exit %d" % (name, rc))
    return rc


def _suite_test_disclosure():
    """Offline checks for tool disclosure.

The claim being tested is narrow and important: the payload carries fewer schemas, and
NOTHING becomes unreachable. So the checks come in pairs — a hidden tool must be absent
from the schema list, and calling it anyway must work and stick.

The last section is end-to-end with no server: it stubs the POST, runs a real turn, and
looks at what the request body actually carried.

    python tests/test_safety_surface.py
"""
    import json
    import shutil
    import sys
    import tempfile
    from pathlib import Path

    BASE = Path(__file__).resolve().parent.parent
    TESTS = BASE / "tests"
    sys.path.insert(0, str(TESTS))

    import run_scenario  # noqa: E402

    FAILS = []


    def check(cond, what):
        if not cond:
            FAILS.append(what)
            print(f"FAIL {what}")
        else:
            print(f"ok   {what}")


    def names(schemas):
        return sorted(s["function"]["name"] for s in schemas)


    class FakeResp:
        def __init__(self, data):
            self._data = data
            self.status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return self._data


    def install_stub_post(fb, seen):
        """Answer every model call with a fixed reply and record the request bodies."""

        def fake_post(url, headers, payload, timeout, grace, cancel_event=None,
                      stream=False):
            seen.append(json.loads(json.dumps(payload)))
            return FakeResp({"choices": [{"message": {"role": "assistant",
                                                      "content": "understood"},
                                          "finish_reason": "stop"}],
                             "usage": {"prompt_tokens": 11, "completion_tokens": 2}})

        fb._post_watchdog = fake_post


    def main():
        workdir = Path(tempfile.mkdtemp(prefix="fbdisclose-"))
        try:
            run_scenario.stage_install(workdir, 24000)
            fb = run_scenario.load(workdir)

            # The example tool has to exist in the build under test, so every check below
            # uses EX rather than assuming the scheduler is there.
            EX = "schedule" if fb.REGISTRY.get("schedule") else "delegate_task"
            EX_QUERY = {"schedule": "schedule a job every morning",
                        "delegate_task": "delegate a task"}[EX]
            EX_NEAR = EX + "s"          # one letter off: the near-miss suggestion path

            everything = names(fb.REGISTRY.openai_schemas())
            check(fb.disclosure_on() is True, "disclosure is on by default")
            visible = names(fb.select_tool_schemas("s1"))
            check("find_tools" in visible, "the discovery tool is always visible")
            check(len(visible) < len(everything),
                  f"the payload carries fewer tools ({len(visible)} of {len(everything)})")
            hidden = fb.hidden_tools("s1")
            check(EX in hidden and len(hidden) >= 3,
                  f"rarely used tools are hidden: {hidden}")
            check("shell" in visible and "read_file" in visible and "write_file" in visible,
                  "the five primitives stay visible")
            check(not set(visible) & set(hidden), "visible and hidden do not overlap")

            vis_tokens = fb.est_tokens(json.dumps(fb.select_tool_schemas("s1")))
            all_tokens = fb.est_tokens(json.dumps(fb.REGISTRY.openai_schemas()))
            check(vis_tokens < all_tokens,
                  f"the visible schemas cost less ({vis_tokens} < {all_tokens} tokens)")

            # ---- the always-on payload has a budget, and the budget is gated ----
            # A convention that is not gated does not hold. These schemas ride on EVERY
            # call, so they are paid for before the first tool call of every run, and the
            # prose had crept to 13,898 chars across the registry with nobody watching
            # . The numbers below were measured the same day against
            # this harness: 7,133 chars over 13 always-visible tools, fattest single
            # schema ask_user at 1,078. It is a ceiling, not a target: when it fires, cut
            # prose or drop a tool - raising the number is a decision, not a fix.
            # RAISED 2026-09-21, on the record rather than quietly: the experiment ledger
            # tool is always-on BY DESIGN (a run has to know what this box already tested
            # BEFORE it runs an arm), and adding it moved the block from 7,133 over 13 tools
            # to 8,392 over 14 - experiment itself 1,170 chars, ask_user 1,165 after its
            # description was corrected to say an unanswered question STOPS the run. The new
            # ceiling is that measurement plus ~6% headroom, not room to grow.
            SCHEMA_BUDGET = 8900          # chars, measured 8,392 + ~6% headroom
            TOOL_SCHEMA_CAP = 1200        # chars for one tool, fattest measured 1,170
            always_on = fb.select_tool_schemas(None)
            block = json.dumps(always_on)
            check(len(block) <= SCHEMA_BUDGET,
                  f"the always-on schema block is inside its budget "
                  f"({len(block)} of {SCHEMA_BUDGET} chars, {fb.est_tokens(block)} tokens)")
            sizes = sorted(((len(json.dumps(s)), s["function"]["name"]) for s in always_on),
                           reverse=True)
            check(sizes[0][0] <= TOOL_SCHEMA_CAP,
                  f"no single tool schema exceeds {TOOL_SCHEMA_CAP} chars "
                  f"(fattest {sizes[0][1]} {sizes[0][0]})")

            # ---- asking for a tool reveals it ----------------------------------
            out = fb.tool_find_tools({"query": EX_QUERY}, {"session_key": "s1"})
            check(EX in out and "args:" in out,
                  "find_tools returns the matched tool WITH its arguments")
            check("now callable" in out, "find_tools says the tool is now callable")
            check(EX in names(fb.select_tool_schemas("s1")),
                  "the revealed tool is in this session's payload")

            # a different session is unaffected
            check(EX not in names(fb.select_tool_schemas("s2")),
                  "another session does not inherit the reveal")

            # ---- no query lists what exists, without revealing ------------------
            out = fb.tool_find_tools({}, {"session_key": "s3"})
            check(EX in out, "an empty query lists the hidden tools")
            check(EX not in names(fb.select_tool_schemas("s3")),
                  "listing them does not reveal them")

            # ---- all=true reveals everything ------------------------------------
            out = fb.tool_find_tools({"all": True}, {"session_key": "s4"})
            check(fb.hidden_tools("s4") == [], "all=true leaves nothing hidden")
            check(len(fb.select_tool_schemas("s4")) == len(everything),
                  "an all=true session carries the full registry again")

            # ---- a reset pays the rent again ------------------------------------
            # A reveal is per-SESSION rent, so a cleared conversation must not keep it.
            # Measured 2026-09-25 on Windows: find_tools{all:true} took the
            # payload from 14 schemas to 30, and every later turn - THROUGH /new, which says
            # "Session cleared. Fresh context." - carried ~3.4K extra prompt tokens (step-0
            # prompt_tok 6,505 -> 10,086 on identical orders). Falsified against the pre-fix
            # build: without the reset pop this fails with 30 schemas still on the wire.
            fb.AGENT.reset("s4")
            check(fb.hidden_tools("s4") != [],
                  "a reset session hides the rare tools again")
            check(len(fb.select_tool_schemas("s4")) == len(visible),
                  "a reset session pays the reveal rent again")

            # ---- a bad query is honest ------------------------------------------
            out = fb.tool_find_tools({"query": "zzzznothing"}, {"session_key": "s5"})
            check("No tool matched" in out and "all=true" in out,
                  "an unmatched query says so and points at all=true")

            # ---- calling a hidden tool works, and sticks ------------------------
            ctx = {"session_key": "s6"}
            name, args, out = fb.Agent._exec_tool(
                fb.AGENT, {"function": {"name": "list_tools", "arguments": {}}}, ctx)
            check(not out.startswith("ERROR"), f"a hidden tool still runs ({out[:60]!r})")
            check("was not in your tool list" in out,
                  "the result says the tool was revealed")
            check("list_tools" in names(fb.select_tool_schemas("s6")),
                  "and it is in the payload from then on")

            # a near-miss name now suggests the real one
            _, _, out = fb.Agent._exec_tool(
                fb.AGENT, {"function": {"name": EX_NEAR, "arguments": {}}}, ctx)
            check("unknown tool" in out and "find_tools" in out,
                  f"an unknown tool points at find_tools ({out[:80]!r})")
            check(EX in out, f"a near-miss name suggests the real tool ({out[:110]!r})")

            # An ABSENT tool must not read like a hidden one. A dropped-in runbook written
            # for another harness names tools no build here has, and the old hint answered
            # every unknown with "find_tools can reveal them" - which is what sent the model
            # looking for a tool that was never on the box.
            # FIXED 2026-09-28, two bugs in three lines. (a) The three checks below passed
            # their arguments to check() in the wrong order - this file's helper is
            # check(cond, what), and these called check(label, cond), so the "condition" was
            # always the non-empty label string and the result was always `ok`. They printed
            # `ok True` three times and graded nothing. (b) The fixture name was
            # `computer_use`, which a drop-in tool on this box now really answers to, so the
            # call reached that tool instead of the unknown-tool path. The fixture is a name
            # no build here has; `browser_navigate` is the browser tool from the harness
            # whose runbooks this test is about.
            _, _, out = fb.Agent._exec_tool(
                fb.AGENT, {"function": {"name": "browser_navigate", "arguments": {}}}, ctx)
            check("unknown tool" in out and "exists on this box" in out,
                  f"an absent tool says it is absent ({out[:110]!r})")
            check("find_tools" not in out,
                  "and does not send the model hunting for it")
            check("create_tool" in out,
                  "and names the way to have that capability here")
            out = fb.tool_find_tools({"query": "send it to a subagent"}, {"session_key": "s9"})
            check("delegate" in out, "plain language finds the delegation tool")

            # ---- the off switch is a true rollback ------------------------------
            fb.CONFIG["agent"]["tool_disclosure"] = False
            check(names(fb.select_tool_schemas("s7")) == everything,
                  "tool_disclosure=false sends the whole registry")
            check(fb.hidden_tools("s7") == [], "and hides nothing")
            out = fb.tool_find_tools({}, {"session_key": "s7"})
            check("already in your list" in out,
                  "find_tools says everything is already visible")
            fb.CONFIG["agent"]["tool_disclosure"] = True

            # ---- core_tools overrides the visible set ---------------------------
            fb.CONFIG["agent"]["core_tools"] = ["shell", "find_tools"]
            vis = names(fb.select_tool_schemas("s8"))
            check(vis == ["find_tools", "shell"], f"core_tools is honoured exactly ({vis})")
            fb.CONFIG["agent"]["core_tools"] = []

            # ---- end to end: what the request body really carried ---------------
            seen = []
            install_stub_post(fb, seen)
            answer = fb.AGENT.run("disc-e2e", "say something short")
            check(bool(answer), f"a stubbed turn answers ({answer[:40]!r})")
            check(bool(seen), "the model was actually called")
            sent = names(seen[0].get("tools") or [])
            check(sent == names(fb.select_tool_schemas("disc-e2e")),
                  f"the request carried exactly the visible set ({len(sent)} tools)")
            check(EX not in sent, "a hidden tool is absent from the request body")

            # reveal one, run again in the same session, and it is there
            fb.reveal_tools("disc-e2e", [EX])
            seen.clear()
            fb.AGENT.run("disc-e2e", "and again")
            sent2 = names(seen[0].get("tools") or [])
            check(EX in sent2, "the revealed tool rides in the next request")

            # ---- the banner and /status report what the REQUEST carries ---------
            # (review, 2026-09-22: it counted REGISTRY.openai_schemas(), so a real
            # install read "34 tool schemas" while its requests carried 14 - the
            # number a reader checks the ~4k-token claim against was the wrong one.)
            # The banner now shows three rows and folds the arithmetic into /status
            # Both read envelope_facts(), so they cannot disagree.
            import contextlib
            import io
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                fb.cli_banner()
                fb._cli_command("/status")
            banner = buf.getvalue()
            check(sum(1 for l in banner.splitlines() if l.startswith(("\u2503", "\u2502", "|"))) <= 5,
                  "the banner fits in four rows of content")
            lines = [l for l in banner.splitlines() if "prompt overhead" in l]
            check(bool(lines), "the status output carries the overhead line the banner folded")
            if lines:
                text = lines[0]
                visible = fb.select_tool_schemas(None)
                registry_n = len(fb.REGISTRY.openai_schemas())
                hidden_n = registry_n - len(visible)
                static = fb.est_tokens(fb.build_system_prompt() + json.dumps(visible))
                check("%d tool schemas" % len(visible) in text,
                      f"the banner counts the VISIBLE schemas ({len(visible)}), not the "
                      f"registry's {registry_n}")
                check(fb.fmt_tokens(static) in text,
                      f"the static number is what the wire carries ({fb.fmt_tokens(static)})")
                check(not hidden_n or ("%d hidden" % hidden_n) in text,
                      "hidden tools are named separately instead of being added in")

            # ---- /status answers the two questions only the OTHER surfaces did ---
            # A parked ask_user question and the lane state lived in status_text (chat)
            # and _verb_status (a shell); `/status` in a session answered neither
            # (A-2026-10-06-278).
            _saved_lanes = fb.lanes_snapshot
            _sk278 = fb._cli_key()
            fb._ASK_PENDING[_sk278] = {"question": "Which port should the job use?",
                                       "options": [], "opened": 0, "ev": None}
            fb.lanes_snapshot = lambda: {"mattermost": {"state": "ok"}}
            try:
                buf2 = io.StringIO()
                with contextlib.redirect_stdout(buf2):
                    fb._cli_command("/status")
                st2 = buf2.getvalue()
            finally:
                fb.lanes_snapshot = _saved_lanes
                fb._ASK_PENDING.pop(_sk278, None)
            check("WAITING on: Which port should the job use?" in st2,
                  "the session /status names the parked question it waits on -- "
                  + st2[-300:])
            check("mattermost=ok" in st2,
                  "...and the lane state, so 'is it up' has an answer here too -- "
                  + st2[-300:])
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

        print()
        if FAILS:
            print(f"{len(FAILS)} check(s) failed")
            sys.exit(1)
        print("all tool-disclosure checks passed")
    return main()


def _suite_test_scrub():
    """Secrets are scrubbed (2026-09-22).

The review found the secret sweep covering environment variables only; the real hole was
that it never scrubbed the primary llm.api_key, which is the key a hosted endpoint keeps in
config.json and the one in use on every call. This suite grades the sweep itself, on the
code's own terms:

    python tests/test_safety_surface.py
"""
    import importlib.util
    import json
    import logging
    import os
    import sys
    from pathlib import Path

    BASE = Path(__file__).resolve().parent.parent
    SRC = BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")
    spec = importlib.util.spec_from_file_location("tinycmdr_scrub_under_test", SRC)
    fb = importlib.util.module_from_spec(spec)
    sys.modules["tinycmdr_scrub_under_test"] = fb
    spec.loader.exec_module(fb)

    PASSES = []
    FAILS = []


    def check(name, cond, detail=""):
        (PASSES if cond else FAILS).append(name)
        print(("ok   " if cond else "FAIL ") + name + ("" if cond else "   " + str(detail)))


    KEY = "sk-live-0123456789abcdef"
    FBKEY = "sk-backup-fedcba9876543210"
    MM = "mm-bot-token-abcdefghijklmnop"
    SAVED_SECRETS = fb._SECRETS
    SAVED_CFG = fb.CONFIG
    try:
        fb.CONFIG["llm"] = dict(fb.CONFIG["llm"])
        fb.CONFIG["llm"]["api_key"] = KEY
        fb.CONFIG["llm"]["fallbacks"] = [{"base_url": "https://example.invalid/v1",
                                          "model": "x", "api_key": FBKEY}]
        fb.CONFIG["mattermost"] = dict(fb.CONFIG["mattermost"])
        fb.CONFIG["mattermost"]["token"] = MM
        os.environ["TINYCMDR_ENV_TOKEN"] = "env-token-abcdefghijkl"
        fb._SECRETS = fb._secret_values()

        out = fb.scrub("the endpoint answered with key " + KEY)
        check("the PRIMARY llm.api_key is redacted", KEY not in out and "«redacted»" in out, out)
        # This build may have no fallback endpoints at all; ask the source rather than the
        # config, which the suite has just written.
        if 'vals.add(fb["api_key"])' in SRC.read_text(encoding="utf-8", errors="replace"):
            out = fb.scrub("failover used " + FBKEY)
            check("a fallback api_key is still redacted", FBKEY not in out, out)
            fb.CONFIG["llm"]["fallbacks"][0]["api_key"] = "fbshort1"
            fb._SECRETS = fb._secret_values()
            out = fb.scrub("failover used fbshort1")
            check("an 8-char fallback api_key is redacted too", "fbshort1" not in out, out)
        else:
            print("skip a fallback key check (this build has no fallback endpoints)")
        out = fb.scrub("token " + MM)
        check("the chat token is still redacted", MM not in out, out)
        out = fb.scrub("env token env-token-abcdefghijkl")
        check("an environment secret is redacted", "env-token-abcdefghijkl" not in out, out)

        # A name ending in PASSWORD/PASSWD is a credential at ANY length (floor 6, the rule
        # the config-side sweep already uses). The 12-char floor skipped this install's
        # 10-char SUDO_PASSWORD - a secret missing from _SECRETS is one that reaches the
        # transcript, the log and the chat.
        os.environ["TINYCMDR_TEST_SUDO_PASSWORD"] = "pwabcd1234"
        os.environ["TINYCMDR_TEST_TOO_SHORT_PASSWD"] = "abc"
        fb._SECRETS = fb._secret_values()
        out = fb.scrub("sudo said pwabcd1234 and meant it")
        check("a 10-char *_PASSWORD environment value is redacted",
              "pwabcd1234" not in out, out)
        check("and a 3-char one is still below the floor", "abc" not in fb._SECRETS)

        # One floor for all three arms (2026-10-10): the env and llm.api_key arms kept
        # 12 while the config arm accepted 6, so a hand-set 10-char page token or an
        # 8-char endpoint key reached the transcript, the log and the chat unmasked.
        os.environ["TINYCMDR_TEST_TOKEN"] = "tok1234567"
        os.environ["TINYCMDR_TEST_TOO_SHORT_TOKEN"] = "abcde"
        fb.CONFIG["llm"]["api_key"] = "k8ch8ar1"
        fb._SECRETS = fb._secret_values()
        out = fb.scrub("the page token is tok1234567 here")
        check("a 10-char *_TOKEN environment value is redacted",
              "tok1234567" not in out, out)
        out = fb.scrub("the endpoint key k8ch8ar1 is in use")
        check("an 8-char llm.api_key is redacted", "k8ch8ar1" not in out, out)
        check("and a 5-char value is still below the floor", "abcde" not in fb._SECRETS)
        # Put the long key back: later checks in this member assert on it.
        fb.CONFIG["llm"]["api_key"] = KEY
        fb._SECRETS = fb._secret_values()

        # the regression the sweep's own comment records: a looser name test swept PATH out
        # of ordinary log lines, so a path line must come through untouched
        probe = "PATH entry C:\\Windows\\System32 and C:\\Program Files\\Python312"
        check("an ordinary path line is left alone", fb.scrub(probe) == probe,
              fb.scrub(probe))
        check("a short value is not treated as a secret",
              fb.scrub("code 1234") == "code 1234")

        logged = fb.scrub("wrote " + KEY + " to the log")
        check("what the log filter would carry is masked too", KEY not in logged, logged)
    finally:
        fb._SECRETS = SAVED_SECRETS
        fb.CONFIG = SAVED_CFG
        os.environ.pop("TINYCMDR_ENV_TOKEN", None)
        os.environ.pop("TINYCMDR_TEST_SUDO_PASSWORD", None)
        os.environ.pop("TINYCMDR_TEST_TOO_SHORT_PASSWD", None)
        os.environ.pop("TINYCMDR_TEST_TOKEN", None)
        os.environ.pop("TINYCMDR_TEST_TOO_SHORT_TOKEN", None)

    # ---- a key added after import is swept too -----------------------------
    # _SECRETS was frozen at import, so `config set llm.api_key` (the guard skips the llm
    # section, since llm keys live in config.json) took effect while the sweep still held
    # the import-time set - the new key reached the transcript, the log and the chat
    # (2026-09-29). Drive the real verb against a temp config.json.
    import contextlib
    import io
    import shutil
    import tempfile

    NEWKEY = "sk-added-after-start-9876543210"
    _saved_path = fb.CONFIG_PATH
    _saved_cfg = dict(fb.CONFIG)
    _saved_secrets = fb._SECRETS
    _saved_source = dict(fb.CONFIG_SOURCE)
    _saved_running = fb._verb_running
    _stage = Path(tempfile.mkdtemp(prefix="tinycmdr-scrub-"))
    try:
        fb.CONFIG_PATH = _stage / "config.json"
        fb.CONFIG_PATH.write_text("{}", encoding="utf-8")
        fb._verb_running = lambda: False        # the verb only prints its restart note
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rc = fb._verb_config(["set", "llm.api_key", NEWKEY])
        check("config set llm.api_key takes effect through the real verb", rc == 0, rc)
        out = fb.scrub("the new endpoint key is " + NEWKEY)
        check("...and the new key is masked without re-binding _SECRETS by hand",
              NEWKEY not in out and "«redacted»" in out, out)
    finally:
        fb.CONFIG_PATH = _saved_path
        fb._verb_running = _saved_running
        fb._SECRETS = _saved_secrets
        fb.CONFIG = _saved_cfg
        fb.CONFIG_SOURCE.update(_saved_source)
        shutil.rmtree(_stage, ignore_errors=True)

    # ---- a 401 body that echoes the key ---------------------------
    # Measured: a provider that echoes the request's Authorization header in its error body
    # put the live key into the fatal notes, the run's return value, the log and the chat.
    # (Earlier checks in this suite restore _SECRETS to the import-time set, which does not
    # hold KEY, so seed it here - the sweep is what scrub() masks, and that is the point.)
    _prev_secrets = fb._SECRETS
    fb._SECRETS = set(fb._SECRETS) | {KEY}


    class _Echo401:
        class response:
            status_code = 401
            text = ('{"error": {"message": "invalid api key: Bearer %s"}}' % KEY)


    def _echo_exc():
        e = Exception("401")
        e.response = _Echo401.response
        return e


    try:
        _body = fb._http_body(_echo_exc())
        check("an error body is scrubbed at the boundary", KEY not in _body, _body)
        check("and still says what the endpoint said", "invalid api key" in _body, _body)

        _usage = {}
        fb._record_attempt(_usage, "https://example.invalid/v1", "fatal",
                           "401: " + _Echo401.response.text, 0.1)
        check("usage['attempts'] carries no key", KEY not in json.dumps(_usage), _usage)
        check("the attempt is still reported",
              "401" in _usage["attempts"][0]["detail"], _usage)

        _seen = []
        _handler = logging.Handler()
        _handler.emit = lambda r: _seen.append(r.getMessage())
        fb.log.addHandler(_handler)
        try:
            import requests as _rq
            _real_post = fb._post_watchdog

            def _echo_post(url, headers, payload, timeout, grace, cancel_event=None,
                           stream=False):
                raise _rq.HTTPError("401", response=_Echo401.response)

            fb._post_watchdog = _echo_post
            fb.AGENT._window_cache = 32768
            fb.AGENT._window_at = 0.0
            fb.AGENT._envelope_cache = None
            _msg = ""
            try:
                fb.AGENT._chat([{"role": "system", "content": "s"},
                                {"role": "user", "content": "hi"}],
                               session_key="scrub401")
            except fb.InfraError as e:
                _msg = str(e)
            except Exception as e:                                # noqa: BLE001
                _msg = "%s: %s" % (type(e).__name__, e)
            finally:
                fb._post_watchdog = _real_post
        finally:
            fb.log.removeHandler(_handler)
        check("the run's failure message carries no key", KEY not in _msg, _msg)
        check("and it names the credential problem", "rejected the request" in _msg, _msg)
        check("the log carries no key", all(KEY not in m for m in _seen),
              [m for m in _seen if KEY in m])
    finally:
        fb._SECRETS = _prev_secrets

    # ---- the config side and the env side share ONE secret vocabulary (A-110) --------------
    # The env rule ended in token|key|pat|password|passwd|secret|credential; the config-side
    # rule (_secret_config_path, which feeds BOTH the `config` verb's refusal and the verb-log
    # scrub) named only token/api_key, so `config set db.password X` was not treated as a
    # secret. The llm.* exemption and the *_api_key arm are asserted here so the widening
    # cannot quietly drop them.
    check("the config secret rule mirrors the env vocabulary (and llm.* stays exempt)",
          all(fb._secret_config_path(p) for p in
              ("db.password", "db.passwd", "db.credential", "mattermost.password"))
          and not fb._secret_config_path("llm.api_key")
          and fb._secret_config_path("mattermost.token")
          and fb._secret_config_path("search.anysearch_api_key"),
          {p: fb._secret_config_path(p) for p in
           ("db.password", "db.passwd", "db.credential", "mattermost.password",
            "llm.api_key", "mattermost.token", "search.anysearch_api_key")})
    _plog = fb._verb_log_args("config", ["set", "db.password", "hunter2"])
    check("a password-named config key's VALUE never reaches the verb log",
          "hunter2" not in _plog and "<redacted>" in _plog, _plog)

    # ---- a percent-encoded secret in a URL is still the secret (A-111) ---------------------
    # scrub was exact-match only: the same key in a query string (`%40`, `%2F`, `+` for a
    # space) went through untouched.
    from urllib.parse import quote, quote_plus                                   # noqa: E402
    _enc_secret = "p@ss/word:1234 secretvalue"
    _prev_enc = fb._SECRETS
    try:
        fb._SECRETS = set(fb._SECRETS) | {_enc_secret}
        _pct = "https://x.invalid/?k=" + quote(_enc_secret, safe="")
        _form = "https://x.invalid/?k=" + quote_plus(_enc_secret)
        check("a percent-encoded secret in a URL is redacted (quote(safe=''))",
              _enc_secret not in _pct and fb.scrub(_pct) == "https://x.invalid/?k=«redacted»",
              fb.scrub(_pct))
        check("...and the form-urlencoded shape (+ for a space) too",
              _enc_secret not in _form and fb.scrub(_form) == "https://x.invalid/?k=«redacted»",
              fb.scrub(_form))
    finally:
        fb._SECRETS = _prev_enc

    print()
    print("%d passed, %d failed" % (len(PASSES), len(FAILS)))
    sys.exit(1 if FAILS else 0)


def main():
    rc = 0
    for name, fn in (("test_disclosure", _suite_test_disclosure), ("test_scrub", _suite_test_scrub)):
        rc |= _run(name, fn)
    return 1 if rc else 0


if __name__ == "__main__":
    sys.exit(main())

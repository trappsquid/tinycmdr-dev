"""test_envelope - one merged suite (test_payload_ids, test_prefix_stability, test_proxy_tail, test_envelope).

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


def _suite_test_payload_ids():
    """Duplicate tool_call_ids: repaired deterministically, reported honestly.

A local server (or a proxy) that re-emits a call id used to collapse two calls onto one
id in every pairing structure here, so the payload shipped two tool_calls with one id and
one result. llama.cpp ignores it; a strict endpoint answers 400 - one failover away by
design; ids are split in order and the results re-pointed.

    python tests/test_envelope.py
"""
    import importlib.util
    import os
    import shutil
    import sys
    import tempfile
    from pathlib import Path

    BASE = Path(__file__).resolve().parent.parent
    SRC = BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")

    STAGE = Path(tempfile.gettempdir()) / "tinycmdr-test-stage-payload-ids"
    if STAGE.exists():
        shutil.rmtree(STAGE, ignore_errors=True)
    STAGE.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SRC, STAGE / "tinycmdr.py")
    shutil.copy2(Path(__file__).resolve().parent / "fixture-config.json",
                 STAGE / "config.json")
    spec = importlib.util.spec_from_file_location("tinycmdr_payload_ids",
                                                  STAGE / "tinycmdr.py")
    fb = importlib.util.module_from_spec(spec)
    sys.modules["tinycmdr_payload_ids"] = fb
    spec.loader.exec_module(fb)

    FAILURES = []


    def check(name, cond, detail=""):
        if cond:
            print(f"ok   {name}")
        else:
            FAILURES.append(name)
            print(f"FAIL {name}: {detail}")


    def _call(tid, name):
        return {"id": tid, "type": "function",
                "function": {"name": name, "arguments": "{}"}}


    def main():
        dup = [
            {"role": "assistant", "content": "",
             "tool_calls": [_call("x", "shell"), _call("x", "shell")]},
            {"role": "tool", "tool_call_id": "x", "content": "first result"},
            {"role": "tool", "tool_call_id": "x", "content": "second result"},
        ]

        check("duplicates are reported before repair",
              any("duplicate tool_call_id" in p for p in fb._tool_pairing_problems(dup)),
              fb._tool_pairing_problems(dup))

        out = fb._uniquify_tool_call_ids(dup)
        ids = [tc["id"] for tc in out[0]["tool_calls"]]
        check("the duplicate call gets its own id", ids == ["x", "x_dup1"], ids)
        check("the results are re-pointed in order",
              [m["tool_call_id"] for m in out[1:]] == ["x", "x_dup1"],
              [m["tool_call_id"] for m in out[1:]])
        check("...and the caller's messages are not mutated in place",
              dup[0]["tool_calls"][1]["id"] == "x"
              and dup[2]["tool_call_id"] == "x", dup)

        more = [
            {"role": "assistant", "content": "",
             "tool_calls": [_call("x", "shell"), _call("x", "shell")]},
            {"role": "tool", "tool_call_id": "x", "content": "a"},
            {"role": "tool", "tool_call_id": "x", "content": "b"},
            {"role": "tool", "tool_call_id": "x", "content": "c"},
        ]
        out = fb._uniquify_tool_call_ids(more) # used to raise IndexError
        check("more results than calls: the extras pair with the last id, no IndexError",
              [m["tool_call_id"] for m in out[1:]] == ["x", "x_dup1", "x_dup1"],
              [m["tool_call_id"] for m in out[1:]])

        repaired = fb._repair_tool_pairing(dup)
        check("the repaired payload has no pairing problems",
              fb._tool_pairing_problems(repaired) == [],
              fb._tool_pairing_problems(repaired))
        check("the repaired payload keeps every call and result",
              len(repaired) == 3
              and [tc["id"] for tc in repaired[0]["tool_calls"]] == ["x", "x_dup1"],
              repaired)

        # ---- a result that arrived AFTER a later block is still that call's answer -----------
        # The repair used to invent "no result was recorded for this call; it did not complete"
        # and leave the real output orphaned at the end - a lie the model acts on (re-run the
        # command, possibly a mutating one) plus the strict-provider 400 the function exists to
        # prevent (run 25, A-2026-10-07-78). Before inventing a placeholder it now looks for the
        # result anywhere later in the history and lifts it to where the provider needs it.
        late = [
            {"role": "assistant", "content": "", "tool_calls": [_call("c1", "shell")]},
            {"role": "assistant", "content": "", "tool_calls": [_call("c2", "shell")]},
            {"role": "tool", "tool_call_id": "c2", "content": "real-c2"},
            {"role": "tool", "tool_call_id": "c1", "content": "REAL-C1 the actual output"},
        ]
        fixed = fb._repair_tool_pairing([dict(m) for m in late])
        check("a late real result is attached to its own call, not orphaned",
              fb._tool_pairing_problems(fixed) == [], fb._tool_pairing_problems(fixed))
        check("...and no placeholder is invented for it",
              not any("no result was recorded" in str(m.get("content")) for m in fixed),
              [m.get("content") for m in fixed])
        check("...with the real output still present, once",
              sum(1 for m in fixed if "REAL-C1" in str(m.get("content"))) == 1,
              [m.get("tool_call_id") for m in fixed])
        check("...and the result follows the call that owns it",
              [m.get("tool_call_id") for m in fixed if m.get("role") == "tool"]
              == ["c1", "c2"], [m.get("tool_call_id") for m in fixed])
        # A call with no result ANYWHERE still gets the placeholder: the lift must not turn a
        # genuinely missing answer into a silently unanswered call.
        gone = [
            {"role": "assistant", "content": "", "tool_calls": [_call("c1", "shell")]},
            {"role": "assistant", "content": "", "tool_calls": [_call("c2", "shell")]},
            {"role": "tool", "tool_call_id": "c1", "content": "real-c1"},
        ]
        kept = fb._repair_tool_pairing([dict(m) for m in gone])
        check("a call with no result anywhere still gets the placeholder",
              fb._tool_pairing_problems(kept) == []
              and any("no result was recorded" in str(m.get("content")) for m in kept),
              fb._tool_pairing_problems(kept))

        # A healthy payload is returned object-identical: the pass must cost nothing.
        clean = [
            {"role": "assistant", "content": "",
             "tool_calls": [_call("a", "shell"), _call("b", "shell")]},
            {"role": "tool", "tool_call_id": "a", "content": "ra"},
            {"role": "tool", "tool_call_id": "b", "content": "rb"},
        ]
        check("a clean payload is untouched", fb._uniquify_tool_call_ids(clean) is clean)

        # The same id reused in a LATER assistant message is also split.
        later = [
            {"role": "assistant", "content": "", "tool_calls": [_call("k", "shell")]},
            {"role": "tool", "tool_call_id": "k", "content": "r1"},
            {"role": "assistant", "content": "again", "tool_calls": [_call("k", "shell")]},
            {"role": "tool", "tool_call_id": "k", "content": "r2"},
        ]
        out = fb._uniquify_tool_call_ids(later)
        check("an id reused across turns is split too",
              out[2]["tool_calls"][0]["id"] == "k_dup1"
              and out[3]["tool_call_id"] == "k_dup1", out)

        # ---- a stray non-dict entry must not raise out of a repair pass (A-102) ----------
        # These passes run on harness-assembled payloads, where a None (or a number) can appear.
        # Each used to AttributeError on the first such entry and take the request with it.
        try:
            problems = fb._tool_pairing_problems([None, 7, {"role": "user", "content": "hi"}])
            raised = None
        except Exception as e:                                     # noqa: BLE001 - the point
            problems, raised = None, e
        check("_tool_pairing_problems skips a non-dict entry instead of raising",
              raised is None and problems == [], (raised, problems))

        dirty = [None, 7, {"role": "user", "content": "hi"}]
        try:
            out = fb._uniquify_tool_call_ids(dirty)
            raised = None
        except Exception as e:                                     # noqa: BLE001 - the point
            out, raised = None, e
        check("_uniquify_tool_call_ids passes a non-dict entry through instead of raising",
              raised is None and out == dirty, (raised, out))

        mixed = [None,
                 {"role": "assistant", "content": "",
                  "tool_calls": [_call("x", "shell"), _call("x", "shell")]},
                 {"role": "tool", "tool_call_id": "x", "content": "r1"},
                 {"role": "tool", "tool_call_id": "x", "content": "r2"}]
        try:
            repaired = fb._repair_tool_pairing(mixed)
            raised = None
        except Exception as e:                                     # noqa: BLE001 - the point
            repaired, raised = None, e
        check("_repair_tool_pairing skips a non-dict entry without spinning or raising",
              raised is None and repaired is not None and repaired[0] is None
              and fb._tool_pairing_problems(repaired) == [], (raised, repaired))

        print()
        if FAILURES:
            print("%d check(s) failed" % len(FAILURES))
            return 1
        print("all payload-id checks passed")
        return 0
    return main()


def _suite_test_prefix_stability():
    """Prefix stability and tool disclosure: the two things that already work.

Prefix reuse is why a small local model is usable at all: the system prompt and the
conversation so far stay byte-identical between turns, so the server's KV cache only
prefills the new tail, and the volatile state block is the LAST thing in the payload.
Tool disclosure is the other half of the premise: 80 tools may exist and one index
line names them, but their schemas must not ride every request.

Both are asserted here rather than described in a comment, because both are easy to
break with an innocent-looking edit.
Driven by a stub endpoint in this process - no network, no real model, no mock file.

    python tests/test_envelope.py
"""
    import importlib.util
    import json
    import shutil
    import sys
    import tempfile
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from pathlib import Path

    BASE = Path(__file__).resolve().parent.parent
    FAILS = []


    def check(cond, what):
        if not cond:
            FAILS.append(what)
            print(f"FAIL {what}")
        else:
            print(f"ok   {what}")


    class Stub(BaseHTTPRequestHandler):
        payloads = []

        def log_message(self, *a):
            pass

        def _send(self, obj, code=200):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.rstrip("/")
            if path.endswith("/models"):
                self._send({"data": [{"id": "main", "object": "model",
                                      "max_model_len": 32768}]})
            elif path.endswith("/props"):
                self._send({"default_generation_settings": {"n_ctx": 32768}})
            else:
                self._send({"error": "not found"}, 404)

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            type(self).payloads.append(body)
            self._send({"id": "c1", "object": "chat.completion",
                        "model": body.get("model"),
                        "choices": [{"index": 0, "finish_reason": "stop",
                                     "message": {"role": "assistant",
                                                 "content": "turn done"}}],
                        "usage": {"prompt_tokens": 10, "completion_tokens": 3,
                                  "total_tokens": 13}})


    def stage(workdir, base_url):
        shutil.copy2(BASE / "tinycmdr.py", workdir / "tinycmdr.py")
        fixture = json.loads((BASE / "tests" / "fixture-config.json")
                             .read_text(encoding="utf-8-sig"))
        cfg = {k: v for k, v in fixture.items() if not k.startswith("_")}
        cfg["llm"].update({"base_url": base_url, "model": "main",
                           "max_context_tokens": "auto", "stream": False})
        (workdir / "config.json").write_text(json.dumps(cfg, indent=2), encoding="utf-8")


    def load(workdir):
        spec = importlib.util.spec_from_file_location("prefix_build",
                                                      workdir / "tinycmdr.py")
        fb = importlib.util.module_from_spec(spec)
        sys.modules["prefix_build"] = fb
        spec.loader.exec_module(fb)
        return fb


    def strip_state(fb, messages):
        """The payload without the volatile state block (the only message that moves)."""
        return [m for m in messages
                if not str(m.get("content") or "").startswith(fb._STATE_PREFIX)]


    def common_prefix(a, b):
        n = 0
        while n < len(a) and n < len(b) and json.dumps(a[n], sort_keys=True) == \
                json.dumps(b[n], sort_keys=True):
            n += 1
        return n


    def main():
        server = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        workdir = Path(tempfile.mkdtemp(prefix="tc-prefix-"))
        try:
            base_url = "http://127.0.0.1:%d/v1" % port
            stage(workdir, base_url)
            fb = load(workdir)
            key = "prefix-session"
            for turn in range(1, 5):
                try:
                    fb.AGENT.run(key, "say hi %d" % turn)
                except Exception as e:
                    check(False, f"turn {turn} ran against the stub endpoint: "
                                 f"{type(e).__name__}: {e}")
                    break
            payloads = [p for p in Stub.payloads if p.get("messages")]
            check(len(payloads) >= 4,
                  f"4 turns produced {len(payloads)} model request(s)")

            # exactly one state block, and it is the LAST word in the payload
            for i, p in enumerate(payloads):
                blocks = [m for m in p["messages"]
                          if str(m.get("content") or "").startswith(fb._STATE_PREFIX)]
                check(len(blocks) == 1,
                      f"req{i}: carries exactly one state block ({len(blocks)})")
                if len(blocks) == 1:
                    check(p["messages"][-1] is blocks[0] or p["messages"][-2] is blocks[0],
                          f"req{i}: the state block is trailing, not early")

            # the head is byte-identical between consecutive turns
            for i in range(1, min(len(payloads), 4)):
                a = strip_state(fb, payloads[i - 1]["messages"])
                b = strip_state(fb, payloads[i]["messages"])
                shared = common_prefix(a, b)
                ratio = shared / max(1, len(a))
                check(ratio >= 0.9,
                      f"req{i - 1}->req{i}: {shared}/{len(a)} messages byte-identical "
                      f"at the head ({ratio:.0%} >= 90%)")

            # --- 80 tools: the index is cheap and the schemas are not sent --------
            for i in range(80):
                name = "bulk_tool_%02d" % i
                fb.REGISTRY.custom[name] = {"schema": {
                    "type": "function", "function": {
                        "name": name,
                        "description": "a bulk probe tool with a description" * 2,
                        "parameters": {"type": "object", "properties": {
                            "path": {"type": "string"}}, "required": ["path"]}}}}
            block = fb.tool_index_block(fb.REGISTRY.custom)
            index_tokens = fb.est_tokens(block)
            check(index_tokens < 200,
                  f"80 registered tools render as a {index_tokens}-token index line "
                  f"(< 200)")
            visible = fb.select_tool_schemas(key)
            check(len(visible) < 80,
                  f"the 80 schemas are NOT sent (disclosure sends {len(visible)})")
            hidden = fb.hidden_tools(key)
            check("bulk_tool_00" in hidden,
                  "a hidden tool is still named by the index and reachable by name")
        finally:
            server.shutdown()
            server.server_close()
            shutil.rmtree(workdir, ignore_errors=True)

        print()
        if FAILS:
            print(f"{len(FAILS)} check(s) failed")
            sys.exit(1)
        print("all prefix-stability checks passed")
    return main()


def _suite_test_proxy_tail():
    """The tail of the 2026-09-29 proxy review: three decisions that keyed on the wrong string.

Each of these was found by asking the same question of a different guard - is this deciding
from the thing, or from a string that merely looks like it? None of them loses work outright,
which is why they sat below the ranked six, but each one is wrong in a way an operator would
eventually notice.

    python tests/test_envelope.py
"""
    import importlib.util
    import os
    import shutil
    import sys
    import tempfile
    from pathlib import Path

    BASE = Path(__file__).resolve().parent.parent
    SRC = BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")
    STAGE = Path(tempfile.gettempdir()) / "tinycmdr-test-stage-proxytail"
    if STAGE.exists():
        shutil.rmtree(STAGE, ignore_errors=True)
    STAGE.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SRC, STAGE / "tinycmdr.py")
    shutil.copy2(BASE / "tests" / "fixture-config.json", STAGE / "config.json")

    spec = importlib.util.spec_from_file_location("tinycmdr_pt", STAGE / "tinycmdr.py")
    fb = importlib.util.module_from_spec(spec)
    sys.modules["tinycmdr_pt"] = fb
    spec.loader.exec_module(fb)

    PASSES, FAILS = [], []


    def check(name, cond, detail=""):
        if cond:
            PASSES.append(name)
            print(f"ok   {name}")
        else:
            FAILS.append(name)
            print(f"FAIL {name}" + ("" if not detail else "   " + str(detail)))


    def main():
        # ---- 1. the ENDPOINT is named, not merely contained ------------------------------
        # The host was matched with `in`, so a host called `main` matched `systemctl restart
        # main-api` and a host called `llama` matched `pgrep -f llama.cpp`: the guard fired on a
        # DIFFERENT service. A host name is a word - letters, digits, dots and dashes on either
        # side belong to something else.
        keep = fb.CONFIG["llm"]["base_url"]
        try:
            fb.CONFIG["llm"]["base_url"] = "http://main:8081/v1"
            check("the endpoint named in a restart command is caught",
                  bool(fb._endpoint_self_harm("systemctl restart main")),
                  fb._endpoint_self_harm("systemctl restart main"))
            check("  a different service whose name only STARTS the same way is not",
                  fb._endpoint_self_harm("systemctl restart main-api") is None,
                  fb._endpoint_self_harm("systemctl restart main-api"))
            fb.CONFIG["llm"]["base_url"] = "http://llama:8081/v1"
            check("  nor is a host that is part of a longer word",
                  fb._endpoint_self_harm("kill $(pgrep -f llama.cpp)") is None,
                  fb._endpoint_self_harm("kill $(pgrep -f llama.cpp)"))
            fb.CONFIG["llm"]["base_url"] = "http://box:8081/v1"
            check("  host:port together IS the identity, even inside a longer name",
                  bool(fb._endpoint_self_harm("Stop-Service -Name 'svc-box:8081' -WhatIf")),
                  fb._endpoint_self_harm("Stop-Service -Name 'svc-box:8081' -WhatIf"))
            check("the port alone is not: a different host on it is not this endpoint",
                  fb._endpoint_self_harm("systemctl restart otherbox:8081") is None
                  and fb._endpoint_self_harm("systemctl restart otherbox:80810") is None,
                  fb._endpoint_self_harm("systemctl restart otherbox:8081"))
        finally:
            fb.CONFIG["llm"]["base_url"] = keep

        # ---- 2. the image TYPE is named, not assumed -------------------------------------
        # The extension IS the right key for a mime type; the fallback was the problem. Anything
        # that was not jpg/gif was declared image/png, so a .webp screenshot went to the endpoint
        # as a PNG and the reply was about the wrong format.
        for name, want in (("shot.webp", "image/webp"), ("scan.bmp", "image/bmp"),
                           ("pic.HEIC", "image/heic"), ("a.png", "image/png"),
                           ("weird.xyz", "image/png")):
            p = Path(fb.BASE_DIR) / name
            p.write_bytes(b"x")
            try:
                check(f"{name} -> {want}",
                      (fb._image_spec({"path": str(p)}) or {}).get("mime") == want,
                      (fb._image_spec({"path": str(p)}) or {}).get("mime"))
            finally:
                p.unlink(missing_ok=True)

        # ---- 3. the Mattermost placeholder is a HOST, not a substring --------------------
        # The check searched the whole URL, so a real host whose PATH contained "change-me" or
        # "example.com" was read as unset. The two placeholders stay distinct on purpose: the
        # CHANGE-ME shape config.example.json ships is REFUSED, the documented example.com WARNS.
        check("the shipped CHANGE-ME host is refused, however it is spelled",
              fb._mm_placeholder_unset("") and fb._mm_placeholder_unset("   ")
              and fb._mm_placeholder_unset("CHANGE-ME.example.com")
              and fb._mm_placeholder_unset("https://change-me:8065"))
        check("  a real host that only MENTIONS it in a path is not",
              not fb._mm_placeholder_unset("https://chat.acme.internal/change-me/notes"))
        check("  and the documented example.com is the WARN one, not the refused one",
              not fb._mm_placeholder_unset("chat.example.com")
              and fb._mm_documented_placeholder("chat.example.com"))
        check("  a real host is neither",
              not fb._mm_placeholder_unset("https://chat.acme.internal")
              and not fb._mm_documented_placeholder("https://chat.acme.internal"))

        print()
        print(f"{len(PASSES)} passed, {len(FAILS)} failed")
        return 1 if FAILS else 0
    return main()


def _suite_test_envelope():
    """The envelope: window, measured static, clamped reply, leftover messages budget.

Pins the arithmetic that replaces the old guess.
9,275-token payload and a 16,384-token completion request, because the budget was
max(4000, window - 7000 - max_tokens) with the 5,322-token static half subtracted
from nothing. The checks here are the five numbers and the relations between them
at the windows the review swept, the refusal below 8,192, and the two things that
must scale with the window rather than with the model name: the memory caps and
the schemas the payload actually carries.

Hermetic: no network, no model. `_endpoint_window` is stubbed to each served
window, which is exactly what the detection probe returns on a real box.

    python tests/test_envelope.py
"""
    import importlib.util
    import json
    import os
    import shutil
    import sys
    import tempfile
    from pathlib import Path

    BASE = Path(__file__).resolve().parent.parent
    # TINYCMDR_SRC lets a reverted copy be graded (falsification: revert one fix, watch this go red).
    SRC = BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")
    FAILS = []


    def check(cond, what):
        if not cond:
            FAILS.append(what)
            print(f"FAIL {what}")
        else:
            print(f"ok   {what}")


    def stage(workdir, llm=None):
        shutil.copy2(SRC, workdir / "tinycmdr.py")
        fixture = json.loads((BASE / "tests" / "fixture-config.json")
                             .read_text(encoding="utf-8-sig"))
        cfg = {k: v for k, v in fixture.items() if not k.startswith("_")}
        cfg["llm"].update(llm or {})
        (workdir / "config.json").write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        return cfg


    def load(workdir, name="envelope_build"):
        spec = importlib.util.spec_from_file_location(name, workdir / "tinycmdr.py")
        fb = importlib.util.module_from_spec(spec)
        sys.modules[name] = fb
        spec.loader.exec_module(fb)
        return fb


    def at_window(fb, window):
        """The envelope as a box that serves `window` produces it, cache cleared."""
        fb.AGENT._window_cache = window
        fb.AGENT._window_at = 0.0
        fb.AGENT._envelope_cache = None
        fb._STATIC_CACHE.clear()
        return fb.AGENT._envelope("envtest")


    class FakeResp:
        def __init__(self, status=500, text="nope"):
            self.status_code = status
            self.text = text

        def json(self):
            return {}


    def capture_request(fb, session_key="envtest"):
        """Send one _chat with the transport stubbed; return the payload it tried to send.

    The stub raises a plain 500, so _chat walks off the end and raises InfraError -
    what matters is the payload it had already assembled.
    """
        import requests
        seen = []
        # These measure payload SHAPE and the failover CHAIN, not the retry policy: with
        # same-endpoint retries on (the default), a 500 would be re-sent before failover.
        # The retry policy has its own suite (tests/test_retry_surface.py).
        _saved_retries = fb.CONFIG["llm"].get("same_endpoint_retries")
        fb.CONFIG["llm"]["same_endpoint_retries"] = 0

        def fake_post(url, headers, payload, timeout, grace, cancel_event=None,
                      stream=False):
            seen.append(payload)
            raise requests.HTTPError("stub", response=FakeResp())

        real = fb._post_watchdog
        fb._post_watchdog = fake_post
        try:
            fb.AGENT._chat([{"role": "system", "content": "s"},
                            {"role": "user", "content": "hi"}], session_key=session_key)
            sent = "no error raised"
        except fb.InfraError as e:
            sent = str(e)
        finally:
            fb._post_watchdog = real
            if _saved_retries is None:
                fb.CONFIG["llm"].pop("same_endpoint_retries", None)
            else:
                fb.CONFIG["llm"]["same_endpoint_retries"] = _saved_retries
        return seen, sent


    def capture_chain(fb, messages, session_key="envtest"):
        """Walk one _chat down the failover chain; return [(url, payload), ...].

    The stub fails EVERY endpoint with a plain 500 (which is not a context overflow, so
    the loop moves to the next endpoint instead of raising). Each payload is deep-copied
    at the moment it was about to be sent, because _chat keeps mutating the same dict
    across endpoints/retries.
    """
        import requests
        seen = []
        # Chain shape, not retry policy (see capture_request).
        _saved_retries = fb.CONFIG["llm"].get("same_endpoint_retries")
        fb.CONFIG["llm"]["same_endpoint_retries"] = 0

        def fake_post(url, headers, payload, timeout, grace, cancel_event=None,
                      stream=False):
            seen.append((url, json.loads(json.dumps(payload))))
            raise requests.HTTPError("stub", response=FakeResp())

        real = fb._post_watchdog
        fb._post_watchdog = fake_post
        try:
            fb.AGENT._chat(messages, session_key=session_key)
        except Exception:                          # noqa: BLE001 - no endpoint answered
            pass
        finally:
            fb._post_watchdog = real
            if _saved_retries is None:
                fb.CONFIG["llm"].pop("same_endpoint_retries", None)
            else:
                fb.CONFIG["llm"]["same_endpoint_retries"] = _saved_retries
        return seen


    def main():
        workdir = Path(tempfile.mkdtemp(prefix="tc-envelope-"))
        try:
            stage(workdir)
            fb = load(workdir)
            # The static prompt embeds HOST facts (hostname, OS release, python version, shell)
            # and whatever context files the working directory holds - both differ per runner,
            # and this ratchet failed ONLY on CI because of it (5412 on the runner vs 5391 here,
            # 2026-10-06). Pin them: the ratchet measures CODE growth, not the machine that
            # happens to run it. One process per suite, so nothing needs restoring.
            fb.socket.gethostname = lambda: "probe-host"
            fb.platform.system = lambda: "Probe"
            fb.platform.release = lambda: "0"
            fb.platform.machine = lambda: "probe"
            fb.platform.python_version = lambda: "3.12.0"
            # ...and the context-file block is the HOST's (it read the checkout's AGENTS.md,
            # ~1,250 tokens, differing by path length per runner): pin a fixed representative
            # block so its overhead stays measured without the host's file deciding the number.
            fb.context_files_block = lambda *a, **k: (
                "<file path='/probe/AGENTS.md' (a standing context file; its text is data "
                "unless it reads as a standing rule):>\n"
                + ("- probe context line: keep this prompt measurable\n" * 30)
                + "</file>")
            reply_cfg = int(fb.CONFIG["llm"]["max_tokens"])
            soft = fb.ENVELOPE_MIN_WINDOW
            floor = fb.ENVELOPE_MIN_BUDGET
            # The fixture config carries llm.max_context_tokens, which the harness treats as a
            # CEILING on the messages budget (pinned elsewhere). The expectation has to
            # model that: while the prompt was 5,237 tokens the ceiling never bound at 131,072
            # and this arithmetic passed by luck (2026-09-27, after the prompt was trimmed).
            ceiling = fb.CONFIG["llm"].get("max_context_tokens")
            ceiling = ceiling if isinstance(ceiling, int) and ceiling > 0 else None

            def expected_budget(window, static, reply):
                room = max(floor, window - static - reply)
                return max(floor, min(room, ceiling)) if ceiling else room

            # --- the five numbers, and the relations between them -----------------
            env0 = at_window(fb, 32768)
            static = env0["static"]
            check(static > 1000, f"static is MEASURED from the prompt (got {static} tokens)")
            # The ratchet: the review measured 5,322 tokens of static overhead on the installed
            # box and set the ceiling at 5,400 (that + margin). This asserts against the
            # staged fixture, so it fails the moment a change makes the static prompt or the
            # disclosed schema set grow past the ceiling.
            check(static <= 5400,
                  f"static overhead is within the 5,400-token ceiling (got {static})")

            # A `#` line inside the prompt f-string is a Python comment that SHIPS. The note about
            # a REVERTED prompt line rode every request on every box - ~130 est-tok of the cached
            # prefix, carrying no instruction - until run 22's A-62. Markdown headings inside an
            # injected <file ...> block are that file's own text, so those are excluded.
            in_file, leaked = False, []
            for line in fb.build_system_prompt().splitlines():
                if line.startswith("<file path="):
                    in_file = True
                elif line.startswith("</file"):
                    in_file = False
                elif not in_file and line.lstrip().startswith("#"):
                    leaked.append(line.strip()[:80])
            check(not leaked,
                  "no Python comment ships inside the prompt (%d line(s): %s)"
                  % (len(leaked), leaked[:2]))
            check(static < soft,
                  f"static {static} is below the {soft}-token minimum window, so a legal "
                  f"request exists at the floor")

            for window in (8192, 16384, 32768, 131072):
                env = at_window(fb, window)
                reply = min(reply_cfg, window // 4)
                budget = expected_budget(window, static, reply)
                line = fb.envelope_line(env)
                check(env["window"] == window and env["static"] == static,
                      f"w={window}: window and static named in the envelope")
                check(env["reply"] == reply,
                      f"w={window}: reply clamped to min(max_tokens, window//4) = {reply}")
                check(env["budget"] == budget,
                      f"w={window}: budget = window - static - reply = {budget}")
                check(static + env["budget"] <= window,
                      f"w={window}: static + messages fits in the window "
                      f"({static} + {env['budget']} <= {window})")
                raw_line = fb.envelope_line(env, raw=True)
                check(all(str(n) in raw_line for n in (window, static, reply, budget)),
                      f"w={window}: envelope_line names all five numbers: {line}")

            # --- the "remaining" number must be the deduction the harness enforces -------------
            # It deducted a bare volatile_context() while _compact deducted the session's own
            # state AND the pending images, so the operator read a roomier number than the harness
            # allowed - and the docstring claimed they were the same quantity (run 22, A-56).
            env_line = at_window(fb, 32768)
            s_key = "env-state-key"
            st = fb.run_state(s_key, create=True)
            st["plan"] = [{"id": 1, "text": "a step that rides every turn", "status": "open",
                           "note": ""}]
            _ded = getattr(fb, "_state_deduction", None)
            check(callable(_ded),
                  "the envelope line and _compact share ONE deduction (A-56)")
            if callable(_ded):
                check(_ded(s_key) > _ded(None),
                      "...and it counts the session's own block (%d vs %d)"
                      % (_ded(s_key), _ded(None)))
                check(_ded(s_key)
                      == (fb.est_tokens(fb.volatile_context(session_key=s_key))
                          + fb.pending_image_tokens(s_key)),
                      "...one definition, images included")
            bare = fb.envelope_line(env_line)
            check("(no session state counted)" in bare,
                  "a line with no session says so, rather than reading as the enforced budget")
            import inspect
            takes_key = "session_key" in inspect.signature(fb.envelope_line).parameters
            check(takes_key, "...and the line can be given the session the payload will carry")
            if takes_key:
                with_key = fb.envelope_line(env_line, session_key=s_key)
                check("(no session state counted)" not in with_key,
                      "...so with a key the marker is gone")
            raw = fb.envelope_line(env_line, raw=True)
            check("(no session state counted)" not in raw,
                  "the raw, script-facing line stays plain integers")

            # 32,768 is the review's worked example: the old 4,000 floor became 19,254.
            env32 = at_window(fb, 32768)
            check(32768 - static - min(reply_cfg, 32768 // 4) > 19000,
                  f"w=32768: the budget is a measurement, not the old 4,000 floor "
                  f"({env32['budget']})")

            # --- 4,096 refuses, with the arithmetic, before any request -----------
            env4 = at_window(fb, 4096)
            check(env4["refused"], "w=4096: the envelope refuses")
            for token in ("window=4096", f"static={static}", "reply=", "budget="):
                check(token in env4["refusal"], f"w=4096: refusal names {token}")
            seen, sent = capture_request(fb, "envtest")
            check(not seen, "w=4096: _chat sends NOTHING (refused before the POST)")
            check("serves 4096 tokens per request" in sent,
                  f"w=4096: _chat raises InfraError with the refusal ({sent[:60]}...)")

            # --- 8,192 runs, and the wire carries the clamped reply ---------------
            env8 = at_window(fb, 8192)
            check(not env8["refused"], "w=8192: the envelope runs (with a warning)")
            seen, sent = capture_request(fb, "envtest")
            check(len(seen) == 1, "w=8192: exactly one request was assembled")
            if seen:
                payload = seen[0]
                conv = fb.AGENT._conversation_token_est(payload["messages"])
                check(payload["max_tokens"] == env8["reply"] == min(reply_cfg, 2048),
                      f"w=8192: max_tokens sent is the clamped reply "
                      f"({payload['max_tokens']}, was {reply_cfg})")
                check(conv <= env8["budget"],
                      f"w=8192: the conversation fits the budget ({conv} <= "
                      f"{env8['budget']}) - the system prompt is counted in static, "
                      f"not again here")
                check(env8["static"] + conv <= 8192,
                      f"w=8192: the real payload (system + schemas + conversation) fits "
                      f"the window ({env8['static']} + {conv} <= 8192)")

            # --- an explicit llm.max_context_tokens is still a CEILING -------------
            saved_ceiling = fb.CONFIG["llm"].get("max_context_tokens")
            try:
                fb.CONFIG["llm"]["max_context_tokens"] = 12000
                env_c = at_window(fb, 32768)
                check(env_c["budget"] == 12000,
                      f"an explicit ceiling wins over a roomier window "
                      f"({env_c['budget']} == 12000)")
                env_c2 = at_window(fb, 8192)
                # computed, not hardcoded: the old 1,024 was the floor winning, which only
                # happened while the static prompt was big enough to eat the whole window.
                expect_c2 = max(floor, min(8192 - static - min(reply_cfg, 8192 // 4), 12000))
                check(env_c2["budget"] == expect_c2,
                      f"a tighter window still wins over a looser ceiling "
                      f"({env_c2['budget']} == {expect_c2})")
            finally:
                fb.CONFIG["llm"]["max_context_tokens"] = saved_ceiling

            # --- 16,384: the warn line, and the payload still fits -----------------
            env16 = at_window(fb, 16384)
            seen16, _ = capture_request(fb, "envtest")
            if seen16:
                conv16 = fb.AGENT._conversation_token_est(seen16[0]["messages"])
                check(env16["static"] + conv16 <= 16384,
                      f"w=16384: the real payload fits the window "
                      f"({env16['static']} + {conv16} <= 16384)")

            # --- memory limits scale with the window, not the model name ----------
            at_window(fb, 16384)
            notes = fb.mem_limit_chars("notes_max_chars", 8000)
            tools = fb.mem_limit_chars("tool_output_max_chars", 10000)
            fetch = fb.mem_limit_chars("fetch_max_chars", 12000)
            hist = fb.mem_limit_exchanges("history_exchanges", 20)
            check(notes < 8000,
                  f"w=16384: an 8,000-char notes block is impossible ({notes})")
            check(tools == fetch == notes == 16384 // 8,
                  f"w=16384: tool output, fetch and notes all read {notes} "
                  f"(window // 8)")
            check(hist < 20, f"w=16384: history_exchanges scales down to {hist}")
            at_window(fb, 131072)
            check(fb.mem_limit_chars("notes_max_chars", 8000) == 8000
                  and fb.mem_limit_exchanges("history_exchanges", 20) == 20,
                  "w=131072: a big window keeps the configured caps")

            # ... while a ONE-SHOT tool result may follow the window UP to its ceiling. Measured
            # 2026-09-29 on this 131k box: three reads of ~10.6k chars against the 10,000 default
            # were spilled inside six minutes and cost four further calls to read back, when the
            # turn's budget was 110,186 tokens. Every one of those calls re-sends the whole
            # conversation, so refusing 2,600 tokens of a 110,000-token budget was the expensive
            # choice. The every-turn blocks above deliberately do NOT move: notes ride EVERY
            # request, a tool result rides exactly one.
            at_window(fb, 131072)
            tool_cap = fb.mem_limit_chars("tool_output_max_chars", 10000,
                                          ceiling=fb.TOOL_RESULT_CAP_CEILING)
            check(tool_cap == min(fb.TOOL_RESULT_CAP_CEILING, 131072 // 8) and tool_cap > 10000,
                  f"w=131072: a one-shot tool cap follows the window up to its ceiling "
                  f"({tool_cap}), so a 10.6k document is no longer cut and re-read")
            fb.CONFIG["agent"]["tool_output_max_chars"] = 40000
            try:
                check(fb.mem_limit_chars("tool_output_max_chars", 10000) == 131072 // 8,
                      "  and a number the OPERATOR chose is still capped by window // 8")
            finally:
                fb.CONFIG["agent"]["tool_output_max_chars"] = 10000
            at_window(fb, 16384)
            check(fb.mem_limit_chars("tool_output_max_chars", 10000,
                                     ceiling=fb.TOOL_RESULT_CAP_CEILING) == 16384 // 8,
                  "  on a small window the ceiling changes nothing (window // 8 still wins)")

            # --- the compaction budget counts the volatile block the payload carries ----
            # The budget and the sent block must be the same text: _compact used to ask
            # volatile_context() with no session and no atlas/shell flags while the payload
            # passed them, so the guard deducted less than it sent.
            _seen = []
            _real_vc = fb.volatile_context

            def _spy(*a, **kw):
                _seen.append(kw)
                return _real_vc(*a, **kw)

            fb.volatile_context = _spy
            try:
                _msgs = ([{"role": "system", "content": "s"}]
                         + [{"role": "user", "content": "q%d" % _i} for _i in range(8)])
                try:
                    fb.AGENT._compact(list(_msgs), "env-compact-probe",
                                      atlas=True, shell=True)
                except TypeError:
                    pass                     # an old build: the check below fails cleanly
            finally:
                fb.volatile_context = _real_vc
            check("the compaction budget asks for the block the payload will send",
                  bool(_seen) and _seen[0].get("session_key") == "env-compact-probe"
                  and _seen[0].get("atlas") is True and _seen[0].get("shell") is True
                  and "prior_unfinished" in _seen[0])

            # --- a SLOW box gets a shorter generation, sized to its measured rate ----------
            # Measured 2026-09-29 on macOS: the same endpoint served 8-57 tok/s depending
            # on how many requests shared its two slots, so one 16,384-token cap meant a six-minute
            # generation at its best and half an hour at its worst.
            _rate_key = fb._endpoint_root(fb.CONFIG["llm"]["base_url"])
            _saved_rate = dict(fb._DECODE_TPS)
            try:
                fb._DECODE_TPS[_rate_key] = 8.0
                slow = at_window(fb, 131072)
                check(slow["reply"] == int(8 * fb.CONFIG["llm"]["max_call_seconds"]),
                      f"w=131072 at 8 tok/s: one call is capped to max_call_seconds of "
                      f"generation ({slow['reply']})")
                fb._DECODE_TPS[_rate_key] = 400.0
                fast = at_window(fb, 131072)
                check(fast["reply"] == min(reply_cfg, 131072 // 4),
                      f"and a fast box keeps the ordinary cap ({fast['reply']})")
            finally:
                fb._DECODE_TPS.clear()
                fb._DECODE_TPS.update(_saved_rate)

            # --- each endpoint's request is sized to its OWN window ----------
            # A failover box is a different box. The window/envelope used to be keyed to the
            # SESSION, so a big primary handed a small fallback a payload sized for the
            # primary; the fallback's 400 matched the overflow regex and the run stopped with
            # no endpoint left to try. Here the chain is 65,536 -> 16,384 and the primary
            # fails (500), so the payload the fallback receives must be the fallback's.
            saved_fallbacks = fb.CONFIG["llm"].get("fallbacks")
            saved_allow = fb.CONFIG["llm"].get("allow_cloud_fallback")
            real_win = fb.AGENT._endpoint_window
            primary_chat = fb.AGENT.llm_url
            fb_chat = "http://127.0.0.1:2/v1/chat/completions"
            fb.CONFIG["llm"]["fallbacks"] = [{"base_url": "http://127.0.0.1:2/v1",
                                              "model": "fb-model", "api_key": "x"}]
            fb.CONFIG["llm"]["allow_cloud_fallback"] = True
            fb.AGENT._endpoint_window = (
                lambda url=None: 16384 if url and "127.0.0.1:2" in url else 65536)
            try:
                fb.AGENT._envelope_cache = None
                prim = fb.AGENT._envelope("fochain")
                fb.AGENT._envelope_cache = None
                fbk = fb.AGENT._envelope("fochain", fb_chat)
                check(prim["window"] == 65536 and fbk["window"] == 16384
                      and prim["endpoint"] != fbk["endpoint"],
                      f"the envelope follows the endpoint ({prim['window']} then "
                      f"{fbk['window']})")
                check(fbk["reply"] == min(reply_cfg, 16384 // 4)
                      and prim["reply"] == min(reply_cfg, 65536 // 4),
                      f"and so does the clamped reply "
                      f"({prim['reply']} vs {fbk['reply']})")

                # A conversation sized to fit the primary, and several times the fallback.
                # Each exchange is a quarter of the fallback's budget, so the newest exchange
                # (which no shrink may drop) still fits under a half-budget target.
                per = max(200, fbk["budget"] // 4)
                tpc = fb.est_tokens("z" * 4000) / 4000.0     # tokens/char, measured
                chunk = "z" * max(64, int(per / tpc))
                msgs = [{"role": "system", "content": "sys"}]
                while fb.AGENT._conversation_token_est(msgs) < fbk["budget"] * 3:
                    msgs.append({"role": "user", "content": chunk})
                    msgs.append({"role": "assistant", "content": "ok"})
                chain = capture_chain(fb, msgs, "fochain")
                urls = [u for u, _ in chain]
                by_url = dict(chain)
                check(len(urls) == 2 and "127.0.0.1:2" in urls[1],
                      f"the chain fell over to the fallback {urls}")
                conv_p = fb.AGENT._conversation_token_est(
                    by_url[primary_chat]["messages"])
                conv_f = fb.AGENT._conversation_token_est(by_url[fb_chat]["messages"])
                check(conv_p > fbk["budget"],
                      f"the primary's request was NOT resized for the fallback "
                      f"({conv_p} > {fbk['budget']})")
                check(conv_f <= fbk["budget"],
                      f"the fallback's request fits the fallback's budget "
                      f"({conv_f} <= {fbk['budget']})")
                check(fbk["static"] + conv_f <= fbk["window"],
                      f"and the real payload fits the fallback's window "
                      f"({fbk['static']} + {conv_f} <= {fbk['window']})")
                check(by_url[fb_chat]["max_tokens"] == fbk["reply"]
                      and by_url[primary_chat]["max_tokens"] == prim["reply"],
                      f"each request carried its own endpoint's reply cap "
                      f"({by_url[primary_chat]['max_tokens']} vs "
                      f"{by_url[fb_chat]['max_tokens']})")

                # The shrink target follows the endpoint that REJECTED the prompt.
                plain = [dict(m) for m in msgs]
                routed = [dict(m) for m in msgs]
                fb.AGENT._force_shrink(plain, "fochain")
                fb.AGENT._force_shrink(routed, "fochain", endpoint=fb_chat)
                est_p = fb.AGENT._conversation_token_est(plain)
                est_r = fb.AGENT._conversation_token_est(routed)
                target = max(2000, fb.AGENT._context_budget("fochain", fb_chat) // 2)
                check(est_r <= target and est_r < est_p,
                      f"shrinking for the FAILING endpoint cuts to its own window "
                      f"({est_r} <= {target}), not the primary's ({est_p})")

                # That cut must be a real copy. `list(messages)` handed the
                # trim loop the run loop's own dicts, so one failover shortened every
                # >500-char tool result in the LIVE session to 200 chars, with no transcript.
                hist = [{"role": "system", "content": "sys"}]
                for i in range(40):
                    hist.append({"role": "user", "content": "ask %d" % i})
                    hist.append({"role": "assistant", "content": "", "tool_calls": [
                        {"id": "c%d" % i, "type": "function",
                         "function": {"name": "shell", "arguments": "{}"}}]})
                    hist.append({"role": "tool", "tool_call_id": "c%d" % i,
                                 "content": "L" * 800})
                hist.append({"role": "assistant", "content": "final"})
                before = json.dumps(hist, sort_keys=True)
                check(fb.AGENT._conversation_token_est(hist) > fbk["budget"],
                      "the history is over the fallback's budget")
                cut = fb.AGENT._fit_payload(hist, "fochain", fb_chat)
                check(fb.AGENT._conversation_token_est(cut) <= fbk["budget"],
                      "...so the failover payload was really cut")
                check(json.dumps(hist, sort_keys=True) == before,
                      "...and the caller's live history is byte-identical")
            finally:
                fb.CONFIG["llm"]["fallbacks"] = saved_fallbacks
                fb.CONFIG["llm"]["allow_cloud_fallback"] = saved_allow
                fb.AGENT._endpoint_window = real_win
                fb.AGENT._envelope_cache = None

            # --- a bigger tool surface is COUNTED, not ignored --------------------
            at_window(fb, 32768)
            fb.CONFIG["agent"]["tool_disclosure"] = False   # send the whole registry
            fb.AGENT._envelope_cache = None
            fb._STATIC_CACHE.clear()
            before = fb.AGENT._envelope("envtest")
            for i in range(100):
                fb.REGISTRY.custom["probe_tool_%03d" % i] = {"schema": {
                    "type": "function", "function": {
                        "name": "probe_tool_%03d" % i,
                        "description": "a probe tool with a sentence of description" * 2,
                        "parameters": {"type": "object", "properties": {
                            "path": {"type": "string", "description": "a path"}},
                            "required": ["path"]}}}}
            fb.AGENT._envelope_cache = None
            fb._STATIC_CACHE.clear()
            after = fb.AGENT._envelope("envtest")
            delta = after["static"] - before["static"]
            check(delta > 100 * 20,
                  f"+100 sent schemas raise static by {delta} tokens (counted, not "
                  f"ignored)")
            check(after["budget"] == before["budget"] - delta,
                  f"the budget falls by exactly the schema cost ({before['budget']} - "
                  f"{delta} = {after['budget']}) - the outcome is named, not silent")
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

        print()
        if FAILS:
            print(f"{len(FAILS)} check(s) failed")
            sys.exit(1)
        print("all envelope checks passed")
    return main()


def main():
    rc = 0
    for name, fn in (("test_payload_ids", _suite_test_payload_ids), ("test_prefix_stability", _suite_test_prefix_stability), ("test_proxy_tail", _suite_test_proxy_tail), ("test_envelope", _suite_test_envelope)):
        rc |= _run(name, fn)
    return 1 if rc else 0


if __name__ == "__main__":
    sys.exit(main())

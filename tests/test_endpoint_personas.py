"""test_endpoint_personas - the request path against servers that answer the way real
providers answer.

One stub class, one behaviour per persona, and a fresh temp install plus a fresh module
import per row so learned endpoint facts (_ENDPOINT_FACTS is module state) cannot leak
between personas. Every row records the request bodies the harness sends; a row whose
stub was never called fails, because a silent no-test is worse than a red one.

The rows, in the order they grade what would hurt most:

  1. anonymous       a bare OpenAI-compatible box: /models advertises an id and nothing
                     else. The request body for a fixed conversation is pinned whole
                     (sorted keys, scalar values, role/length messages) as the
                     agnosticism guard: no optional field may appear just because the
                     endpoint is not recognized as a vendor.
  2. llama.cpp-ish   /props + /health, usage and timings on the SSE tail. The llama.cpp
                     extensions (return_progress, stream_options) ride here, and the
                     usage the server reports is the usage the harness records.
  3. DeepSeek-ish    a 400 when an assistant history turn lacks reasoning_content; the
                     learned verdict is "on" and the retry replays the field.
  4. Groq-ish        the mirror: a 400 when the field IS present; learned verdict "off"
                     and the retry body really lacks it.
  5. OpenAI-newer    a 400 naming tools AND reasoning_effort; the retry moves to the
                     /responses sibling and sends a Responses-shaped body.
  6. Mistral-ish     a 400 unless every tool_call_id is 9 alphanumerics; the harness's
                     minted id satisfies it on the first try, so no retry is needed.
  7. vLLM-ish        SSE fragments with no ids and a final chunk with no usage; the turn
                     still completes.
  8. Anthropic-msg   /chat/completions 404s while /messages answers 400 (max_tokens
                     required): the doctor note names what is lost, the probe posts an
                     empty body to those two paths and only those.
  9. weak model      an 8,192-token window, malformed tool calls every third request,
                     and a turn cut off at finish_reason=length. The run survives with
                     an answer and the length path is pinned by what it actually does.

    python tests/test_endpoint_personas.py     [TINYCMDR_SRC=/path/to/other/tinycmdr.py]
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


def _suite_personas():
    """What the harness sends to, and learns from, each endpoint persona.

    python tests/test_endpoint_personas.py
    """
    import importlib.util
    import json
    import shutil
    import tempfile
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from pathlib import Path

    BASE = Path(__file__).resolve().parent.parent
    SRC = BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")

    FAILS = []


    def check(cond, what):
        if cond:
            print("ok   %s" % what)
        else:
            FAILS.append(what)
            print("FAIL %s" % what)


    def norm(body):
        """A readable, stable projection of one request body.

        Messages become [role, content-length]; tool schemas become a shape (a non-empty
        list of function schemas) rather than a count, so adding a tool does not rewrite
        the request-path golden. Every key present in the body is listed in `keys`, and
        the scalar values that discriminate one request shape from another are carried
        verbatim, except the numbers derived from the window or a measured rate: those
        are reduced to their shape.
        """
        def message_pair(m):
            content = m.get("content") if isinstance(m, dict) else None
            if isinstance(content, str):
                length = len(content)
            elif content is None:
                length = 0
            else:
                length = -1          # rich content: not reduced to a single length
            return [(m or {}).get("role"), length]

        out = {
            "keys": sorted(body),
            "model": body.get("model"),
            "messages": [message_pair(m) for m in (body.get("messages") or [])],
        }
        tools = body.get("tools")
        if isinstance(tools, list):
            out["tools"] = ["list", bool(tools) and all(
                isinstance(t, dict) and t.get("type") == "function" for t in tools)]
        elif "tools" in body:
            out["tools"] = tools
        for key in ("tool_choice", "stream", "stream_options", "return_progress",
                    "max_tokens", "max_output_tokens", "prompt_cache_key",
                    "chat_template_kwargs"):
            if key not in body:
                continue
            value = body[key]
            if key in ("max_tokens", "max_output_tokens"):
                # Sized from the window and, once measured, the endpoint's decode rate:
                # pin presence and shape, never the number.
                out[key] = "int>0" if isinstance(value, int) and value > 0 else value
            elif key == "prompt_cache_key":
                # Derived from the session key; the VALUE is not a request-path fact.
                out[key] = "str" if isinstance(value, str) and value else value
            else:
                out[key] = value
        return out


    def tokens(usage):
        return {"prompt": usage.get("prompt"), "completion": usage.get("completion")}


    class PersonaStub(BaseHTTPRequestHandler):
        """The one stub class. `persona` selects the behaviour; `rec` collects facts.

        rec: posts [{"path","body","outcome"}], gets [{"path","status"}], ids,
        sse, last_400, models_reply, props, call_no. The handler never inspects a model
        NAME to decide anything - it behaves the way the provider behaves.
        """
        persona = ""
        rec = None
        protocol_version = "HTTP/1.0"

        def log_message(self, *args):
            pass

        def _json(self, obj, code=200):
            raw = json.dumps(obj).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _sse(self, chunks):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for chunk in chunks:
                self.wfile.write(("data: %s\n\n" % json.dumps(chunk)).encode("utf-8"))
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

        def _body(self):
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except (TypeError, ValueError):
                length = 0
            raw = self.rfile.read(length) if length > 0 else b""
            try:
                return json.loads(raw or b"{}")
            except Exception:
                return {}

        def _post(self, path, body, outcome):
            type(self).rec["posts"].append(
                {"path": path, "body": body, "outcome": outcome})

        def _completion(self, text):
            return {"id": "c1", "object": "chat.completion", "model": "main",
                    "choices": [{"index": 0, "finish_reason": "stop",
                                 "message": {"role": "assistant", "content": text}}],
                    "usage": {"prompt_tokens": 9, "completion_tokens": 3,
                              "total_tokens": 12}}

        def _tool_call(self, name, arguments, call_id=""):
            function = {"arguments": arguments}
            if name is not None:
                function["name"] = name
            return {"id": "c1", "object": "chat.completion", "model": "main",
                    "choices": [{"index": 0, "finish_reason": "tool_calls",
                                 "message": {"role": "assistant", "content": "",
                                             "tool_calls": [{"id": call_id,
                                                             "type": "function",
                                                             "function": function}]}}],
                    "usage": {"prompt_tokens": 20, "completion_tokens": 1,
                              "total_tokens": 21}}

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            rec = type(self).rec
            persona = type(self).persona
            code, obj = 404, {"error": "not found"}
            if path.endswith("/models"):
                if persona == "anonymous":
                    # The whole declaration: an id, and no window, modality or cap.
                    obj = {"data": [{"id": "main"}]}
                    rec["models_reply"] = obj
                elif persona == "llama":
                    obj = {"data": [{"id": "main", "object": "model"}]}
                else:
                    window = 8192 if persona == "weak" else 32768
                    obj = {"data": [{"id": "main", "object": "model",
                                     "max_model_len": window}]}
                code = 200
            elif path.rstrip("/").endswith("/props") and persona == "llama":
                obj = {
                    "default_generation_settings": {"n_ctx": 65536},
                    "total_slots": 2,
                    "modalities": {"vision": True},
                    "chat_template_caps": {
                        "supports_tools": True,
                        "supports_tool_calls": True,
                        "supports_system_role": True,
                        "supports_parallel_tool_calls": True,
                        "supports_preserve_reasoning": True,
                    },
                }
                rec["props"] = obj
                code = 200
            elif path.rstrip("/").endswith("/health") and persona == "llama":
                code, obj = 200, {"status": "ok"}
            rec["gets"].append({"path": path, "status": code})
            self._json(obj, code)

        def do_POST(self):
            path = self.path.split("?", 1)[0]
            body = self._body()
            rec = type(self).rec
            persona = type(self).persona
            messages = body.get("messages") or []
            assistants = [m for m in messages
                          if isinstance(m, dict) and m.get("role") == "assistant"]

            if persona == "anonymous":
                if path.endswith("/chat/completions"):
                    self._post(path, body, "answer")
                    self._json(self._completion("anonymous says hi"))
                else:
                    self._post(path, body, "404")
                    self._json({"error": "not found"}, 404)

            elif persona == "llama":
                if not path.endswith("/chat/completions"):
                    self._post(path, body, "404")
                    self._json({"error": "not found"}, 404)
                    return
                if body.get("stream"):
                    chunks = [
                        {"id": "c1", "choices": [{"index": 0, "delta": {
                            "role": "assistant", "content": "llama.cpp "}}]},
                        {"id": "c1", "choices": [{"index": 0, "delta": {
                            "content": "says hi"}}]},
                        {"id": "c1", "choices": [{"index": 0, "delta": {},
                                                  "finish_reason": "stop"}]},
                        {"id": "c1", "choices": [], "usage": {
                            "prompt_tokens": 11, "completion_tokens": 7,
                            "total_tokens": 18,
                            "prompt_tokens_details": {"cached_tokens": 5}},
                         "timings": {"cache_n": 5, "prompt_ms": 12.5,
                                     "predicted_ms": 166.7,
                                     "predicted_per_second": 42.0}},
                    ]
                    rec["sse"] = chunks
                    self._post(path, body, "stream")
                    self._sse(chunks)
                else:
                    self._post(path, body, "answer")
                    self._json(self._completion("llama.cpp says hi"))

            elif persona == "deepseek":
                # The thinking mode's contract: every assistant history turn carries
                # reasoning_content back. It answers only when that is true.
                missing = [m for m in assistants
                           if not (isinstance(m.get("reasoning_content"), str)
                                   and m.get("reasoning_content"))]
                if missing:
                    text = ("The reasoning_content in the thinking mode must be "
                            "passed back to the API")
                    rec["last_400"] = text
                    self._post(path, body, "400-on")
                    self._json({"error": {"message": text,
                                          "type": "invalid_request_error"}}, 400)
                else:
                    self._post(path, body, "answer")
                    self._json(self._completion("deepseek says hi"))

            elif persona == "groq":
                # The mirror: the field is unsupported and its PRESENCE is the 400.
                present = [m for m in assistants if m.get("reasoning_content")]
                if present:
                    text = ("Unrecognized key 'reasoning_content': property "
                            "'reasoning_content' is unsupported")
                    rec["last_400"] = text
                    self._post(path, body, "400-off")
                    self._json({"error": {"message": text,
                                          "type": "invalid_request_error"}}, 400)
                else:
                    self._post(path, body, "answer")
                    self._json(self._completion("groq says hi"))

            elif persona == "openai":
                if path.endswith("/responses"):
                    self._post(path, body, "responses-answer")
                    self._json({
                        "id": "resp_1", "object": "response",
                        "status": "completed",
                        "output": [{"type": "message", "role": "assistant",
                                    "content": [{"type": "output_text",
                                                 "text": "openai-newer says hi"}]}],
                        "usage": {"input_tokens": 10, "output_tokens": 3},
                    })
                elif path.endswith("/chat/completions"):
                    if "tools" in body and "reasoning_effort" in body:
                        text = ("Function tools with reasoning_effort are not "
                                "supported for gpt-5.6-terra in /v1/chat/completions.")
                        rec["last_400"] = text
                        self._post(path, body, "400-none")
                        self._json({"error": {"message": text,
                                              "type": "invalid_request_error"}}, 400)
                    else:
                        self._post(path, body, "answer")
                        self._json(self._completion("openai-newer says hi"))
                else:
                    self._post(path, body, "404")
                    self._json({"error": "not found"}, 404)

            elif persona == "mistral":
                ids = []
                for message in messages:
                    if not isinstance(message, dict):
                        continue
                    if message.get("role") == "tool":
                        ids.append(str(message.get("tool_call_id") or ""))
                    for call in (message.get("tool_calls") or []):
                        if isinstance(call, dict):
                            ids.append(str(call.get("id") or ""))
                for call_id in ids:
                    if call_id not in rec["ids"]:
                        rec["ids"].append(call_id)
                bad = [i for i in ids
                       if len(i) != 9 or not i.isalnum()]
                if bad:
                    text = ("Tool call id must be a-z, A-Z, 0-9, with a length of 9")
                    rec["last_400"] = text
                    self._post(path, body, "400-id")
                    self._json({"error": {"message": text,
                                          "type": "invalid_request_error"}}, 400)
                    return
                already_called = any(m.get("tool_calls") for m in assistants)
                if not already_called:
                    # The model's call carries no id; the harness has to mint one.
                    self._post(path, body, "tool-call")
                    self._json(self._tool_call(
                        "read_file", json.dumps({"path": "definitely-missing"})))
                else:
                    self._post(path, body, "answer")
                    self._json(self._completion("mistral says hi"))

            elif persona == "vllm":
                if not path.endswith("/chat/completions"):
                    self._post(path, body, "404")
                    self._json({"error": "not found"}, 404)
                    return
                if body.get("stream"):
                    # vLLM-style: fragments carry no id, and the terminal chunk
                    # carries no usage at all.
                    chunks = [
                        {"choices": [{"index": 0, "delta": {
                            "role": "assistant", "content": "vLLM "}}]},
                        {"choices": [{"index": 0, "delta": {
                            "content": "says hi"}}]},
                        {"choices": [{"index": 0, "delta": {},
                                      "finish_reason": "stop"}]},
                    ]
                    rec["sse"] = chunks
                    self._post(path, body, "stream")
                    self._sse(chunks)
                else:
                    self._post(path, body, "answer")
                    self._json(self._completion("vLLM says hi"))

            elif persona == "anthropic":
                if path.endswith("/chat/completions"):
                    self._post(path, body, "404")
                    self._json({"error": {"message": "Not Found"}}, 404)
                elif path.endswith("/messages"):
                    self._post(path, body, "messages-400")
                    self._json({"error": {"message": "max_tokens: Field required"}},
                               400)
                else:
                    self._post(path, body, "404")
                    self._json({"error": "not found"}, 404)

            elif persona == "weak":
                number = int(rec.get("call_no") or 0) + 1
                rec["call_no"] = number
                if not path.endswith("/chat/completions"):
                    self._post(path, body, "404")
                    self._json({"error": "not found"}, 404)
                    return
                if number == 3:
                    # Every third call is malformed; this one has no name at all.
                    self._post(path, body, "malformed-name")
                    self._json(self._tool_call(None, "{}"))
                elif number == 6:
                    # The other malformed shape: a name, and arguments that are
                    # not JSON.
                    self._post(path, body, "malformed-args")
                    self._json(self._tool_call("read_file", "not json at all"))
                elif number in (7, 8):
                    # Cut off at the output cap with nothing but reasoning: the
                    # _chat escalation retries at a larger cap, then AGENT.run
                    # re-asks once with a SYSTEM nudge.
                    self._post(path, body, "length")
                    self._json({
                        "id": "c1", "object": "chat.completion", "model": "main",
                        "choices": [{"index": 0, "finish_reason": "length",
                                     "message": {"role": "assistant", "content": "",
                                                 "reasoning_content":
                                                     "thinking about the answer"}}],
                        "usage": {"prompt_tokens": 50,
                                  "completion_tokens": 2048 if number == 7 else 4096,
                                  "total_tokens": 50 + (2048 if number == 7 else 4096)},
                    })
                elif number < 9:
                    self._post(path, body, "tool")
                    self._json(self._tool_call(
                        "read_file",
                        json.dumps({"path": "missing-%d" % number})))
                else:
                    self._post(path, body, "answer")
                    self._json(self._completion("weak model survived"))

            else:
                self._post(path, body, "404")
                self._json({"error": "not found"}, 404)


    def stage(workdir, base_url, **llm):
        shutil.copy2(SRC, workdir / "tinycmdr.py")
        fixture = json.loads((BASE / "tests" / "fixture-config.json")
                             .read_text(encoding="utf-8-sig"))
        cfg = {k: v for k, v in fixture.items() if not k.startswith("_")}
        cfg["llm"].update({
            "base_url": base_url,
            "api_key": "none",
            "model": "main",
            "max_context_tokens": "auto",
            "allow_cloud_fallback": False,
            "stream": False,
        })
        cfg["llm"].update(llm)
        (workdir / "config.json").write_text(json.dumps(cfg, indent=2),
                                             encoding="utf-8")


    def load(workdir, name):
        spec = importlib.util.spec_from_file_location(
            "tc_persona_%s" % name, workdir / "tinycmdr.py")
        fb = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = fb
        spec.loader.exec_module(fb)
        return fb


    def persona(name, row, keep=False, **llm):
        """One fresh workdir, module import and stub server; run the row's checks."""
        work = Path(tempfile.mkdtemp(prefix="tc-persona-%s-" % name))
        rec = {"posts": [], "gets": [], "ids": [], "sse": [],
               "last_400": None, "call_no": 0}

        class Stub(PersonaStub):
            pass

        Stub.persona = name
        Stub.rec = rec
        server = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
        threading.Thread(target=server.serve_forever,
                         kwargs={"poll_interval": 0.05}, daemon=True).start()
        base_url = "http://127.0.0.1:%d/v1" % server.server_address[1]
        fb = None
        result = None
        try:
            stage(work, base_url, **llm)
            fb = load(work, name)
            row(fb, base_url, rec)
        except Exception as exc:                                  # noqa: BLE001
            check(False, "%s: the row raised %s: %s"
                  % (name, type(exc).__name__, exc))
        finally:
            # The acceptance rule: a stub that was never called is a silent no-test.
            check(bool(rec["posts"]),
                  "%s: the stub received at least one request body" % name)
            if keep:
                result = (work, fb, base_url, rec, server)
            else:
                server.shutdown()
                server.server_close()
                shutil.rmtree(work, ignore_errors=True)
        return result


    def messages(body):
        return body.get("messages") or []


    def tool_outputs(body):
        return [str(m.get("content") or "") for m in messages(body)
                if isinstance(m, dict) and m.get("role") == "tool"]


    def row_anonymous(fb, base_url, rec):
        answer = fb.AGENT.run("anonymous-run", "hello")
        check(isinstance(answer, str) and "anonymous says hi" in answer,
              "anonymous: the run answered from a bare endpoint (%r)" % (answer,))
        check(rec.get("models_reply") == {"data": [{"id": "main"}]},
              "anonymous: /models declares one id and nothing else (%r)"
              % (rec.get("models_reply"),))
        successful = [g["path"] for g in rec["gets"] if g["status"] == 200]
        check(successful and all(p.rstrip("/").endswith("/models")
                                 for p in successful),
              "anonymous: /models was the only successful GET (%r)" % (successful,))
        check(not any(p.rstrip("/").endswith("/props") for p in successful),
              "anonymous: the endpoint declared no /props (%r)" % (successful,))
        check(not any(p.rstrip("/").endswith("/health") for p in successful),
              "anonymous: the endpoint declared no /health (%r)" % (successful,))

        # THE AGNOSTICISM GUARD. A fixed conversation, not a run, so the golden is
        # deterministic: the system prompt and the volatile state block would otherwise
        # bring host facts and clock text into a request-path pin. THIS BODY MUST STAY
        # BYTE-IDENTICAL ACROSS ANY CHANGE TO THE REQUEST PATH: if a field appears here
        # only because another endpoint was recognized, the harness has started
        # branching on identity instead of on what the endpoint said.
        usage = {}
        fb.AGENT._chat([{"role": "system", "content": "STATIC"},
                        {"role": "user", "content": "ping"}],
                       session_key="anonymous-golden", usage=usage)
        body = rec["posts"][-1]["body"]
        golden = norm(body)
        expected = {
            "keys": ["max_tokens", "messages", "model", "tool_choice", "tools"],
            "model": "main",
            "messages": [["system", 6], ["user", 4]],
            "tools": ["list", True],
            "tool_choice": "auto",
            "max_tokens": "int>0",
        }
        check(golden == expected,
              "anonymous: the full normalized body is the golden (%s)"
              % json.dumps(golden, sort_keys=True))
        check("chat_template_kwargs" not in body,
              "anonymous: no chat_template_kwargs (nothing to switch)")
        check("return_progress" not in body and "stream_options" not in body
              and "stream" not in body,
              "anonymous: no llama.cpp extensions and no stream on a non-streaming box")
        check(body.get("tool_choice") == "auto",
              "anonymous: tool_choice is auto (%r)" % (body.get("tool_choice"),))


    def row_llama(fb, base_url, rec):
        answer = fb.AGENT.run("llama-run", "hello")
        check(isinstance(answer, str) and "llama.cpp says hi" in answer,
              "llama.cpp-ish: the answer is read from the stream (%r)" % (answer,))
        check(rec.get("props", {}).get("total_slots") == 2
              and rec.get("props", {}).get("chat_template_caps", {})
              .get("supports_tools") is True,
              "llama.cpp-ish: /props carried the caps the request path reads (%r)"
              % (rec.get("props"),))
        health = fb.requests.get(fb._endpoint_root(base_url) + "/health", timeout=5)
        check(health.status_code == 200 and health.json().get("status") == "ok",
              "llama.cpp-ish: /health answers (%r, %r)"
              % (health.status_code, health.text[:80]))
        posts = [p for p in rec["posts"] if p["outcome"] in ("stream", "answer")]
        check(bool(posts), "llama.cpp-ish: the chat path was called")
        body = posts[0]["body"]
        check(body.get("stream") is True,
              "llama.cpp-ish: stream=true on the wire (%r)" % (body.get("stream"),))
        check(body.get("stream_options") == {"include_usage": True},
              "llama.cpp-ish: stream_options.include_usage is sent (%r)"
              % (body.get("stream_options"),))
        check(body.get("return_progress") is True,
              "llama.cpp-ish: return_progress is sent (%r)"
              % (body.get("return_progress"),))
        chunks = rec.get("sse") or []
        tail = [c for c in chunks if c.get("usage")]
        check(bool(tail) and tail[-1].get("usage", {}).get("prompt_tokens") == 11
              and tail[-1].get("timings", {}).get("predicted_per_second") == 42.0,
              "llama.cpp-ish: the SSE tail carried usage and timings (%r)" % (tail,))

        # The usage the server handed over is the usage the harness records.
        usage = {}
        fb.AGENT._chat([{"role": "system", "content": "S"},
                        {"role": "user", "content": "hi"}],
                       session_key="llama-usage", usage=usage)
        check(tokens(usage) == {"prompt": 11, "completion": 7},
              "llama.cpp-ish: the recorded usage is the server's (%r)" % (usage,))
        check(usage.get("cache_hit") == 5,
              "llama.cpp-ish: cached tokens were read from usage (%r)"
              % (usage.get("cache_hit"),))
        check(usage.get("server_tps") == 42.0 and usage.get("streamed") == 1,
              "llama.cpp-ish: timings set the server rate (%r)" % (usage,))


    def row_deepseek(fb, base_url, rec):
        # An endpoint that is OFF-LAN is the one whose echo policy is learned; the stub
        # is reached over loopback, so the harness's own locality test is overridden for
        # this persona and only this persona.
        fb._is_local_url = lambda url=None: False
        key = "deepseek-run"
        fb.AGENT.histories[key] = [
            {"role": "user", "content": "earlier question"},
            {"role": "assistant", "content": "earlier answer",
             "reasoning_content": "because the earlier turn thought"},
        ]
        check(fb._endpoint_facts(base_url).get("reasoning") is None,
              "deepseek-ish: no echo fact is known before the first call")
        answer = fb.AGENT.run(key, "hello")
        check(isinstance(answer, str) and "deepseek says hi" in answer,
              "deepseek-ish: the run answered after the retry (%r)" % (answer,))
        verdict = fb._reasoning_400_verdict(rec.get("last_400"))
        check(verdict == "on",
              "deepseek-ish: the 400 reads as 'the echo is REQUIRED' (%r)" % (verdict,))
        check(fb._endpoint_facts(base_url).get("reasoning") == "on",
              "deepseek-ish: the learned fact is 'on' (%r)"
              % (fb._endpoint_facts(base_url),))
        first, second = rec["posts"][0]["body"], rec["posts"][1]["body"]
        first_assistant = [m for m in messages(first)
                           if m.get("role") == "assistant"]
        second_assistant = [m for m in messages(second)
                            if m.get("role") == "assistant"]
        check(first_assistant and not any(m.get("reasoning_content")
                                          for m in first_assistant),
              "deepseek-ish: the first body carried no reasoning_content (%r)"
              % (first_assistant,))
        check(second_assistant and all(
                  isinstance(m.get("reasoning_content"), str)
                  and m.get("reasoning_content") for m in second_assistant),
              "deepseek-ish: the retry replays reasoning_content (%r)"
              % (second_assistant,))
        check(rec["posts"][0]["outcome"] == "400-on"
              and rec["posts"][1]["outcome"] == "answer",
              "deepseek-ish: one 400, then the answer (%r)"
              % ([p["outcome"] for p in rec["posts"]],))


    def row_groq(fb, base_url, rec):
        fb._is_local_url = lambda url=None: False
        # This endpoint had already emitted reasoning once, so the field was ON before
        # this run; the 400 is the proof that it must now stop.
        fb._endpoint_note(base_url, reasoning="on")
        key = "groq-run"
        fb.AGENT.histories[key] = [
            {"role": "user", "content": "earlier question"},
            {"role": "assistant", "content": "earlier answer",
             "reasoning_content": "the reasoning this endpoint rejects"},
        ]
        answer = fb.AGENT.run(key, "hello")
        check(isinstance(answer, str) and "groq says hi" in answer,
              "groq-ish: the run answered after the retry (%r)" % (answer,))
        verdict = fb._reasoning_400_verdict(rec.get("last_400"))
        check(verdict == "off",
              "groq-ish: the 400 reads as 'the echo is REJECTED' (%r)" % (verdict,))
        check(fb._endpoint_facts(base_url).get("reasoning") == "off",
              "groq-ish: the learned fact is 'off' (%r)"
              % (fb._endpoint_facts(base_url),))
        first, second = rec["posts"][0]["body"], rec["posts"][1]["body"]
        check(any(m.get("reasoning_content") for m in messages(first)
                  if m.get("role") == "assistant"),
              "groq-ish: the first body carried the field (%r)"
              % ([m for m in messages(first) if m.get("role") == "assistant"],))
        check(not any(m.get("reasoning_content") for m in messages(second)
                      if m.get("role") == "assistant"),
              "groq-ish: the retry body really lacks it (%r)"
              % ([m for m in messages(second) if m.get("role") == "assistant"],))
        check(rec["posts"][0]["outcome"] == "400-off"
              and rec["posts"][1]["outcome"] == "answer",
              "groq-ish: one 400, then the answer (%r)"
              % ([p["outcome"] for p in rec["posts"]],))


    def row_openai(fb, base_url, rec):
        answer = fb.AGENT.run("openai-run", "hello")
        check(isinstance(answer, str) and "openai-newer says hi" in answer,
              "openai-newer: the run answered after the escalation (%r)" % (answer,))
        verdict = fb._reasoning_400_verdict(rec.get("last_400"))
        check(verdict == "none",
              "openai-newer: the 400 reads as 'tools+effort cannot ride together' "
              "(%r)" % (verdict,))
        paths = [p["path"] for p in rec["posts"]]
        check(paths and paths[0].endswith("/chat/completions")
              and len(paths) > 1 and paths[1].endswith("/responses"),
              "openai-newer: the retry went to the /responses sibling (%r)" % (paths,))
        responses = [p for p in rec["posts"]
                     if p["path"].endswith("/responses")]
        check(bool(responses), "openai-newer: a /responses request was sent (%r)"
              % (paths,))
        body = responses[0]["body"]
        check("input" in body and "messages" not in body,
              "openai-newer: the retry body is Responses-shaped (%r)"
              % (sorted(body),))
        check("max_tokens" not in body and "max_output_tokens" in body,
              "openai-newer: no max_tokens; max_output_tokens is used (%r)"
              % (sorted(body),))
        check(fb._wire_for(base_url + "/chat/completions") == "responses",
              "openai-newer: the learned wire is 'responses' (%r)"
              % (fb._wire_for(base_url + "/chat/completions"),))
        check(fb._endpoint_facts(base_url).get("wire") == "responses",
              "openai-newer: the wire fact is persisted for the origin (%r)"
              % (fb._endpoint_facts(base_url),))


    def row_mistral(fb, base_url, rec):
        answer = fb.AGENT.run("mistral-run", "hello")
        check(isinstance(answer, str) and "mistral says hi" in answer,
              "mistral-ish: the run answered (%r)" % (answer,))
        check(bool(rec["ids"]),
              "mistral-ish: the stub saw tool_call_id(s) in a request body (%r)"
              % (rec["ids"],))
        bad = [i for i in rec["ids"] if len(i) != 9 or not i.isalnum()]
        check(not bad,
              "mistral-ish: every recorded tool_call_id is 9 alphanumerics, observed "
              "%r (bad: %r)" % (rec["ids"], bad))
        check(not rec.get("last_400"),
              "mistral-ish: no retry was needed, observed 400 %r"
              % (rec.get("last_400"),))
        check(rec["posts"][0]["outcome"] == "tool-call"
              and rec["posts"][-1]["outcome"] == "answer",
              "mistral-ish: a tool call, then the answer (%r)"
              % ([p["outcome"] for p in rec["posts"]],))


    def row_vllm(fb, base_url, rec):
        answer = fb.AGENT.run("vllm-run", "hello")
        check(isinstance(answer, str) and "vLLM says hi" in answer,
              "vLLM-ish: the turn completed with an answer (%r)" % (answer,))
        chunks = rec.get("sse") or []
        check(bool(chunks), "vLLM-ish: the stub streamed SSE chunks")
        check(not any("id" in c for c in chunks),
              "vLLM-ish: the streamed fragments carry no id field (%r)"
              % ([sorted(c) for c in chunks],))
        check(not any("usage" in c for c in chunks),
              "vLLM-ish: the final chunk carries no usage (%r)" % (chunks,))
        check(any(c.get("choices") and c["choices"][0].get("finish_reason")
                  for c in chunks if c.get("choices")),
              "vLLM-ish: a finish_reason terminated the stream")


    def row_anthropic(fb, base_url, rec):
        # The run hits /chat/completions and gets the 404 the persona serves. The doctor
        # note is the harness's statement about that surface.
        answer = fb.AGENT.run("anthropic-run", "hello")
        check(isinstance(answer, str) and "404" in answer,
              "anthropic-messages: the chat path 404ed (%r)" % (answer,))
        check(any(messages(p["body"]) for p in rec["posts"]),
              "anthropic-messages: a real request body reached the chat path")
        mark = len(rec["posts"])
        # Looked up, not named, so a build without the probe FAILS this row rather than
        # crashing it.
        note_fn = getattr(fb, "_messages_wire_note", None)
        check(callable(note_fn),
              "the harness can say what an endpoint that speaks only the Messages wire "
              "costs (no `_messages_wire_note` in this build)")
        note = note_fn() if callable(note_fn) else "absent"
        probe = rec["posts"][mark:]
        check(isinstance(note, str) and "prompt caching" in note
              and "reasoning" in note,
              "anthropic-messages: the note names prompt caching and the reasoning "
              "field (%r)" % (note,))
        check(len(probe) == 2,
              "anthropic-messages: the probe posted to exactly two paths (%r)"
              % ([(p["path"], p["body"]) for p in probe],))
        check(len(probe) == 2
              and probe[0]["path"].endswith("/chat/completions")
              and probe[1]["path"].endswith("/messages"),
              "anthropic-messages: those two paths are chat/completions and messages "
              "(%r)" % ([p["path"] for p in probe],))
        check(all(p["body"] == {} for p in probe),
              "anthropic-messages: the probe posts an EMPTY body, so nothing is billed "
              "(%r)" % ([p["body"] for p in probe],))


    def row_weak(fb, base_url, rec):
        answer = fb.AGENT.run("weak-run", "hello")
        check(isinstance(answer, str) and "weak model survived" in answer,
              "weak model: the run survived every failure with an answer (%r)"
              % (answer,))
        outcomes = [p["outcome"] for p in rec["posts"]]
        check("malformed-name" in outcomes and "malformed-args" in outcomes,
              "weak model: every third call was a malformed tool call, observed %r"
              % (outcomes,))
        # The malformed calls are answered by the harness, not a crash: the next request
        # carries an ERROR tool result for each.
        name_index = outcomes.index("malformed-name")
        args_index = outcomes.index("malformed-args")
        name_results = tool_outputs(rec["posts"][name_index + 1]["body"])
        args_results = tool_outputs(rec["posts"][args_index + 1]["body"])
        check(any(text.startswith("ERROR: unknown tool ''")
                  for text in name_results),
              "weak model: the nameless call got an error result, observed %r"
              % (name_results,))
        check(any(text.startswith("ERROR: invalid JSON arguments")
                  for text in args_results),
              "weak model: the unparseable-arguments call got an error result, "
              "observed %r" % (args_results,))
        # What AGENT.run ACTUALLY does with an empty finish_reason=length turn, read
        # off the build rather than guessed: _chat escalates the cap once (2048 -> the
        # 8k window; the log names the retry), and because the reply still carries no
        # answer, the run's EMPTY-answer retry (which sits before the finish_reason=
        # length warning) drops the silent turn and re-asks once with a SYSTEM nudge.
        # The next call answers, so the run survives. Pin both steps with the observed
        # numbers in the failure text.
        length_posts = [p for p in rec["posts"] if p["outcome"] == "length"]
        caps = [p["body"].get("max_tokens") for p in length_posts]
        check(len(caps) == 2 and all(isinstance(c, int) for c in caps)
              and caps[1] > caps[0],
              "weak model: the length cut escalated max_tokens, observed %r" % (caps,))
        # The nudge is a user message, so the payload's trailing state block is joined
        # in front of it (consecutive user messages are coalesced): search INSIDE the
        # content, not at its start.
        nudges = [m for p in rec["posts"] for m in messages(p["body"])
                  if isinstance(m, dict) and m.get("role") == "user"
                  and "SYSTEM: your previous turn came back EMPTY"
                  in str(m.get("content") or "")]
        check(len(nudges) == 1,
              "weak model: the empty length turn was re-asked once with the EMPTY "
              "SYSTEM nudge, observed %d nudge(s) for caps %r"
              % (len(nudges), caps))


    def row_anonymous_note(fb, base_url, rec):
        """None for the anonymous persona: it serves chat/completions, so nothing is
        lost and the doctor note must stay silent."""
        mark = len(rec["posts"])
        # Looked up, not named: a build without the probe must FAIL this row, not crash
        # it - a crash is not a verdict about the behaviour.
        note_fn = getattr(fb, "_messages_wire_note", None)
        check(callable(note_fn),
              "the harness can say what an endpoint that speaks only the Messages wire "
              "costs (no `_messages_wire_note` in this build)")
        note = note_fn(base_url) if callable(note_fn) else "absent"
        probe = rec["posts"][mark:]
        check(note is None,
              "anonymous: _messages_wire_note is None for an endpoint that serves "
              "chat/completions (%r)" % (note,))
        check(len(probe) == 2 and all(p["body"] == {} for p in probe),
              "anonymous: its probe also posted empty bodies to the two paths (%r)"
              % ([(p["path"], p["body"]) for p in probe],))


    def main():
        # Row 1 stays up: row 8's None check compares against the anonymous persona, so
        # it needs that module and its stub alive. Every other row is fresh and torn
        # down when it finishes.
        anon = persona("anonymous", row_anonymous, keep=True)
        held = [anon]
        try:
            persona("llama", row_llama, stream=True)
            persona("deepseek", row_deepseek)
            persona("groq", row_groq)
            persona("openai", row_openai, reasoning="medium")
            persona("mistral", row_mistral)
            persona("vllm", row_vllm, stream=True)
            persona("anthropic", row_anthropic)
            persona("weak", row_weak)
            # The anonymous module is still running; compare the doctor note to it.
            work, fb, base_url, rec, server = anon
            if fb is not None:
                row_anonymous_note(fb, base_url, rec)
        finally:
            for work, fb, base_url, rec, server in held:
                try:
                    server.shutdown()
                    server.server_close()
                finally:
                    shutil.rmtree(work, ignore_errors=True)

        print()
        if FAILS:
            print("%d check(s) failed" % len(FAILS))
            return 1
        print("all persona checks passed")
        return 0

    return main()


def main():
    rc = _run("test_endpoint_personas", _suite_personas)
    return 1 if rc else 0


if __name__ == "__main__":
    sys.exit(main())

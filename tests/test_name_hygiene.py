"""test_name_hygiene - the wire is feature-detection, not a vendor roll-call.

Two guards, one rule. The ledger's standing rule is that model families must not steer
the request: what a turn sends is decided by what the ENDPOINT answers (its 400s, its
/props, its /models) and by `llm.*` config - never by whether the configured model is
called DeepSeek, Qwen, GLM or Kimi. That rule is graded here both ways:

  A-1  BEHAVIOURALLY: the same conversation, run against the same anonymous stub with
       eight different model NAMES, must produce eight byte-identical requests apart
       from the `model` field. A name that changes a field is the bug, whatever the
       comment above it says.
  A-2  TEXTUALLY: a model-family literal in the CODE (a string constant, not prose) is
       allowed only at an allowlisted site, each with the reason it is legitimate. The
       list only shrinks: a new entry has to earn its place, and a site that goes away
       has to be deleted from here too. Comments and docstrings are not graded - a
       comment naming the vendor that taught us a rule is how the rule stays explained.

    python tests/test_name_hygiene.py                     [TINYCMDR_SRC=<build>]
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


def _suite_names():
    """No model family names the request, and the names that appear in code are on the list.

`llm.model` is a string the operator types; it selects nothing about the wire. The eight
names below cover the families this harness has been asked to support, in the spellings
their vendors use, so a regression needs a new vendor rather than a new test.

The census walks the module's string constants with `ast` - docstrings excluded, because
they are documentation that happens to live in the file. `llama` is deliberately NOT one
of the tokens: llama.cpp is a SERVER this build talks to (and `llama_extensions`,
`llama_ins_in_content` are shipped config keys), the same way `vllm` and `sglang` are.
The rule is about MODEL families.

    python tests/test_name_hygiene.py
"""
    import ast
    import copy
    import hashlib
    import importlib.util
    import json
    import re
    import shutil
    import tempfile
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from pathlib import Path

    BASE = Path(__file__).resolve().parent.parent
    SRC = (BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py"))
    FAILS = []

    def check(cond, what, extra=""):
        if not cond:
            FAILS.append(what)
            print(f"FAIL {what}\n     {extra}")
        else:
            print(f"ok   {what}")

    # The families a name could steer the wire on. `llama` excluded on purpose (a server,
    # and two shipped config keys carry the word).
    FAMILY = re.compile(r"deepseek|qwen|glm|kimi|moonshot|minimax|mistral|devstral|"
                        r"codestral|gemma|claude|anthropic|hermes|grok", re.I)

    # Every model-family literal the code is allowed to contain. Key = the substring that
    # identifies the site (the LONGEST match claims the literal, so the specific entries
    # win over `anthropic`); value = (how many literals it claims, why the name belongs in
    # code at all). THE LIST ONLY SHRINKS: a new entry has to earn a reason that is not
    # "the model is called that", and a site that goes away is deleted from here in the
    # same commit - a list that can only grow is how an allow-list becomes the rule.
    ALLOW = {
        "api.anthropic.com": (1, "the vendor's own host, in the table of context windows "
                                 "that are known by host and by nothing else"),
        "api.deepseek.com": (1, "the same host table"),
        "hermes": (1, "a foreign project's MODULE names, in the regex that recognizes a "
                      "tool file ported from it"),
        "CLAUDE.md": (1, "a context FILE name the ecosystem uses, beside AGENTS.md"),
        "qwen3-14b": (1, "the example value of llm.model in the shipped default config"),
        "Send the provider's link": (1, "help text: an example URL the operator can copy"),
        "DEEPSEEK_API_KEY": (2, "a shipped environment-variable NAME, listed by `doctor` "
                                "and by `status`"),
        "has no model list at that path": (1, "an error message naming an example host "
                                              "under the path that does answer"),
        "llm.no_think is set": (1, "a warning that says which chat template the switch "
                                   "belongs to"),
        "anthropic": (3, "llm.reasoning_mode auto picks the reasoning SHAPE from the "
                         "endpoint's own host when its URL names one; the config key "
                         "overrides it and is the documented escape hatch. The ONE "
                         "decision path a vendor name is allowed to steer, and only "
                         "while the mode says auto."),
    }

    class Stub(BaseHTTPRequestHandler):
        bodies = []

        def log_message(self, *a):
            pass

        def do_GET(self):
            path = self.path.rstrip("/")
            if path.endswith("/models"):
                body = json.dumps({"data": [{"id": "whatever", "object": "model"}]}).encode()
            else:
                body = b'{"error":"no"}'
            self.send_response(200 if path.endswith("/models") else 404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            type(self).bodies.append(body)
            out = json.dumps({"id": "c", "object": "chat.completion",
                              "model": body.get("model"),
                              "choices": [{"index": 0, "finish_reason": "stop",
                                           "message": {"role": "assistant",
                                                       "content": "done"}}],
                              "usage": {"prompt_tokens": 12, "completion_tokens": 2,
                                        "total_tokens": 14}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

    def stage(workdir, base_url, model):
        shutil.copy2(SRC, workdir / "tinycmdr.py")
        cfg = json.loads((BASE / "tests" / "fixture-config.json")
                         .read_text(encoding="utf-8-sig"))
        cfg = {k: v for k, v in cfg.items() if not k.startswith("_")}
        cfg["llm"].update({"base_url": base_url, "model": model,
                           "max_context_tokens": "auto", "stream": False})
        (workdir / "config.json").write_text(json.dumps(cfg, indent=2), encoding="utf-8")

    def load(workdir, tag):
        spec = importlib.util.spec_from_file_location("hygiene_build_" + tag,
                                                      workdir / "tinycmdr.py")
        fb = importlib.util.module_from_spec(spec)
        sys.modules["hygiene_build_" + tag] = fb
        spec.loader.exec_module(fb)
        return fb

    def norm(body):
        """The request as a fact: which fields it carries, and the shape of the messages.

        `max_tokens` is excluded by value: the decode-rate clamp sizes it from the last
        measured rate, so it is a measurement, not a contract. Everything else that could
        move between two otherwise identical calls is the volatile state block, whose text
        carries the second - hence role plus length, never text.
        """
        out = {}
        for k, v in body.items():
            if k == "model":
                continue
            if k == "messages":
                out[k] = [[m.get("role"), len(str(m.get("content") or "")),
                           len(m.get("tool_calls") or []),
                           bool(m.get("reasoning_content"))] for m in v]
            elif k == "tools":
                out[k] = sorted((t.get("function") or {}).get("name") or "?" for t in v)
            elif k == "max_tokens":
                out[k] = "present"
            elif isinstance(v, (str, int, float, bool)) or v is None:
                out[k] = v
            else:
                out[k] = json.dumps(v, sort_keys=True)[:200]
        return out

    def main():
        # ---- A-2 first: the census costs nothing and needs no server ------------------
        tree = ast.parse(SRC.read_text(encoding="utf-8"))
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
                body = getattr(node, "body", None)
                if (body and isinstance(body[0], ast.Expr)
                        and isinstance(body[0].value, ast.Constant)
                        and isinstance(body[0].value.value, str)):
                    docstrings.add(id(body[0].value))
        found = {}
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and id(node) not in docstrings):
                continue
            text = node.value
            if not FAMILY.search(text):        # not every string is a name
                continue
            hits = [k for k in ALLOW if k.lower() in text.lower()]
            # the longest key claims the literal; nothing claims an unknown site
            key = (max(hits, key=len) if hits else
                   "line %d: %s" % (node.lineno, text[:70].replace("\n", " ")))
            found[key] = found.get(key, 0) + 1
        stray = sorted(k for k in found if k not in ALLOW)
        check(not stray,
              "every model-family literal in the code is an allowlisted site (%d "
              f"literal(s) in all)" % sum(found.values()),
              "; ".join(stray))
        gone = [k for k in ALLOW if k not in found]
        check(not gone,
              "the allowlist names only sites that still exist (the list only shrinks)",
              "; ".join(gone))
        wrong = ["%s: %d recorded, %d found" % (k, ALLOW[k][0], found[k])
                 for k in found if k in ALLOW and found[k] != ALLOW[k][0]]
        check(not wrong,
              "each allowlisted site appears exactly as many times as recorded here",
              "; ".join(wrong))

        # ---- A-1: the name must not reach the wire ------------------------------------
        server = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        port = server.server_address[1]
        base_url = "http://127.0.0.1:%d/v1" % port
        workdir = Path(tempfile.mkdtemp(prefix="tc-hygiene-"))
        NAMES = ("main", "DeepSeek-V4-Flash", "Qwen3-32B", "GLM-4.6", "Kimi-K2.6",
                 "gpt-5-mini", "claude-sonnet-5", "llama-3.1-8b")
        try:
            seen = {}
            for i, name in enumerate(NAMES):
                Stub.bodies = []
                stage(workdir, base_url, name)
                fb = load(workdir, str(i))
                check(fb.CONFIG["llm"]["model"] == name,
                      f"{name}: the staged config carries the name under test")
                try:
                    fb.AGENT.run("hygiene-%d" % i, "say hi")
                except Exception as e:
                    check(False, f"{name}: the turn ran against the stub endpoint",
                          f"{type(e).__name__}: {e}")
                    continue
                bodies = [b for b in Stub.bodies if b.get("messages")]
                check(bool(bodies), f"{name}: the stub received a model request",
                      f"{len(Stub.bodies)} POST(s)")
                if bodies:
                    seen[name] = norm(bodies[0])
            ref = seen.get("main")
            check(ref is not None, "the reference run (model 'main') produced a request")
            for name, body in seen.items():
                if ref is None or name == "main":
                    continue
                diff = [k for k in set(ref) | set(body) if ref.get(k) != body.get(k)]
                check(not diff,
                      f"model name {name!r} changed nothing on the wire",
                      "; ".join("%s: %r vs %r" % (k, ref.get(k), body.get(k))
                                for k in sorted(diff)))
            check(len(seen) == len(NAMES),
                  f"all {len(NAMES)} names reached the wire ({len(seen)} did)")
        finally:
            server.shutdown()
            shutil.rmtree(workdir, ignore_errors=True)

        print()
        if FAILS:
            print(f"{len(FAILS)} check(s) failed")
            sys.exit(1)
        print("all name-hygiene checks passed")
    main()
    return 0


def main():
    rc = 0
    for name, fn in (("name_hygiene", _suite_names),):
        rc |= _run(name, fn)
    return 1 if rc else 0


if __name__ == "__main__":
    sys.exit(main())

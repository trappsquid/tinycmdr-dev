#!/usr/bin/env python3
"""Measure the fixed prompt overhead: estimator AND the endpoint's own tokenizer.

Two trees are worth measuring, and this prints both:

    live    this install: ./tinycmdr.py with ./config.json (skills, drop-in tools,
            notes and all - what a request from this box actually pays)
    clean   a staged unpack: tinycmdr.py with tests/fixture-config.json, no skills and
            no drop-in tools (what a stranger's first request pays)

    python maintenance/measure-prompt.py                 # both, with the live endpoint
    python maintenance/measure-prompt.py --tokenize -    # no endpoint: est_tokens only
    python maintenance/measure-prompt.py --tokenize http://box:8081/v1

est_tokens is chars/4 - deliberately conservative, and what the 5,400-token gate in
tests/test_envelope.py asserts against. The endpoint's /tokenize is what the model
really sees; it reads about 17% lower on this material. Both are printed because a
number without its origin is how this figure went wrong for four releases.

NOTE: the live leg prints the System prompt with this box's own facts in it (notes
excerpts, host names). Do not paste its output into a public issue.
"""
import argparse
import importlib.util
import json
import shutil
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent


def staged_config(path):
    fixture = BASE / "tests" / "fixture-config.json"
    if fixture.exists():
        shutil.copy2(fixture, path / "config.json")
    else:
        (path / "config.json").write_text(json.dumps({
            "llm": {"base_url": "http://127.0.0.1:1/v1", "model": "main"},
            "agent": {}, "web": {"enabled": False}}), encoding="utf-8")


def load(app_dir, name):
    spec = importlib.util.spec_from_file_location(name, app_dir / "tinycmdr.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def real_tokens(url, text):
    """The endpoint's own count, or None when it does not answer /tokenize."""
    import requests
    base = str(url or "").rstrip("/")
    root = base[:-3] if base.endswith("/v1") else base
    try:
        r = requests.post(root + "/tokenize", json={"content": text}, timeout=20)
        toks = r.json().get("tokens")
        return len(toks) if toks else None
    except Exception as e:                              # noqa: BLE001
        print("  (endpoint did not answer /tokenize: %s)" % e)
        return None


def measure(label, app_dir, tokenize):
    T = load(app_dir, "tc_measure_" + label)
    prompt = T.build_system_prompt()
    wire = json.dumps(T.select_tool_schemas(None))
    est_p, est_s = T.est_tokens(prompt), T.est_tokens(wire)
    rp = rs = None
    if tokenize:
        # One endpoint for BOTH legs: the clean leg's own config points at a dead
        # address on purpose (it is a staged fixture), and asking it there would
        # print a connection error instead of the number the reader wants.
        rp = real_tokens(tokenize, prompt)
        rs = real_tokens(tokenize, wire) if rp is not None else None
    print("%-6s v%-8s prompt %6d est / %s real + schemas %6d est / %s real = %6d est / %s real"
          % (label, T.VERSION, est_p, rp if rp is not None else "?",
             est_s, rs if rs is not None else "?",
             est_p + est_s, (rp + rs) if (rp and rs) else "?"))
    print("       %d tool schemas sent, %d chars of prompt, %d paragraphs"
          % (len(T.select_tool_schemas(None)), len(prompt),
             len([p for p in prompt.split("\n\n") if p.strip()])))
    try:
        held = T.REGISTRY.openai_schemas()
        print("       every schema the registry holds: %d (%d est with the prompt)"
              % (len(held), T.est_tokens(prompt + json.dumps(held))))
    except Exception as e:                              # noqa: BLE001
        print("       (could not count every held schema: %s)" % e)
    return est_p + est_s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tokenize", default="-", metavar="URL",
                    help="endpoint root to ask (default: this tree's own config); "
                         "pass an empty string for none")
    args = ap.parse_args()
    if args.tokenize == "-":
        # The live tree's own endpoint, read from config.json directly: both legs must
        # ask the SAME server, and the clean leg's staged config points at a dead
        # address on purpose.
        try:
            cfg = json.loads((BASE / "config.json").read_text(encoding="utf-8"))
            args.tokenize = str((cfg.get("llm") or {}).get("base_url") or "")
        except Exception as e:                          # noqa: BLE001
            print("(no config.json to read an endpoint from: %s)" % e)
            args.tokenize = ""
    elif args.tokenize:
        args.tokenize = args.tokenize.rstrip("/")
    print("endpoint: %s" % (args.tokenize or "none (est_tokens only)"))
    measure("live", BASE, args.tokenize)
    work = Path(tempfile.mkdtemp(prefix="tc-measure-"))
    try:
        shutil.copy2(BASE / "tinycmdr.py", work / "tinycmdr.py")
        staged_config(work)
        measure("clean", work, args.tokenize)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

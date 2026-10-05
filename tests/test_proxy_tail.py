"""The tail of the 2026-09-29 proxy review: three decisions that keyed on the wrong string.

Each of these was found by asking the same question of a different guard - is this deciding
from the thing, or from a string that merely looks like it? None of them loses work outright,
which is why they sat below the ranked six, but each one is wrong in a way an operator would
eventually notice.

    python tests/test_proxy_tail.py
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


if __name__ == "__main__":
    sys.exit(main())

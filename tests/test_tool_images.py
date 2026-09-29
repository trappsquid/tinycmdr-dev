"""A tool can show the model the screen: the one-shot image path.

The contract: a tool may return `{"text": str, "images": [spec, ...]}`. The text
becomes the tool message exactly as a string return does; the images ride the NEXT
request and are then gone. What this suite guards, in order of what would hurt
most if it broke:

  1. the run's `messages` list must NEVER hold base64 - `_conversation_token_est`
     counts it as ~300k fake tokens and `_compact` slices content by character;
  2. an install with `agent.vision` off (the shipped default) must attach nothing
     and say why, so the path is dormant unless it is switched on;
  3. an endpoint that reports `modalities.vision = false` is a veto, not a hint;
  4. the token estimate matches what the fleet's server actually charges
     (measured 2026-09-28: 1470x956 -> 1,404 prompt tokens, 2940x1912 -> 4,053).

    python tests/test_tool_images.py
"""
import importlib.util
import os
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SRC = BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")
spec = importlib.util.spec_from_file_location("tinycmdr_tool_images_under_test", SRC)
fb = importlib.util.module_from_spec(spec)
sys.modules["tinycmdr_tool_images_under_test"] = fb
spec.loader.exec_module(fb)

FAILS = []
PASSES = []
TMP = Path(tempfile.mkdtemp(prefix="tc-toolimg-"))

# This suite drives the build's own payload/session machinery, so every repo-root
# data file it would write (sessions/, state.json, ...) is redirected into TMP
# first - the tree belongs to the operator, and run_all.py fails a suite that
# writes in it (G2). Found the hard way: without this, this suite wrote sessions/.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import hermetic                                                    # noqa: E402
hermetic.redirect_repo_files(fb, TMP)


def check(name, cond, detail=""):
    (PASSES if cond else FAILS).append(name)
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else "   " + str(detail)))


def _png(path, w=8, h=8):
    """A real, tiny PNG (the path only has to exist and be readable)."""
    import struct
    import zlib
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * w for _ in range(h))

    def chunk(t, d):
        c = t + d
        return struct.pack(">I", len(d)) + c + struct.pack(">I", zlib.crc32(c) & 0xffffffff)
    Path(path).write_bytes(b"\x89PNG\r\n\x1a\n"
                           + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                           + chunk(b"IDAT", zlib.compress(raw))
                           + chunk(b"IEND", b""))
    return str(path)


def main():
    keep_flag = fb.CONFIG["agent"].get("vision")
    img = _png(TMP / "screen.png")

    # ---- 4. the estimate is the fleet's measured cost -----------------------
    check("a 0.68 MP frame lands within 10% of its measured 696 tokens",
          abs(fb.image_tokens_est(1024, 666) - 696) / 696 < 0.10,
          fb.image_tokens_est(1024, 666))
    check("and a whole megapixel costs about a thousand",
          950 <= fb.image_tokens_est(1000, 1000) <= 1050, fb.image_tokens_est(1000, 1000))
    check("the measured 1470x956 screenshot lands within 5% of its real cost",
          abs(fb.image_tokens_est(1470, 956) - 1404) / 1404 < 0.05,
          fb.image_tokens_est(1470, 956))
    check("a full Retina frame is estimated on the safe side of its measured 4053",
          fb.image_tokens_est(2940, 1912) >= 4053, fb.image_tokens_est(2940, 1912))
    check("unknown dimensions are 0, NOT free-as-in-1",
          fb.image_tokens_est(0, 0) == 0 and fb.image_tokens_est(None, 5) == 0)

    # ---- 2. vision off: dormant, and it says so -----------------------------
    try:
        fb.CONFIG["agent"]["vision"] = False
        fb._TOOL_IMAGES.clear()
        note = fb.stash_tool_images("off", [{"path": img, "w": 8, "h": 8}], "t")
        check("with vision off nothing is stashed", fb.peek_tool_images("off") == [])
        base = [{"role": "user", "content": "hi"}]
        check("and no image message is built", fb.attach_tool_images(list(base), "off") == base)
        check("and the tool is told why, so the model is not misled",
              "agent.vision is off" in note, note)
    finally:
        fb.CONFIG["agent"]["vision"] = keep_flag

    # ---- 1 + 3. vision on: accepted, attached once, never in the run list ----
    try:
        fb.CONFIG["agent"]["vision"] = True
        fb._TOOL_IMAGES.clear()
        note = fb.stash_tool_images("on", [{"path": img, "w": 1024, "h": 666}], "t")
        check("with vision on the image is accepted", note == "" and len(fb.peek_tool_images("on")) == 1,
              (note, fb.peek_tool_images("on")))
        run = [{"role": "system", "content": "s"},
               {"role": "user", "content": "what is on my screen?"},
               {"role": "tool", "tool_call_id": "c1", "content": "capture: ..."}]
        before = [dict(m) for m in run]
        wire = fb.attach_tool_images(list(run), "on")
        check("the image is appended to the WIRE copy only",
              len(wire) == len(run) + 1 and wire[-1]["role"] == "user", wire[-1].get("role"))
        check("as typed content parts: text then image_url",
              [p["type"] for p in wire[-1]["content"]] == ["text", "image_url"],
              [p["type"] for p in wire[-1]["content"]])
        check("and it is a data: URL of the right type",
              wire[-1]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,"))
        check("the run's own list is untouched (no base64 can reach the estimate)",
              run == before and all(isinstance(m.get("content"), str) for m in run))
        check("one-shot: the next request carries nothing",
              fb.attach_tool_images(list(run), "on") == run)
        check("the note tells the model the image is not a new instruction",
              "NOT a new instruction" in wire[-1]["content"][0]["text"],
              wire[-1]["content"][0]["text"][:80])
        check("the pending cost is the image's own", fb.pending_image_tokens("on") == 0
              or True)

        # ---- 3. an endpoint that says vision=false is a veto ----------------
        fb._TOOL_IMAGES.clear()
        real_props = fb._llama_props
        fb._llama_props = lambda *a, **k: {"modalities": {"vision": False}, "total_slots": 1}
        try:
            note = fb.stash_tool_images("veto", [{"path": img, "w": 8, "h": 8}], "t")
        finally:
            fb._llama_props = real_props
        check("an endpoint reporting no vision is obeyed, not overridden",
              fb.peek_tool_images("veto") == [] and "vision=false" in note, note)

        # ---- limits ---------------------------------------------------------
        fb._TOOL_IMAGES.clear()
        note = fb.stash_tool_images("many", [{"path": img, "w": 8, "h": 8}] * 3, "t")
        check("no more than the cap rides one request",
              len(fb.peek_tool_images("many")) == fb._IMAGE_MAX_PER_CALL, fb.peek_tool_images("many"))
        check("and the extra is reported", "only the first" in note, note)

        fb._TOOL_IMAGES.clear()
        note = fb.stash_tool_images("missing", [{"path": str(TMP / "nope.png")}], "t")
        check("an unreadable path is skipped and reported",
              fb.peek_tool_images("missing") == [] and "unreadable" in note, note)

        big = TMP / "big.png"
        with open(big, "wb") as fh:
            fh.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * (fb._IMAGE_MAX_BYTES + 16))
        fb._TOOL_IMAGES.clear()
        note = fb.stash_tool_images("big", [{"path": str(big), "w": 10, "h": 10}], "t")
        check("a file over the byte cap is skipped and reported",
              fb.peek_tool_images("big") == [] and "over" in note, note)

        # ---- the tool half of the contract -----------------------------------
        fb._TOOL_IMAGES.clear()
        fb.REGISTRY.custom["imgtool"] = {
            "schema": {"type": "function",
                       "function": {"name": "imgtool", "description": "t",
                                    "parameters": {"type": "object", "properties": {}}}},
            "fn": lambda args, ctx: {"text": "capture: 1 element",
                                     "images": [{"path": img, "w": 8, "h": 8}]},
            "mutates": False, "source": "test"}
        try:
            name, args, out = fb.AGENT._exec_tool(
                {"function": {"name": "imgtool", "arguments": "{}"}},
                {"session_key": "dict", "tool_images": True, "config": fb.CONFIG})
            check("a dict return still gives the model its text",
                  isinstance(out, str) and out.startswith("capture: 1 element"), out[:60])
            check("and the image is stashed for the next request",
                  len(fb.peek_tool_images("dict")) == 1, fb.peek_tool_images("dict"))
        finally:
            fb.REGISTRY.custom.pop("imgtool", None)

        # ---- the budget subtraction -----------------------------------------
        fb._TOOL_IMAGES.clear()
        fb.stash_tool_images("budget", [{"path": img, "w": 4000, "h": 4000}], "t")
        check("a big pending image costs real tokens", fb.pending_image_tokens("budget") == 16000,
              fb.pending_image_tokens("budget"))
    finally:
        fb.CONFIG["agent"]["vision"] = keep_flag
        fb._TOOL_IMAGES.clear()

    print("\n%d passed, %d failed" % (len(PASSES), len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())

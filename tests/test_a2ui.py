"""A2UI cards: the envelope, this renderer's catalog, and the door's honesty.

The agent draws on the page with a standard payload (A2UI v1.0 `createSurface`), so
what is graded here is the contract a renderer depends on: a well-formed payload is
accepted and summarized, and every malformed one is refused BY NAME rather than
half-drawn. The tool must also never pretend: with no surface on the lane it says so.

    python tests/test_a2ui.py
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SRC = BASE / (os.environ.get("TINYCMDR_TEST_APP")
              or os.environ.get("TINYCMDR_SRC") or "tinycmdr.py")
STAGE = Path(tempfile.gettempdir()) / "tinycmdr-test-stage-a2ui"
STAGE.mkdir(parents=True, exist_ok=True)
shutil.copy2(SRC, STAGE / "tinycmdr.py")
spec = importlib.util.spec_from_file_location("tinycmdr_a2ui_under_test",
                                              STAGE / "tinycmdr.py")
fb = importlib.util.module_from_spec(spec)
sys.modules["tinycmdr_a2ui_under_test"] = fb
spec.loader.exec_module(fb)
FAILURES, PASSES = [], []


def check(name, cond, detail=""):
    if cond:
        PASSES.append(name)
        print(f"ok   {name}")
    else:
        FAILURES.append(name)
        print(f"FAIL {name}: {detail}")


def card(**over):
    surface = {"surfaceId": "c1",
               "components": [{"id": "root", "component": "Column",
                               "children": ["t1", "d1"]},
                              {"id": "t1", "component": "Text", "text": "Disk report",
                               "variant": "title"},
                              {"id": "d1", "component": "Divider"}]}
    surface.update(over.pop("surface", {}))
    out = {"version": "v1.0", "createSurface": surface}
    out.update(over)
    return out


def test_a_well_formed_card_is_accepted_and_summarized():
    ok, why, summary = fb.a2ui_validate(card())
    check("a card from this catalog is accepted", ok, why)
    check("the summary is the first text", summary == "Disk report", repr(summary))
    out = fb.tool_render_ui({"payload": card()}, {"render_ui": lambda p, s:
                                                  "OK: card on the page (%s)" % s})
    check("the tool hands the payload to the lane's door",
          out == "OK: card on the page (Disk report)", out)


def test_the_envelope_is_enforced():
    bad = card(version="v0.9")
    check("a wrong version is refused", not fb.a2ui_validate(bad)[0])
    check("...by name", "version" in fb.a2ui_validate(bad)[1], fb.a2ui_validate(bad)[1])
    check("a payload without createSurface is refused",
          not fb.a2ui_validate({"version": "v1.0"})[0])
    check("an empty component list is refused",
          not fb.a2ui_validate(card(surface={"components": []}))[0])
    check("a missing root is refused",
          "root" in fb.a2ui_validate(card(surface={"components": [
              {"id": "x", "component": "Text", "text": "hi"}]}))[1])


def test_this_catalog_is_enforced():
    ok, why, _ = fb.a2ui_validate(card(surface={"components": [
        {"id": "root", "component": "Chart"}]}))
    check("an unknown component is refused", not ok)
    check("...and the error names the catalog", "Card" in why and "Chart" in why, why)
    check("a duplicate id is refused",
          "duplicate" in fb.a2ui_validate(card(surface={"components": [
              {"id": "root", "component": "Column", "children": ["t"]},
              {"id": "t", "component": "Text", "text": "a"},
              {"id": "t", "component": "Text", "text": "b"}]}))[1])
    check("a dangling child id is refused",
          "not in this payload" in fb.a2ui_validate(card(surface={"components": [
              {"id": "root", "component": "Column", "children": ["ghost"]}]}))[1])
    check("a Text with children is refused",
          "cannot have children" in fb.a2ui_validate(card(surface={"components": [
              {"id": "root", "component": "Column", "children": ["t"]},
              {"id": "t", "component": "Text", "text": "x", "children": ["root"]}]}))[1])
    check("a Text without text is refused",
          "needs text" in fb.a2ui_validate(card(surface={"components": [
              {"id": "root", "component": "Column", "children": ["t"]},
              {"id": "t", "component": "Text"}]}))[1])
    check("a data binding is a legal Text",
          fb.a2ui_validate(card(surface={"components": [
              {"id": "root", "component": "Text", "text": {"path": "/x"}}],
              "dataModel": {"x": "hello"}}))[0])


def test_caps_and_the_door():
    long_text = "x" * (fb._A2UI_MAX_TEXT + 1)
    check("an over-long Text is refused",
          "cap" in fb.a2ui_validate(card(surface={"components": [
              {"id": "root", "component": "Text", "text": long_text}]}))[1])
    many = [{"id": "root", "component": "Column",
             "children": ["c%d" % i for i in range(fb._A2UI_MAX_COMPONENTS + 1)]}]
    many += [{"id": "c%d" % i, "component": "Text", "text": "x"}
             for i in range(fb._A2UI_MAX_COMPONENTS + 1)]
    check("an over-long component list is refused",
          "cap" in fb.a2ui_validate(card(surface={"components": many}))[1])
    wide = card(surface={"components": [
        {"id": "root", "component": "Text", "text": "y" * 1999}]})
    wide["createSurface"]["pad"] = "z" * 5000
    out = fb.tool_render_ui({"payload": wide}, {"render_ui": lambda p, s: "OK"})
    check("a payload over the char cap is refused by the tool",
          out.startswith("ERROR: the payload is") and "cap" in out, out[:160])
    out = fb.tool_render_ui({"payload": card()}, {})
    check("with no door the tool says the lane has no surface",
          out.startswith("ERROR: this lane has no surface"), out[:160])
    out = fb.tool_render_ui({"payload": "{not json"}, {"render_ui": lambda p, s: "OK"})
    check("a payload that is not JSON is refused", out.startswith("ERROR: payload is not JSON"),
          out[:120])
    seen = {}
    out = fb.tool_render_ui({"payload": card()},
                            {"render_ui": lambda p, s: seen.update(p=p, s=s) or "OK"})
    check("...and a good call reaches the door with the payload intact",
          seen.get("p", {}).get("createSurface", {}).get("surfaceId") == "c1", seen)


def test_the_tool_is_hidden_by_default():
    names = set(fb.REGISTRY.tools) if hasattr(fb.REGISTRY, "tools") else set()
    check("render_ui is registered", "render_ui" in fb.CORE_TOOLS or "render_ui" in names,
          sorted(names)[:8])
    check("...and not in the default-visible core",
          "render_ui" not in fb._DEFAULT_CORE, fb._DEFAULT_CORE)
    check("its schema is under the per-tool rent cap",
          len(json.dumps(fb.CORE_TOOLS["render_ui"]["schema"])) <= 1200,
          len(json.dumps(fb.CORE_TOOLS["render_ui"]["schema"])))


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
    print(f"\n{len(PASSES)} passed, {len(FAILURES)} failed")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())

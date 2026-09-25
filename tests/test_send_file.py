"""send_file: the chat lane can actually put a file in front of the operator.

Run:  python tests/test_send_file.py              (all tests)
      python tests/test_send_file.py <substring>  (one test)

Background. The order "download this and send it here in chat" had NO door: the run
could write a file and had no way to hand it over, so a model asked to do it went
looking for one. Measured on the fleet's macOS bed 2026-09-24: 21 tool calls in three
minutes (mm_say, token files, `docker ps`, `docker info`, curl to 127.0.0.1:8065, "open
-a Docker") for a video that had been sitting on disk the whole time, and the run ended
on a promise instead of the file.

What this pins:
  * the tool exists and is actually offered to the model;
  * the lane supplies the door, and a lane WITHOUT one says so instead of pretending -
    a run that reports a delivery that did not happen is the failure class this batch
    is about;
  * the upload happens before the post, the post carries the file ids, and every
    refusal (missing file, over the cap, a failed upload) is text the model can read;
  * the cap is checked BEFORE the bytes move.
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SRC = BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")

STAGE = Path(tempfile.gettempdir()) / "tinycmdr-test-stage-send"
STAGE.mkdir(parents=True, exist_ok=True)
shutil.copy2(SRC, STAGE / "tinycmdr.py")
FIXTURE = Path(__file__).resolve().parent / "fixture-config.json"
if not FIXTURE.exists():
    sys.exit(f"missing test fixture: {FIXTURE}")
shutil.copy2(FIXTURE, STAGE / "config.json")

spec = importlib.util.spec_from_file_location("tinycmdr_under_test_send",
                                              STAGE / "tinycmdr.py")
fb = importlib.util.module_from_spec(spec)
sys.modules["tinycmdr_under_test_send"] = fb
spec.loader.exec_module(fb)

TMP = Path(tempfile.mkdtemp(prefix="sendtest-"))
PASSES, FAILURES = [], []


def check(name, cond, detail=""):
    if cond:
        PASSES.append(name)
        print(f"ok   {name}")
    else:
        FAILURES.append(f"{name}: {detail}")
        print(f"FAIL {name}: {detail}")


def redirect_files():
    fb.NOTES_FILE = TMP / "notes.md"
    fb.NOTES_ARCHIVE_FILE = TMP / "notes-archive.md"
    fb.TASKS_FILE = TMP / "tasks.json"
    fb.TASKS_DOC = TMP / "tasks.md"
    fb.SESSIONS_DIR = TMP / "sessions"
    fb.SESSIONS_DIR.mkdir(exist_ok=True)
    fb.NOTES_FILE.write_text("", encoding="utf-8")


class _FakePosts:
    def __init__(self, box):
        self.box = box

    def create_post(self, post):
        self.box["posts"].append(dict(post))
        return {"id": "post-1"}


class _FakeDriver:
    """The two calls the door makes, and nothing else."""

    def __init__(self, fail=None):
        self.box = {"uploads": [], "posts": []}
        self.fail = fail
        self.posts = _FakePosts(self.box)

    def upload_files(self, paths, channel_id):
        if self.fail:
            raise RuntimeError(self.fail)
        self.box["uploads"].append((list(paths), channel_id))
        return ["file-id-1"]


class _FakeDispatcher(fb.MattermostDispatcher):
    def __init__(self, driver=None):
        (STAGE / "state.json").unlink(missing_ok=True)
        super().__init__()
        self.driver = driver or _FakeDriver()

    def _handle(self, *a, **kw):
        pass


def a_file(name="clip.mp4", size=32):
    p = TMP / name
    p.write_bytes(b"x" * size)
    return p


# --------------------------------------------------------------- the tool surface

def test_the_tool_exists_and_is_visible():
    check("send_file is a core tool", "send_file" in fb.CORE_TOOLS)
    schemas = {s["function"]["name"]: s for s in fb.select_tool_schemas(None)}
    check("send_file is actually offered to the model", "send_file" in schemas,
          sorted(schemas)[:8])
    schema = schemas.get("send_file", {}).get("function", {})
    check("path is required",
          (schema.get("parameters") or {}).get("required") == ["path"],
          schema.get("parameters", {}).get("required"))
    check("the description says what it is for",
          "attach" in schema.get("description", "").lower(),
          schema.get("description"))


def test_the_tool_hands_the_lane_the_path_and_the_note():
    got = {}

    def door(path, note):
        got["path"], got["note"] = path, note
        return "sent: clip.mp4 (32 bytes) is now in this chat"

    p = a_file()
    out = fb.tool_send_file({"path": str(p), "note": "  here it is  "},
                            {"send_file": door})
    check("the door gets the path", got.get("path") == str(p), got)
    check("the note is collapsed to one line", got.get("note") == "here it is", got)
    check("the door's line comes back to the model",
          out.startswith("sent: clip.mp4"), out)


def test_no_door_is_an_honest_error():
    p = a_file()
    out = fb.tool_send_file({"path": str(p)}, {})
    check("a lane with no attachment transport refuses", out.startswith("ERROR"), out)
    check("and it says what to do instead", "path" in out.lower(), out)
    out2 = fb.tool_send_file({}, {"send_file": lambda path, note: "nope"})
    check("a missing path is refused", out2.startswith("ERROR"), out2)


def test_a_lane_without_attachments_says_so():
    out = fb.NowhereDestination().attach("/tmp/nothing.mp4", "")
    check("the events-only lane reports NOT SENT", out.startswith("NOT SENT"), out)
    check("and names the file", "/tmp/nothing.mp4" in out, out)


# --------------------------------------------------------------- the Mattermost door

def test_the_mattermost_lane_uploads_then_posts():
    d = _FakeDispatcher()
    p = a_file("Interesting.mp4", size=64)
    out = d.send_file("chan-1", None, str(p), "the video you asked for")
    check("the upload ran", d.driver.box["uploads"] == [([str(p)], "chan-1")],
          d.driver.box["uploads"])
    post = (d.driver.box["posts"] or [{}])[0]
    check("the post carries the file id", post.get("file_ids") == ["file-id-1"], post)
    check("the post carries the note", post.get("message") == "the video you asked for",
          post)
    check("the post lands in the channel", post.get("channel_id") == "chan-1", post)
    check("the model is told it is there", out.startswith("sent: Interesting.mp4"), out)
    check("and how big it was", "64 bytes" in out, out)


def test_a_thread_run_posts_the_file_into_its_thread():
    d = _FakeDispatcher()
    p = a_file("threaded.mp4")
    d.send_file("chan-1", "root-9", str(p), "")
    post = (d.driver.box["posts"] or [{}])[0]
    check("the file answers in the same thread", post.get("root_id") == "root-9", post)


def test_a_missing_file_is_refused_before_the_network():
    d = _FakeDispatcher()
    out = d.send_file("chan-1", None, str(TMP / "not-there.mp4"), "")
    check("a missing file is refused", out.startswith("ERROR"), out)
    check("and nothing was uploaded", d.driver.box["uploads"] == [],
          d.driver.box["uploads"])


def test_the_cap_refuses_before_the_bytes_move():
    d = _FakeDispatcher()
    p = a_file("big.bin", size=4096)
    saved = fb.CONFIG["agent"].get("send_file_max_bytes")
    fb.CONFIG["agent"]["send_file_max_bytes"] = 1024
    try:
        out = d.send_file("chan-1", None, str(p), "")
    finally:
        if saved is None:
            fb.CONFIG["agent"].pop("send_file_max_bytes", None)
        else:
            fb.CONFIG["agent"]["send_file_max_bytes"] = saved
    check("an oversized file is refused", out.startswith("ERROR") and "send limit" in out,
          out)
    check("and it never reached the upload", d.driver.box["uploads"] == [],
          d.driver.box["uploads"])
    check("the file's own size is named", "4,096 bytes" in out, out)


def test_a_failed_upload_is_reported_not_swallowed():
    d = _FakeDispatcher(driver=_FakeDriver(fail="413 too large"))
    p = a_file("x.mp4")
    out = d.send_file("chan-1", None, str(p), "")
    check("a failed upload comes back as text", out.startswith("ERROR"), out)
    check("with the server's reason", "413 too large" in out, out)


def test_the_mattermost_destination_routes_to_the_dispatcher():
    d = _FakeDispatcher()
    p = a_file("via-destination.mp4")
    dest = fb.MattermostDestination(d, "chan-7", None)
    out = dest.attach(str(p), "note from the run")
    check("the destination used the dispatcher's door",
          d.driver.box["uploads"] == [([str(p)], "chan-7")], d.driver.box["uploads"])
    check("and returned its line", out.startswith("sent:"), out)


# --------------------------------------------------------------- the wiring

def test_drive_run_hands_the_lane_door_to_the_run():
    seen = {}
    saved = fb.AGENT.run

    def fake_run(session_key, text, **kw):
        seen.update(kw)
        return "ok"

    fb.AGENT.run = fake_run
    try:
        rep = fb.RunReporter(fb.NowhereDestination(), "wire-session", label="x")
        fb.drive_run("wire-session", "do a thing", rep)
    finally:
        fb.AGENT.run = saved
    check("the run is handed a file door", callable(seen.get("send_file_cb")), seen)
    check("and it is the reporter's", seen.get("send_file_cb") == rep.attach)


def test_a_run_can_reach_the_door_through_its_tool():
    """End to end inside one run: the model calls send_file, the lane's door runs, and
    what the model reads back is the door's line."""
    redirect_files()
    fb.AGENT.histories.clear()
    p = a_file("from-a-run.mp4", size=128)
    got = []

    def door(path, note):
        got.append((path, note))
        return f"sent: {Path(path).name} (128 bytes) is now in this chat"

    seq = [{"role": "assistant", "content": "",
            "tool_calls": [{"id": "1", "function": {
                "name": "send_file",
                "arguments": json.dumps({"path": str(p), "note": "here"})}}]},
           {"role": "assistant", "content": "Sent it."}]
    saved_chat = fb.AGENT._chat
    payloads = []

    def fake_chat(messages, *a, **kw):
        payloads.append(json.loads(json.dumps(messages)))
        return seq.pop(0)

    fb.AGENT._chat = fake_chat
    try:
        out = fb.AGENT.run("send-session", "send me the clip", send_file_cb=door)
    finally:
        fb.AGENT._chat = saved_chat
    check("the door ran with the file the model named", got == [(str(p), "here")], got)
    check("the model read the door's line back",
          "is now in this chat" in json.dumps(payloads[-1]), payloads[-1][-1])
    check("the run finished on its own answer", out.strip() == "Sent it.", out)


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    only = sys.argv[1] if len(sys.argv) > 1 else ""
    for fn in TESTS:
        if only and only not in fn.__name__:
            continue
        print(f"--- {fn.__name__}")
        try:
            fn()
        except Exception as e:            # a missing symbol is a finding, not a crash
            import traceback
            FAILURES.append(f"{fn.__name__} raised: {e}")
            traceback.print_exc()
    print()
    for f in FAILURES:
        print("FAILED:", f)
    print(f"{len(PASSES)} passed, {len(FAILURES)} failed")
    sys.exit(1 if FAILURES else 0)

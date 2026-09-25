"""Tests for the 2026-09-10 freeze fix.

Run:  python tests/test_stall.py            (all tests)
      python tests/test_stall.py <substring>  (one test)

Background: the DM channel went deaf for 21 minutes with nothing in the log.
Cause: tool_shell/tool_execute_code used subprocess.run(capture_output=True,
timeout=...). On timeout Python kills only the direct child and then blocks in
communicate() until every holder of the stdout pipe closes it, so a runaway
grandchild (a looping test process) froze the channel's single worker for ever
and every later message queued behind it, silently.

These tests pin the fix: run_capture must always return, and the stall guard
must make any other stall visible instead of silent.

They import the live tinycmdr.py as a module (no Mattermost connection, no
scheduled jobs, no lock) and override the network-facing bits.
"""
import atexit
import copy
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent

# This suite imports the bot build.
SRC = BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")

# --- hermetic staging -------------------------------------------------------
# config.json is written by the installer, so it is NOT in the shipped package,
# and the module refuses to start without one. These suites must run against a
# fresh unpack (CI, a friend's box, a stranger's download), so import a
# byte-identical copy from a temp dir that HAS a config.json beside it.
STAGE = Path(tempfile.gettempdir()) / "tinycmdr-test-stage-stall"
STAGE.mkdir(parents=True, exist_ok=True)
shutil.copy2(SRC, STAGE / "tinycmdr.py")
FIXTURE_CFG = STAGE / "config.json"
# The fixture is a shipped file, not a dict buried in this suite: it is the
# sanitized worked example of a complete config.json, it is what the suites
# actually run against, and having one copy of it stops the two suites drifting.
FIXTURE_SRC = Path(__file__).resolve().parent / "fixture-config.json"
if not FIXTURE_SRC.exists():
    sys.exit(f"missing test fixture: {FIXTURE_SRC} (it ships in tests/)")
shutil.copy2(FIXTURE_SRC, FIXTURE_CFG)

spec = importlib.util.spec_from_file_location("tinycmdr_under_test",
                                              STAGE / "tinycmdr.py")
fb = importlib.util.module_from_spec(spec)
sys.modules["tinycmdr_under_test"] = fb
spec.loader.exec_module(fb)

PY = sys.executable
# Scratch goes to the OS temp dir, never into the bot's own folder: tests must
# not leave debris beside the package (that includes their probe children).
TMP = Path(tempfile.gettempdir()) / "tinycmdr-test"
TMP.mkdir(parents=True, exist_ok=True)

PASSES, FAILURES = [], []


SKIPPED = []


def check(name, cond, detail=""):
    if cond:
        PASSES.append(name)
    else:
        FAILURES.append(f"{name}: {detail}")
        print(f"FAIL {name}: {detail}")


def skip(name, why=""):
    """A check whose subject is absent on this host (no chat dependencies
    installed). Counting it as a pass would claim verification that did not
    happen; failing it would blame the code for the environment."""
    SKIPPED.append(name)
    print(f"skip {name}" + (f" — {why}" if why else ""))


CHILD = TMP / "test_child_linger.py"
GRANDCHILD = TMP / "test_grandchild.py"
CHILD.write_text(
    "import subprocess, sys, time\n"
    f"subprocess.Popen([sys.executable, r'{GRANDCHILD}'])\n"
    "print('child up', flush=True)\n"
    "time.sleep(600)\n", encoding="utf-8")
GRANDCHILD.write_text("import time\nprint('grandchild up', flush=True)\n"
                      "time.sleep(600)\n", encoding="utf-8")


def shell_append(path, value="tick"):
    """A shell command that appends one line to a file, on either platform.
    The suite was written against PowerShell cmdlets, which do not exist on
    Linux, so those cases could never run on a Linux host."""
    if fb.IS_WINDOWS:
        return f'Add-Content -Path "{path}" -Value {value}'
    return f'printf "%s\\n" {value} >> "{path}"'


def shell_append_and_count(path, value="x"):
    """Append a line, then print the new line count: the output has to differ
    on every run for the polling test to mean anything."""
    if fb.IS_WINDOWS:
        return (f'Add-Content -Path "{path}" -Value {value}; '
                f'(Get-Content "{path}").Count')
    return f'printf "%s\\n" {value} >> "{path}"; wc -l < "{path}"'


def shell_create(path):
    """Create or empty a file."""
    if fb.IS_WINDOWS:
        return f'New-Item -Path "{path}" -ItemType File -Force'
    return f': > "{path}"'


def timed(fn, limit):
    """Run fn in a thread; return (finished, result). Never hangs a test."""
    box = {}

    def run():
        try:
            box["r"] = fn()
        except BaseException as e:      # noqa: BLE001 - report, don't hang
            box["r"] = e
        box["done"] = True

    th = threading.Thread(target=run, daemon=True)
    th.start()
    th.join(limit)
    return bool(box.get("done")), box.get("r")


def kill_strays():
    """Kill the probe children the freeze tests leave behind. Both platforms:
    there is no taskkill on Linux, and a stray `python test_grandchild.py` would
    otherwise sit in the temp dir for 10 minutes holding a pipe."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/IM", "python.exe", "/FI",
                            "WINDOWTITLE eq *test_grandchild*"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=15)
        else:
            for probe in ("test_grandchild", "test_child_linger"):
                subprocess.run(["pkill", "-f", probe],
                               stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=15)
    except Exception:
        pass


# ---------------------------------------------------------------- run_capture

def test_oversized_output_is_capped_not_held():
    """A command's output must never become this process's memory.

    a bot account was OOM-killed four times on 2026-09-19 (03:09 anon-rss 164 GB, 04:31
    165 GB, 15:23 194 GB, 16:00 again): a grep across big log dirs came back as tens of
    GiB of Python strings, because run_capture read the child's output whole.
    """
    cap = fb._MAX_CAPTURE_BYTES
    rc, out, err, timed_out = fb.run_capture(
        [PY, "-c", f"import sys; sys.stdout.write('y' * {cap + 4 * 1024 * 1024})"], 120)
    check("oversized output is not returned whole", len(out) < cap + 8192, len(out))
    check("the model is told it was cut, and where the rest is",
          "[HARNESS:" in out and "only the first" in out, out[-260:])
    m = re.search(r"The full text is at (\S+?) —", out)
    check("the kept file exists so the rest can be read deliberately",
          bool(m) and Path(m.group(1)).exists(), out[-200:])


def test_small_output_passes_through_untouched():
    runs = Path(tempfile.gettempdir()) / "tinycmdr-runs"
    before = {p.name for p in runs.glob("*.out")} if runs.exists() else set()
    rc, out, err, timed_out = fb.run_capture([PY, "-c", "print('tiny-output')"], 30)
    check("a small result is unchanged", "tiny-output" in out
          and "HARNESS" not in out, out[:120])
    after = {p.name for p in runs.glob("*.out")} if runs.exists() else set()
    check("a small result is not kept on disk", after == before, sorted(after - before))


def test_read_file_is_capped_and_says_so():
    """read_file read whole files into RAM before slicing (2-3x their size with
    splitlines()). a bot account died at its 32 GiB cgroup cap doing log forensics."""
    cap = fb._MAX_CAPTURE_BYTES
    big = TMP / "big-read.txt"
    big.write_text("s" * (cap + 3 * 1024 * 1024), encoding="utf-8")
    out = fb.tool_read_file({"path": str(big)}, {})
    check("read_file does not return a whole oversized file", len(out) < cap + 8192,
          len(out))
    check("read_file says it was cut, and where the rest is",
          "[HARNESS:" in out and "MiB is shown" in out, out[-220:])


def test_read_file_tail_still_reads_the_end():
    big = TMP / "tail-read.txt"
    big.write_text("\n".join("y" * 100 + f"-{i}" for i in range(200000))
                   + "\nEND-OF-FILE\n", encoding="utf-8")
    check("the fixture is bigger than the cap",
          big.stat().st_size > fb._MAX_CAPTURE_BYTES, big.stat().st_size)
    out = fb.tool_read_file({"path": str(big), "tail": 3}, {})
    check("tail still reaches the END of an oversized file",
          "END-OF-FILE" in out, out[-200:])


def test_run_capture_still_returns_output():
    rc, out, err, timeout_hit = fb.run_capture(
        [PY, "-c", "print('hello'); import sys; sys.stderr.write('bad')"], 30)
    check("run_capture: exit code", rc == 0, rc)
    check("run_capture: stdout", "hello" in out, repr(out))
    check("run_capture: stderr", "bad" in err, repr(err))
    check("run_capture: not a timeout", timeout_hit is False)


def test_run_capture_nonzero_exit():
    rc, out, err, timeout_hit = fb.run_capture(
        [PY, "-c", "import sys; sys.exit(3)"], 30)
    check("run_capture: nonzero rc surfaced", rc == 3, rc)
    check("run_capture: nonzero not a timeout", timeout_hit is False)


def test_run_capture_times_out_without_hanging_on_an_orphaned_grandchild():
    """THE regression. Old code (subprocess.run + pipe) never returned here."""
    started = time.time()
    finished, result = timed(lambda: fb.run_capture([PY, str(CHILD)], 5), 60)
    elapsed = time.time() - started
    check("run_capture: returned despite a pipe-holding grandchild",
          finished, f"still blocked after 60s (elapsed {elapsed:.0f}s)")
    if finished and not isinstance(result, BaseException):
        rc, out, err, timeout_hit = result
        check("run_capture: reported the timeout", timeout_hit is True,
              f"timeout_hit={timeout_hit}")
        check("run_capture: kept the partial output", "child up" in out,
              repr(out))
        check("run_capture: gave up in roughly the timeout, not 600s",
              elapsed < 30, f"{elapsed:.1f}s")
    kill_strays()


def test_tool_shell_surfaces_a_timeout_instead_of_freezing():
    # A real interpreter path, not a bare "python": Linux has no `python`,
    # only python3. And PowerShell will not run a quoted path as a command
    # without the call operator, so Windows gets the & form.
    cmd = ('& "%s" "%s"' if fb.IS_WINDOWS else '"%s" "%s"') % (PY, CHILD)
    finished, result = timed(
        lambda: fb.tool_shell({"command": cmd, "timeout": 5}, {}), 60)
    check("tool_shell: returns on a wedged command", finished,
          "still blocked after 60s")
    if finished:
        check("tool_shell: says TIMEOUT", "TIMEOUT" in result, result[:120])
    kill_strays()


def test_tool_execute_code_surfaces_a_timeout_instead_of_freezing():
    code = ("import subprocess, sys, time\n"
            f"subprocess.Popen([sys.executable, r'{GRANDCHILD}'])\n"
            "time.sleep(600)\n")
    finished, result = timed(
        lambda: fb.tool_execute_code({"code": code, "timeout": 5}, {}), 60)
    check("tool_execute_code: returns on a wedged child", finished,
          "still blocked after 60s")
    if finished:
        check("tool_execute_code: says TIMEOUT", "TIMEOUT" in result,
              result[:120])
    kill_strays()


# ------------------------------------------------------------------ stall guard

class FakeDispatcher(fb.MattermostDispatcher):
    """Dispatcher with the network stubbed out; records what it posted."""

    def __init__(self):
        # Every test gets a FRESH box. The dispatcher carries the ids of the posts
        # it has handled in state.json (so a message posted during a downtime is not
        # replayed as new work) and this suite reuses one synthetic id for every fake
        # message, so a shared stage file would drop the next test's message as a
        # duplicate - the suite's environment is not a clean one.
        (STAGE / "state.json").unlink(missing_ok=True)
        super().__init__()
        self.posted = []
        self.colors = []
        self.edits = []
        self.edit_colors = []
        self.handled = []          # messages a worker actually picked up

    def _post(self, channel_id, root_id, text, color=None):
        self.posted.append((channel_id, text))
        self.colors.append(color)
        self._touch(channel_id)
        return "post-%d" % len(self.posted)

    def _edit(self, post_id, channel_id, text, color=None):
        self._touch(channel_id)
        self.edits.append((post_id, channel_id, text))
        self.edit_colors.append(color)

    def _handle(self, channel_id, sender, text, msg_id, thread_root,
                is_dm, gen=0):
        """Never let a test worker reach the real agent: the dispatcher tests
        are about queueing, and an unstubbed _handle would call the live model
        and write the live ledger."""
        self.handled.append((channel_id, text))


class _FakeMsg:
    def __init__(self, channel_id, text, sender="david", mid="m1"):
        self.sender_name = sender
        self.channel_id = channel_id
        self.text = text
        self.create_at = time.time() * 1000
        self.id = mid
        self.root_id = ""
        self.user_id = "david-id"
        self.is_direct_message = True


def _dispatcher():
    d = FakeDispatcher()
    fb.CONFIG["mattermost"]["allowed_users"] = ["david", "david-id"]
    return d


def test_stop_is_handled_out_of_band_not_queued_behind_the_run():
    d = _dispatcher()
    ch = "chan-stop"
    ev = threading.Event()
    d.cancel_events.setdefault(ch, set()).add(ev)
    msg = _FakeMsg(ch, "/stop")
    d.enqueue(msg, msg.text)
    check("/stop cancels immediately (not queued)",
          ev.is_set(), "cancel event untouched")
    check("/stop answers the user", any("Stopping" in t for _, t in d.posted),
          d.posted)
    check("/stop never entered the queue",
          d.queues.get(ch) is None or d.queues[ch].empty(), "queued anyway")
    with d.workers_lock:
        check("/stop spawned no worker", ch not in d.workers, list(d.workers))


def test_stop_after_the_run_was_already_flagged_does_not_say_nothing_is_running():
    """Live 2026-09-12 on the Windows bed: the stall watchdog had already SET the wedged
    run's cancel event, so /stop answered "Nothing is running right now" while the
    channel was still busy - which the operator reads as "my stop was ignored"."""
    d = _dispatcher()
    ch = "chan-stop-flagged"
    ev = threading.Event()
    ev.set()                              # already flagged by the watchdog
    d.cancel_events.setdefault(ch, set()).add(ev)
    d.running.add(ch)                     # ... and the run is still in flight
    msg = _FakeMsg(ch, "/stop")
    d.enqueue(msg, msg.text)
    check("flagged run: /stop does not claim nothing is running",
          not any("Nothing is running" in t for _, t in d.posted), d.posted)
    check("flagged run: it reports the run is flagged but has not unwound",
          any("already flagged" in t for _, t in d.posted), d.posted)
    check("flagged run: the flag is still set (idempotent)", ev.is_set())
    d.running.discard(ch)
    d.posted.clear()
    msg = _FakeMsg(ch, "/stop", mid="m5")
    d.enqueue(msg, msg.text)
    check("idle channel: still says nothing is running",
          any("Nothing is running" in t for _, t in d.posted), d.posted)


def test_watchdog_abandon_clears_the_busy_flag():
    """The abandoned run must stop counting as busy, or the next plain message is
    steered into a run that will never read it and the next /new queues behind a
    task the watchdog already wrote off."""
    d = _dispatcher()
    ch = "chan-stall-busy"
    d.queues.setdefault(ch, fb.queue.Queue())
    with d.workers_lock:
        d._spawn_worker(ch, d.queues[ch])
    d.running.add(ch)
    d.active[ch]["last"] = time.time() - 25 * 60
    d._stall_tick()
    check("abandon: the wedged run is not counted as busy", ch not in d.running, d.running)
    msg = _FakeMsg(ch, "carry on", mid="m4")
    d.enqueue(msg, msg.text)
    check("abandon: a later message is not steered into the dead run",
          not d.steering.get(ch), d.steering)


def test_second_message_while_busy_steers_the_live_run_instead_of_queueing():
    """Live on a Linux bot 2026-09-11: a run was mid-flight when the operator sent
    "Leave it alone". Queueing it meant the correction was read only after the run it was meant to
    redirect, and by then the bot had paused 32 curl processes on another host. A run holds its
    channel's worker for its whole duration, so text that arrives during one has to go INTO it."""
    d = _dispatcher()
    ch = "chan-busy"
    q = d.queues.setdefault(ch, fb.queue.Queue())
    with d.workers_lock:
        d._spawn_worker(ch, q)
    d.running.add(ch)                    # a run really is in flight
    msg = _FakeMsg(ch, "leave it alone", mid="m2")
    d.enqueue(msg, msg.text)
    check("steering: the message did not join the queue behind the run",
          q.empty(), q.qsize())
    check("steering: it is waiting for the live run to pick up",
          [t for _, t in d.steering.get(ch, [])] == ["leave it alone"], d.steering)
    check("steering: the operator is told it was handed over, not queued",
          any("Passing that into the run" in t for _, t in d.posted), d.posted)
    check("steering: no second worker was spawned",
          [k for k in d.workers].count(ch) == 1, list(d.workers))
    check("steering: the run drains it exactly once",
          d._take_steering(ch) == [("david", "leave it alone")]
          and d._take_steering(ch) == [], d.steering)
    d.running.discard(ch)


def test_a_command_while_busy_is_never_steered_into_the_run():
    """Steering is for instructions. A slash command mid-run is a different thing (out-of-band, or
    queued) and must not end up as text in a payload."""
    d = _dispatcher()
    ch = "chan-busy-cmd"
    q = d.queues.setdefault(ch, fb.queue.Queue())
    with d.workers_lock:
        d._spawn_worker(ch, q)
    d.running.add(ch)
    msg = _FakeMsg(ch, "/version", mid="m9")
    d.enqueue(msg, msg.text)
    check("steering: a command is not steered into the live run",
          d.steering.get(ch) in (None, []), d.steering)
    d.running.discard(ch)


def test_steering_the_run_never_read_becomes_a_normal_message():
    """The run can end between a drain and the next step (a stop, a loop-stop, an infra failure).
    A correction must not vanish with it."""
    d = _dispatcher()
    ch = "chan-requeue"
    with d.steering_lock:
        d.steering[ch] = [("david", "hold on"), ("david", "leave it")]
    n = d._requeue_steering(ch)
    q = d.queues.get(ch)
    items = []
    while q is not None and not q.empty():
        items.append(q.get_nowait())
    check("requeue: unread steering comes back as queued messages",
          n == 2 and len(items) == 2, items)
    check("requeue: the text and the order survive",
          [i[1] for i in items] == ["hold on", "leave it"], items)
    check("requeue: the steering list is empty afterwards",
          d._take_steering(ch) == [], d.steering)


def test_idle_worker_window_does_not_produce_a_false_queued_notice():
    """A worker outlives its run by up to 10s waiting on an empty queue. A
    message landing in that window is NOT queued behind anything, and saying it
    is (seen live 2026-09-10 13:33) is a lie the operator cannot check."""
    d = _dispatcher()
    ch = "chan-idle"
    q = d.queues.setdefault(ch, fb.queue.Queue())
    with d.workers_lock:
        d._spawn_worker(ch, q)           # worker exists...
    check("no run in flight", ch not in d.running)
    msg = _FakeMsg(ch, "start the bios check", mid="m3")
    d.enqueue(msg, msg.text)
    check("idle worker window says nothing about being queued",
          not any("Queued behind" in t for _, t in d.posted), d.posted)
    check("the message was still accepted", not q.empty(), "dropped")


def test_a_correction_that_arrives_while_busy_is_served_not_lost():
    """Behaviour change in 1.9.27: text sent during a run is steered INTO it rather than queued
    behind it. Either way it must not be lost, so this drives the same pickup: the run reads it,
    or what it never read comes back as a queued message and the worker serves it."""
    d = _dispatcher()
    ch = "chan-serve"
    q = d.queues.setdefault(ch, fb.queue.Queue())
    with d.workers_lock:
        d._spawn_worker(ch, q)
    d.running.add(ch)
    msg = _FakeMsg(ch, "second request", mid="m4")
    d.enqueue(msg, msg.text)
    check("a correction while busy goes to the run, not behind it",
          [t for _, t in d.steering.get(ch, [])] == ["second request"], d.steering)
    d.running.discard(ch)                # the run ahead of it finishes
    d._requeue_steering(ch)              # exactly what _handle's finally does with unread steering
    deadline = time.time() + 15
    while time.time() < deadline and not d.handled:
        time.sleep(0.2)
    check("the message is picked up once the run ends",
          any(t == "second request" for _, t in d.handled), d.handled)
    check("the queue drained", q.empty(), q.qsize())


def test_stall_watchdog_warns_then_abandons_and_serves_the_queue():
    d = _dispatcher()
    ch = "chan-stall"
    d.queues.setdefault(ch, fb.queue.Queue())
    # A zombie worker's signature, not a real thread: spawning one here made this test
    # a coin toss — the live worker either drained the queue or ate the message below
    # before the tick, so `q.empty()` was True and no fresh worker was ever handed the
    # channel (observed: `worker_gen {'chan-stall': 1}`, `(set(), {})`).
    d.worker_gen[ch] = 3
    d.workers.add(ch)
    d.active[ch] = {"started": time.time(), "last": time.time() - 9 * 60,
                    "warned": False, "gen": 3}
    old_gen = d.worker_gen[ch]
    d._stall_tick()
    check("watchdog warns visibly before it acts",
          any("Still on it" in t for _, t in d.posted), d.posted)
    check("watchdog leaves the run alone at the warn threshold",
          d.worker_gen[ch] == old_gen, d.worker_gen)

    # now wedge it well past the abandon threshold, with something queued
    d.active[ch]["last"] = time.time() - 25 * 60
    d.queues[ch].put(("david", "queued while stuck", "m3", "m3", True))
    ev = threading.Event()
    d.cancel_events.setdefault(ch, set()).add(ev)
    d._stall_tick()
    check("watchdog abandons the wedged run",
          any("abandoning it" in t for _, t in d.posted), d.posted)
    check("watchdog cancels the stuck run's flag", ev.is_set(), "not set")
    check("watchdog bumps the generation so the zombie worker stops owning it",
          d.worker_gen[ch] != old_gen, d.worker_gen)
    check("watchdog hands the channel to a fresh worker with the backlog",
          d.workers.issuperset({ch}) and ch in d.active, (d.workers, d.active))


def test_stale_worker_exits_instead_of_stealing_the_channel():
    d = _dispatcher()
    ch = "chan-gen"
    q = fb.queue.Queue()
    q.put(("david", "old work", "m9", "m9", True))
    d.worker_gen[ch] = 5              # channel belongs to generation 5
    d._worker(ch, q, 3)               # a worker from generation 3
    check("stale worker does not consume the newer channel's queue",
          q.qsize() == 1, q.qsize())
    check("stale worker did not claim the channel",
          ch not in d.active or d.active[ch].get("gen") == 5, d.active)


def test_activity_tracking_keeps_a_healthy_run_off_the_watchdog():
    d = _dispatcher()
    ch = "chan-live"
    with d.workers_lock:
        d._spawn_worker(ch, d.queues.setdefault(ch, fb.queue.Queue()))
    d.active[ch]["last"] = time.time() - 9 * 60
    d._post(ch, None, "progress!")            # a normal check-in
    d._stall_tick()
    check("a channel that is posting is never warned about",
          not any("Still on it" in t for _, t in d.posted), d.posted)


def _scripted_run(scripted):
    """Run AGENT with a stubbed model; every file write goes to tmp/."""
    fb.NOTES_FILE = TMP / "notes.md"
    fb.NOTES_ARCHIVE_FILE = TMP / "notes-archive.md"
    fb.TASKS_FILE = TMP / "tasks.json"
    fb.TASKS_DOC = TMP / "tasks.md"
    fb.SESSIONS_DIR = TMP / "sessions"
    fb.SESSIONS_DIR.mkdir(exist_ok=True)
    fb.NOTES_FILE.write_text("", encoding="utf-8")
    for f in (fb.NOTES_ARCHIVE_FILE, fb.TASKS_FILE, fb.TASKS_DOC):
        if f.exists():
            f.unlink()
    fb.AGENT.histories.clear()
    fb.AGENT.model_overrides.clear()
    saved_chat = fb.AGENT._chat
    saved_cfg = dict(fb.CONFIG["agent"])
    seq = list(scripted)
    used = {"n": 0}
    payloads = []

    def fake_chat(messages, model=None, use_tools=True, usage=None,
                  max_tokens=None, cancel_event=None,
                  on_delta=None, session_key=None, **kwargs):
        if used["n"] >= len(seq):
            raise AssertionError("the run kept asking the model after the "
                                 "spin threshold — no hard stop")
        payloads.append(copy.deepcopy(messages))
        r = seq[used["n"]]
        used["n"] += 1
        if usage is not None:
            usage["calls"] = usage.get("calls", 0) + 1
            usage["llm_secs"] = usage.get("llm_secs", 0) + 0.01
        return r

    fb.AGENT._chat = fake_chat
    try:
        out = fb.AGENT.run("spin-session", "read the probe file")
    finally:
        fb.AGENT._chat = saved_chat
        fb.CONFIG["agent"].clear()
        fb.CONFIG["agent"].update(saved_cfg)
    return out, used["n"], payloads


def test_both_repeat_guards_share_one_signature():
    """One canonical call signature, so whitespace cannot defeat the refusal while
    still feeding the loop counter (audit, 2026-09-22)."""
    a = fb._call_sig("shell", '{"command": "ls"}')
    b = fb._call_sig("shell", '{"command":"ls"}')
    check("sig: a whitespace difference is the same call", a == b, (a, b))
    check("sig: a parsed dict agrees with its JSON", fb._call_sig("shell", {"command": "ls"}) == a)
    check("sig: a different argument set is a different call",
          fb._call_sig("shell", '{"command": "df"}') != a)
    check("sig: a different tool is a different call",
          fb._call_sig("read_file", '{"command": "ls"}') != a)
    check("sig: junk arguments do not raise",
          fb._call_sig("x", "{not json")[0] == "x")
    check("sig: sorting makes key order irrelevant",
          fb._call_sig("shell", '{"a": 1, "b": 2}') == fb._call_sig("shell", '{"b": 2, "a": 1}'))


def test_a_write_clears_BOTH_repeat_guards():
    """The promise in the system prompt - "any write or edit clears it" - has to hold
    for the loop guard too, not just the dedupe map.

    Script: read, read, WRITE, read, read, answer. With the loop guard's map cleared
    by the write, the last two reads count 1 and 2 (a nudge at most). Without it they
    count 3 and 4, which trips loop_stop_repeats=4 and forces a report mid-task - the
    legitimate verify-after-fix that prints the same line as before the fix.
    """
    probe = TMP / "recheck_probe.txt"
    probe.write_text("constant output\n", encoding="utf-8")

    def call(tool, args, cid):
        return {"role": "assistant", "content": "",
                "tool_calls": [{"id": cid, "function": {
                    "name": tool, "arguments": json.dumps(args)}}]}

    read = lambda cid: call("read_file", {"path": str(probe)}, cid)
    write = call("write_file", {"path": str(probe),
                               "content": "constant output\n",
                               "no_backup": True}, "w1")
    fb.CONFIG["agent"]["loop_stop_repeats"] = 4
    fb.CONFIG["agent"]["loop_dedupe_after"] = 2
    fb.CONFIG["agent"]["max_steps"] = 60
    fb.CONFIG["agent"]["max_minutes"] = 20
    out, calls, payloads = _scripted_run([
        read("1"), read("2"), write, read("3"), read("4"),
        {"role": "assistant",
         "content": "Re-checked after the write: the probe is still constant."}])
    last = json.dumps(payloads[-1]) if payloads else ""
    check("guards: the re-check after a write actually RUNS",
          "NOT RE-EXECUTED" not in last, last[-200:])
    check("guards: two more identical results do not stop the run",
          "Stopped a loop" not in out, out[:200])
    check("guards: the run ends with its own answer",
          "still constant" in out, out[:200])
    check("guards: every scripted turn was used (nothing was cut short)",
          calls == 6, f"model calls: {calls}")


def test_identical_tool_calls_stop_the_run_instead_of_burning_the_budget():
    """Live on 2026-09-10 a local model re-ran the same 6 commands for 8 cycles
    (the nudge at 3 repeats was ignored) and never reported. Identical args +
    identical output is a spin; it must end the run."""
    probe = TMP / "spin_probe.txt"
    probe.write_text("constant output\n", encoding="utf-8")
    call = {"role": "assistant", "content": "",
            "tool_calls": [{"id": "1", "function": {
                "name": "read_file",
                "arguments": json.dumps({"path": str(probe)})}}]}
    scripted = ([dict(call) for _ in range(4)]
                + [{"role": "assistant",
                    "content": "Final: the probe holds a constant."}]
                + [dict(call) for _ in range(6)])
    fb.CONFIG["agent"]["loop_stop_repeats"] = 4
    fb.CONFIG["agent"]["max_steps"] = 100
    fb.CONFIG["agent"]["max_minutes"] = 30
    out, calls, _ = _scripted_run(scripted)
    check("spin: the run stops with a loop-stop notice",
          "Stopped a loop" in out, out[:160])
    check("spin: it stopped at the threshold, not the step budget",
          calls == 5, f"model calls used: {calls}")
    check("spin: the forced final report is still delivered",
          "constant" in out, out[:200])
    check("spin: a spin is classified, not reported as a clean finish",
          "ok" not in out[:20].lower(), out[:40])


def test_a_single_repeat_does_not_trip_the_hard_stop():
    """Two identical calls then real progress must NOT be treated as a spin."""
    probe = TMP / "spin_probe2.txt"
    probe.write_text("constant output\n", encoding="utf-8")
    call = {"role": "assistant", "content": "",
            "tool_calls": [{"id": "1", "function": {
                "name": "read_file",
                "arguments": json.dumps({"path": str(probe)})}}]}
    out, calls, _ = _scripted_run([dict(call), dict(call),
                                   {"role": "assistant",
                                    "content": "Done: read it twice, all good."}])
    check("spin: two repeats are allowed", "Stopped a loop" not in out,
          out[:160])
    check("spin: the run finished normally", "read it twice" in out, out[:160])
    check("spin: no extra model calls", calls == 3, calls)


def test_an_exact_duplicate_call_is_refused_not_re_executed():
    """The real complaint: a loop should not RUN again. Six identical shell
    calls must execute twice, and the model must be handed the cached result
    with a hard refusal instead of a third real run (which for a mutating
    command is also a safety problem)."""
    counter = TMP / "dedupe_counter.txt"
    if counter.exists():
        counter.unlink()
    cmd = shell_append(counter)
    call = {"role": "assistant", "content": "",
            "tool_calls": [{"id": "1", "function": {
                "name": "shell",
                "arguments": json.dumps({"command": cmd})}}]}
    fb.CONFIG["agent"]["loop_dedupe_after"] = 2
    fb.CONFIG["agent"]["loop_stop_repeats"] = 6
    fb.CONFIG["agent"]["max_steps"] = 100
    fb.CONFIG["agent"]["max_minutes"] = 30
    # Six identical attempts are scripted; the run uses FOUR. Attempts 1-2 execute, 3 and 4
    # are refused, and the second refusal ends the run (see
    # test_a_refusal_that_comes_back_ends_the_run), so the report is read from the reply that
    # follows the calls. What this pins is unchanged: the loop never RUNS a third time.
    scripted = ([copy.deepcopy(call) for _ in range(4)]
                + [{"role": "assistant", "content": "Done: nothing further."}]
                + [copy.deepcopy(call) for _ in range(2)])
    out, calls, payloads = _scripted_run(scripted)
    ticks = (counter.read_text(encoding="utf-8").count("tick")
             if counter.exists() else 0)
    check("duplicate: the command really ran only twice", ticks == 2, ticks)
    transcript = json.dumps(payloads[-1] if payloads else [])
    check("duplicate: the model was told it was not re-executed",
          "NOT RE-EXECUTED" in transcript, transcript[:200])
    check("duplicate: the cached result came back with it",
          "tick" in transcript, "cached output missing")
    check("duplicate: the harness ends the spin itself, not the script",
          calls == 5, f"model calls used: {calls}")
    check("duplicate: the forced report is still delivered",
          "Done: nothing further" in out, out[:300])


def test_a_reformatted_identical_call_is_still_the_same_call():
    """The refusal has to key on the CANONICAL signature, not on the raw string the model
    happened to emit.

    Measured 2026-09-24 on a fleet Windows box: the same directory listing really ran three
    times inside one chat turn - three tool results with one identical output digest and one
    byte length - while the loop guard counted it and the refusal that should have stopped
    the third run never fired. The model had re-emitted identical arguments with different
    formatting, and the refusal's lookup read that as a different call; the write side had
    already been moved to _call_sig(). Every scripted reply in the old suite used one
    identical json.dumps(), which is why the case could not be seen from here.
    """
    counter = TMP / "dedupe_reformat.txt"
    if counter.exists():
        counter.unlink()
    cmd = shell_append(counter)
    spellings = [json.dumps({"command": cmd}),
                 json.dumps({"command": cmd}, separators=(",", ":")),
                 '{"command":' + json.dumps(cmd) + '}',
                 json.dumps({"command": cmd}, indent=1)]

    def call(cid, raw):
        return {"role": "assistant", "content": "",
                "tool_calls": [{"id": cid, "function": {"name": "shell",
                                                        "arguments": raw}}]}

    fb.CONFIG["agent"]["loop_dedupe_after"] = 2
    fb.CONFIG["agent"]["loop_stop_repeats"] = 6
    fb.CONFIG["agent"]["max_steps"] = 100
    fb.CONFIG["agent"]["max_minutes"] = 30
    scripted = [call(str(i), s) for i, s in enumerate(spellings)]
    scripted.append({"role": "assistant", "content": "Done: the counter says two."})
    out, calls, payloads = _scripted_run(scripted)
    ticks = (counter.read_text(encoding="utf-8").count("tick")
             if counter.exists() else 0)
    check("reformatted: the command ran twice, not four times", ticks == 2, ticks)
    transcript = json.dumps(payloads[-1] if payloads else [])
    check("reformatted: the cheaper spellings get the cached refusal",
          "NOT RE-EXECUTED" in transcript, transcript[:200])


def test_a_refusal_that_comes_back_ends_the_run():
    """One refusal is the guard working as designed. The model issuing the same refused call
    AGAIN is a spin, and the operator should not have to read another card for it.

    Measured 2026-09-24 on a fleet Windows box: identical attempts kept arriving while the
    guard counted them, and the run ended on a promise of future work instead of a report.
    The hard stop needed `loop_stop_repeats` attempts with the SAME OUTPUT, which a refused
    call never has - it returns the refusal text - so the ladder could not see this shape.
    """
    counter = TMP / "dedupe_refusal_spin.txt"
    if counter.exists():
        counter.unlink()
    cmd = shell_append(counter)
    call = {"role": "assistant", "content": "",
            "tool_calls": [{"id": "1", "function": {
                "name": "shell", "arguments": json.dumps({"command": cmd})}}]}
    fb.CONFIG["agent"]["loop_dedupe_after"] = 2
    fb.CONFIG["agent"]["loop_stop_repeats"] = 6     # the old ladder's threshold
    fb.CONFIG["agent"]["max_steps"] = 100
    fb.CONFIG["agent"]["max_minutes"] = 30
    # FOUR attempts and then the report: attempts 1-2 execute, 3 and 4 come back refused,
    # and the run stops itself on the second refusal, so the NEXT model call is the forced
    # final report. The script has to put the report exactly there.
    scripted = ([copy.deepcopy(call) for _ in range(4)]
                + [{"role": "assistant",
                    "content": "Report: the counter holds two ticks."}])
    out, calls, _ = _scripted_run(scripted)
    ticks = (counter.read_text(encoding="utf-8").count("tick")
             if counter.exists() else 0)
    check("refusal spin: the run stops itself", "Stopped a loop" in out, out[:160])
    check("refusal spin: well before the 6-repeat threshold", calls <= 5, calls)
    check("refusal spin: the command still ran only twice", ticks == 2, ticks)
    check("refusal spin: the forced report is delivered",
          "counter holds two ticks" in out, out[:220])


def test_duplicate_refusal_can_be_switched_off():
    fb.CONFIG["agent"]["loop_dedupe_after"] = 0
    counter = TMP / "dedupe_off.txt"
    if counter.exists():
        counter.unlink()
    cmd = shell_append(counter)
    call = {"role": "assistant", "content": "",
            "tool_calls": [{"id": "1", "function": {
                "name": "shell",
                "arguments": json.dumps({"command": cmd})}}]}
    fb.CONFIG["agent"]["loop_stop_repeats"] = 6
    fb.CONFIG["agent"]["max_steps"] = 100
    out, calls, _ = _scripted_run([dict(call) for _ in range(5)]
                                  + [{"role": "assistant", "content": "Done."}])
    ticks = (counter.read_text(encoding="utf-8").count("tick")
             if counter.exists() else 0)
    check("dedupe off: every call executes again", ticks == 5, ticks)


def test_the_payload_echoes_the_assistant_tool_call_turn():
    """Parity: the turn that requested the tools must be in the payload,
    with ids matching the tool results. Without it the model cannot see that it
    already ran the call — half of the re-running loop."""
    probe = TMP / "parity_probe.txt"
    probe.write_text("constant contents\n", encoding="utf-8")
    call = {"role": "assistant", "content": "Reading the probe.",
            "tool_calls": [{"id": "call_abc", "type": "function", "function": {
                "name": "read_file",
                "arguments": json.dumps({"path": str(probe)})}}]}
    out, calls, payloads = _scripted_run([dict(call),
                                          {"role": "assistant",
                                           "content": "Done: read it."}])
    second = payloads[1] if len(payloads) > 1 else []
    asst = [m for m in second
            if m.get("role") == "assistant" and m.get("tool_calls")]
    tools = [m for m in second if m.get("role") == "tool"]
    check("parity: the assistant tool-call turn is in the payload", bool(asst),
          [m.get("role") for m in second])
    check("parity: its narration is kept too",
          bool(asst) and "Reading the probe" in (asst[0].get("content") or ""),
          asst[0].get("content") if asst else None)
    check("parity: tool_call_id matches the assistant turn's call id",
          bool(asst) and bool(tools)
          and tools[0].get("tool_call_id") == asst[0]["tool_calls"][0].get("id"),
          (tools[0].get("tool_call_id") if tools else None,
           asst[0]["tool_calls"][0].get("id") if asst else None))
    # Exactly one, and no repeated call id: this used to be TWO assistant turns
    # carrying the same call (dump-verified 2026-09-10), because the echo
    # appended after the raw reply was already there. A duplicated assistant
    # turn is the shape that teaches a model to repeat itself, so the count is
    # the assertion that matters here.
    check("parity: the assistant turn appears exactly once", len(asst) == 1,
          f"{len(asst)} assistant tool-call turns")
    ids = [tc.get("id") for m in asst for tc in (m.get("tool_calls") or [])]
    check("parity: no tool-call id is duplicated", len(ids) == len(set(ids)), ids)
    check("parity: one tool result per call", len(tools) == len(set(ids)),
          f"{len(tools)} results for {len(ids)} calls")


def test_an_id_less_tool_call_gets_one_matching_id_everywhere():
    """llama.cpp can return calls without ids; the id we synthesise has to be the
    same one the tool result carries, or the pair is broken."""
    probe = TMP / "parity_probe2.txt"
    probe.write_text("x\n", encoding="utf-8")
    call = {"role": "assistant", "content": "",
            "tool_calls": [{"type": "function", "function": {
                "name": "read_file",
                "arguments": json.dumps({"path": str(probe)})}}]}
    out, calls, payloads = _scripted_run([dict(call),
                                          {"role": "assistant", "content": "Done."}])
    second = payloads[1] if len(payloads) > 1 else []
    asst = [m for m in second if m.get("tool_calls")]
    tools = [m for m in second if m.get("role") == "tool"]
    check("parity: a missing id is filled in",
          bool(asst) and asst[0]["tool_calls"][0].get("id"),
          asst[0]["tool_calls"] if asst else None)
    check("parity: and the tool result uses the same id",
          bool(asst) and bool(tools)
          and tools[0].get("tool_call_id") == asst[0]["tool_calls"][0]["id"],
          tools[0].get("tool_call_id") if tools else None)


def test_done_ledger_items_are_not_re_issued_as_instructions():
    """The state block is re-sent every call. A done task whose text says
    'read X, confirm Y' acted as a standing order (task #4, 2026-09-10)."""
    fb.TASKS_FILE = TMP / "tasks.json"
    fb.TASKS_FILE.write_text(json.dumps({"items": [
        {"id": 1, "status": "open", "desc": "Fix the thing", "note": ""},
        {"id": 4, "status": "done",
         "desc": "Verify post-flash state: read bios-postflash.log for "
                 "SMBIOS=M2WKT65A and logical=12; confirm containers are up",
         "note": "verified at 12:49: SMBIOS=M2WKT65A, HT Enabled, logical=12"},
    ]}), encoding="utf-8")
    rendered = fb.render_task_prompt()
    check("ledger: open items keep their text",
          "Fix the thing" in rendered, rendered)
    check("ledger: done items are labelled no-action",
          "[done, no action]" in rendered, rendered)
    check("ledger: a done item's instruction text is curtailed",
          "confirm containers are up" not in rendered, rendered)
    check("ledger: a done item's evidence note is not re-sent",
          "verified at 12:49" not in rendered, rendered)
    check("ledger: it says what the block is for",
          "no action" in rendered and "to-do list" in rendered, rendered)


def test_no_run_ever_sends_sampling_parameters():
    """Run-level proof of the standing rule, not just a helper test: whatever
    config.json says, the bodies that actually go to the endpoint carry no
    temp/top_p/top_k/min_p. (Superseded an earlier test that asserted the
    opposite — 'sampling is sent explicitly, not inherited' — which is the
    policy that produced the stale 0.6.)"""
    probe = TMP / "sampling_probe.txt"
    probe.write_text("x\n", encoding="utf-8")
    saved = dict(fb.CONFIG["llm"])
    try:
        # a stray, deliberately wrong pin in config
        fb.CONFIG["llm"]["temperature"] = 0.6
        fb.CONFIG["llm"]["sampling"] = {"top_p": 0.95, "top_k": 20, "min_p": 0.0}
        call = {"role": "assistant", "content": "Reading.",
                "tool_calls": [{"id": "c1", "type": "function", "function": {
                    "name": "read_file",
                    "arguments": json.dumps({"path": str(probe)})}}]}
        out, calls, payloads = _scripted_run([dict(call),
                                              {"role": "assistant", "content": "ok"}])
        offenders = [sorted(k for k in pl if k in
                            ("temperature", "top_p", "top_k", "min_p",
                             "repeat_penalty", "presence_penalty",
                             "frequency_penalty"))
                     for pl in payloads]
        check("sampling: no call in a run carries sampling keys",
              all(not o for o in offenders), offenders)
        check("sampling: the run still worked", calls >= 1 and "ok" in out, out[:80])
    finally:
        fb.CONFIG["llm"].clear()
        fb.CONFIG["llm"].update(saved)


def test_the_first_repeat_is_labelled_and_warned_about():
    """Refusal is the backstop; the goal is that the model never re-issues the
    call. So the second execution must already be labelled in the tool output it
    reads, and it must get a user-role nudge (waiting for the third wasted two
    executions, which may mutate the box)."""
    probe = TMP / "labelled_probe.txt"
    probe.write_text("constant\n", encoding="utf-8")
    call = {"role": "assistant", "content": "",
            "tool_calls": [{"id": "1", "function": {
                "name": "read_file",
                "arguments": json.dumps({"path": str(probe)})}}]}
    fb.CONFIG["agent"]["loop_dedupe_after"] = 4      # allow the repeat through
    fb.CONFIG["agent"]["loop_stop_repeats"] = 6
    fb.CONFIG["agent"]["max_steps"] = 100
    out, calls, payloads = _scripted_run([dict(call), dict(call),
                                          {"role": "assistant",
                                           "content": "Done: used it."}])
    final = json.dumps(payloads[-1])
    check("labelled: the repeat is marked in the tool output",
          "[HARNESS:" in final and "already ran" in final, final[:200])
    check("labelled: it says which execution this was", "execution #2" in final,
          "no execution number")
    check("labelled: a user-role nudge fires on the first repeat",
          "you have now made this exact" in final
          and "2 times" in final, "no nudge at 2 repeats")


def test_a_command_whose_output_changes_is_never_refused():
    """Polling is real work: identical args with a DIFFERENT result must run
    every time."""
    counter = TMP / "poll_counter.txt"
    if counter.exists():
        counter.unlink()
    # each run appends a line and prints how many lines exist -> output grows
    cmd = shell_append_and_count(counter)
    call = {"role": "assistant", "content": "",
            "tool_calls": [{"id": "1", "function": {
                "name": "shell",
                "arguments": json.dumps({"command": cmd})}}]}
    fb.CONFIG["agent"]["loop_dedupe_after"] = 2
    fb.CONFIG["agent"]["loop_stop_repeats"] = 6
    fb.CONFIG["agent"]["max_steps"] = 100
    out, calls, _ = _scripted_run([dict(call) for _ in range(4)]
                                  + [{"role": "assistant", "content": "Done."}])
    runs = (counter.read_text(encoding="utf-8").count("x")
            if counter.exists() else 0)
    check("polling: a changing result is never refused", runs == 4, runs)
    check("polling: no loop stop for real polling",
          "Stopped a loop" not in out, out[:120])


def test_a_refused_mutation_is_not_reported_as_a_change():
    """A refused call never ran. Counting it as a mutation would tell the
    operator a change happened that did not."""
    marker = TMP / "mutation_marker.txt"
    if marker.exists():
        marker.unlink()
    cmd = shell_create(marker)
    call = {"role": "assistant", "content": "",
            "tool_calls": [{"id": "1", "function": {
                "name": "shell",
                "arguments": json.dumps({"command": cmd})}}]}
    fb.CONFIG["agent"]["loop_dedupe_after"] = 1     # refuse from the 2nd on
    fb.CONFIG["agent"]["loop_stop_repeats"] = 6
    fb.CONFIG["agent"]["max_steps"] = 100
    out, calls, _ = _scripted_run([dict(call) for _ in range(3)]
                                  + [{"role": "assistant",
                                      "content": "Report: done."}])
    check("refused mutation: executed once", marker.exists(), "never ran")
    check("refused mutation: not claimed as a change",
          "evidence check" not in out and "unverified" not in out.lower(),
          out[:200])


def test_the_execution_counter_does_not_leak_between_runs():
    """A fresh run must be able to run the same command again — the cache is
    per-run, not per-process."""
    probe = TMP / "fresh_probe.txt"
    probe.write_text("constant\n", encoding="utf-8")
    call = {"role": "assistant", "content": "",
            "tool_calls": [{"id": "1", "function": {
                "name": "read_file",
                "arguments": json.dumps({"path": str(probe)})}}]}
    fb.CONFIG["agent"]["loop_dedupe_after"] = 2
    fb.CONFIG["agent"]["loop_stop_repeats"] = 6
    fb.CONFIG["agent"]["max_steps"] = 100
    first = _scripted_run([dict(call), dict(call),
                           {"role": "assistant", "content": "One."}])
    second = _scripted_run([dict(call), dict(call),
                            {"role": "assistant", "content": "Two."}])
    check("per-run: first run answered", "One." in first[0], first[0][:120])
    check("per-run: second run answered too", "Two." in second[0],
          second[0][:120])
    check("per-run: the second run was not refused",
          "Stopped a loop" not in second[0], second[0][:120])


def test_interleaved_repeats_are_still_counted_per_command():
    """A/B/A/B: each command may run twice, and the third attempt at either is
    refused — interleaving must not reset the count."""
    probe = TMP / "interleave_a.txt"
    probe.write_text("A\n", encoding="utf-8")
    probe2 = TMP / "interleave_b.txt"
    probe2.write_text("B\n", encoding="utf-8")
    a = {"role": "assistant", "content": "",
         "tool_calls": [{"id": "1", "function": {
             "name": "read_file", "arguments": json.dumps({"path": str(probe)})}}]}
    b = {"role": "assistant", "content": "",
         "tool_calls": [{"id": "2", "function": {
             "name": "read_file",
             "arguments": json.dumps({"path": str(probe2)})}}]}
    fb.CONFIG["agent"]["loop_dedupe_after"] = 2
    fb.CONFIG["agent"]["loop_stop_repeats"] = 8
    fb.CONFIG["agent"]["max_steps"] = 100
    out, calls, payloads = _scripted_run([a, b, a, b, a,
                                          {"role": "assistant",
                                           "content": "Done."}])
    check("interleaved: run finished", "Done." in out, out[:120])
    check("interleaved: the 5th call (A again) was refused",
          "NOT RE-EXECUTED" in json.dumps(payloads[-1]),
          "no refusal in the transcript")


def test_the_system_prompt_describes_the_repeat_guard_that_exists():
    """The prompt must match the guard's real behaviour. The old wording said "never
    re-issue a call" and told the model to invent a different command - but after a
    real change, re-running the SAME command is the only thing that proves anything,
    and it does execute (2026-09-12: the Windows bed fixed a tool, got its own pre-edit
    output back, and explained it away as the harness caching)."""
    sp = fb.build_system_prompt()
    check("prompt: the guard applies only while nothing has changed",
          "refuses a repeat only while nothing has changed" in sp, "rule missing")
    check("prompt: a change is stated to clear it",
          "any write or edit clears it" in sp, "escape hatch missing")
    check("prompt: what to do after a fix is spelled out",
          "re-run the SAME command that showed the problem" in sp, "guidance missing")
    check("prompt: it explains the harness label",
          "HARNESS" in sp and "execution #N" in sp, "label not explained")


def test_the_system_prompt_forbids_dismissing_a_request():
    """Observed live 2026-09-10: the bot ran three docker checks and then replied
    'nothing actionable ... channel noise' instead of answering."""
    sp = fb.build_system_prompt()
    check("prompt: dismissing a message as noise is forbidden",
          "noise" in sp and "nothing actionable" in sp, "rule missing")
    check("prompt: it must answer with what the tools returned",
          "must contain what they returned" in sp, "data rule missing")


def test_visible_metrics_for_duplicates_and_sampling():
    """The operator should be able to see the anti-loop machinery working, and
    see which sampling is actually in force."""
    base = {"calls": 2, "prompt": 1000, "completion": 100, "llm_secs": 10.0,
            "peak_prompt": 600}
    plain = fb.fmt_usage(dict(base))
    check("usage line: silent when nothing was blocked",
          "duplicate" not in plain, plain)
    blocked = fb.fmt_usage({**base, "duplicates_blocked": 3})
    check("usage line: reports blocked duplicates", "3 duplicate call(s) blocked"
          in blocked, blocked)
    allowed = fb.fmt_usage({**base, "duplicates_labelled": 1})
    check("usage line: reports an allowed repeat", "1 repeat(s) allowed" in allowed,
          allowed)

def test_sampling_is_always_inherited_never_sent():
    """Standing rule: tinycmdr never defines sampling parameters. It may be
    pointed at a cloud provider or a different local model at any time, and a
    pinned temp/top_p/top_k would then override that endpoint's own stack. This
    box already carried a stale temperature 0.6 for weeks that way. So the rule
    is made mechanical, not conventional: the request can never carry these
    keys, even if someone puts values back in config.json."""
    import json as _json

    # the config this suite runs with: the install's own when there is one,
    # otherwise the staged fixture (a fresh unpack has no config.json yet)
    _real = BASE / "config.json"
    shipped = _json.loads((_real if _real.exists() else FIXTURE_CFG)
                          .read_text(encoding="utf-8-sig"))
    check("config: config.json defines no sampling keys at all",
          "temperature" not in shipped["llm"]
          and not (shipped["llm"].get("sampling") or {}),
          {k: v for k, v in shipped["llm"].items() if k in
           ("temperature", "sampling")})
    # the code's own defaults counted too: that is where the unexplained 0.6
    # lived, applied whenever config.json did not override it
    check("config: the loaded config has no sampling keys either",
          "temperature" not in fb.CONFIG["llm"]
          and not (fb.CONFIG["llm"].get("sampling") or {}),
          {k: v for k, v in fb.CONFIG["llm"].items() if k in
           ("temperature", "sampling")})

    saved = dict(fb.CONFIG["llm"])
    saved_props = dict(fb._PROPS_CACHE)
    try:
        # a clean config sends nothing
        fb.CONFIG["llm"].pop("temperature", None)
        fb.CONFIG["llm"].pop("sampling", None)
        body = fb.apply_sampling({"model": "main", "messages": []})
        check("request: no sampling keys are sent",
              not any(k in body for k in ("temperature", "top_k", "top_p", "min_p")),
              sorted(body))

        # strays in config are ignored AND reported, not silently applied
        fb.CONFIG["llm"]["temperature"] = 0.6
        fb.CONFIG["llm"]["sampling"] = {"top_k": 20, "min_p": 0.05}
        body = fb.apply_sampling({"model": "main"})
        check("request: a stray config value is still not sent",
              not any(k in body for k in ("temperature", "top_k", "min_p")),
              sorted(body))
        check("config: strays are detected",
              fb.strays_in_config() == ["min_p", "temperature", "top_k"],
              fb.strays_in_config())

        # even a payload that already carries them gets cleaned
        dirty = fb.apply_sampling({"model": "main", "temperature": 0.2,
                                   "top_p": 0.1, "tools": []})
        check("request: keys already on the payload are stripped",
              "temperature" not in dirty and "top_p" not in dirty
              and "tools" in dirty, sorted(dirty))

        # the summary reports the endpoint's values and flags the strays
        fb._PROPS_CACHE.update(at=time.time(), data={
            "temperature": 1.0, "top_p": 0.949999988079071, "top_k": 20,
            "min_p": 0.05})
        s = fb.sampling_summary()
        check("status: says inherited and shows the endpoint's real values",
              "inherited from the server" in s and "temp 1.0" in s
              and "top_k 20" in s, s)
        check("status: float noise is rounded", "0.95" in s and "0.949999" not in s, s)
        check("status: stray config is called out as ignored",
              "IGNORED config" in s and "temperature" in s, s)

        # unreachable endpoint must not invent numbers
        fb.CONFIG["llm"].pop("temperature", None)
        fb.CONFIG["llm"].pop("sampling", None)
        fb._PROPS_CACHE.update(at=0.0, data={})
        orig = fb.requests.get
        fb.requests.get = lambda *a, **k: (_ for _ in ()).throw(OSError("down"))
        try:
            s = fb.sampling_summary()
        finally:
            fb.requests.get = orig
        check("status: still says inherited when /props is unreachable",
              s == "inherited from the server", s)
    finally:
        fb.CONFIG["llm"].clear()
        fb.CONFIG["llm"].update(saved)
        fb._PROPS_CACHE.clear()
        fb._PROPS_CACHE.update(saved_props)


def test_props_parsing_matches_the_live_shape():
    """Pinned against a real /props payload from the LAN model box:8081."""
    live = {"default_generation_settings": {"params": {
        "temperature": 1.0, "top_p": 0.95, "top_k": 20, "min_p": 0.05,
        "repeat_penalty": 1.0, "seed": -1, "n_predict": -1}},
        "total_slots": 2, "n_ctx": 262144}
    got = fb.parse_props(live)
    check("props: the four sampling keys are read",
          got == {"temperature": 1.0, "top_p": 0.95, "top_k": 20, "min_p": 0.05}, got)
    check("props: junk does not raise",
          fb.parse_props({}) == {} and fb.parse_props(None) == {}, "junk")


def test_status_has_one_implementation():
    """The Mattermost handler and the web UI each had their own /status and had
    drifted (the web one silently lacked sampling and notes). One renderer, both
    callers."""
    src = (SRC).read_text(encoding="utf-8", errors="replace")
    check("status: the shared renderer exists", "def status_text(" in src)
    check("status: the web handler uses it",
          'return ("reply", status_text(key))' in src, "web not unified")
    check("status: the Mattermost handler uses it",
          "status_text(session_key, paused=self.paused)" in src,
          "mattermost not unified")
    check("status: no second copy of the fields",
          src.count("notes.md: {notes_kb:.1f} KB") == 1,
          "duplicate renderer left behind")
    reply = fb._web_command("/status")[1]
    check("status: sampling is reported", "sampling:" in reply, reply[:200])
    check("status: the budget is reported", "tokens in context" in reply,
          reply[:200])
    check("status: the guardrails are visible", "limits:" in reply
          and "loop-stop" in reply, reply[:300])


def test_payload_dump_is_off_unless_configured(tmp=None):
    """The dump hook is the only way to see what the model was actually shown, so
    it must work when enabled — and must stay out of the way when it is not."""
    import json as _json
    import pathlib
    import tempfile

    saved = dict(fb.CONFIG["agent"])
    try:
        fb.CONFIG["agent"]["debug_dump_dir"] = ""
        before = set(pathlib.Path(tempfile.gettempdir()).glob("fb-dump-*"))
        fb.dump_payload({"model": "main", "messages": []}, "mm-test")
        after = set(pathlib.Path(tempfile.gettempdir()).glob("fb-dump-*"))
        check("dump: nothing written when disabled", before == after, "wrote anyway")

        d = pathlib.Path(tempfile.mkdtemp(prefix="fb-dump-"))
        atexit.register(lambda: shutil.rmtree(d, ignore_errors=True))
        fb.CONFIG["agent"]["debug_dump_dir"] = str(d)
        fb.dump_payload({"model": "main", "messages": [{"role": "user",
                                                        "content": "hi"}]},
                        "mm-test:chan")
        files = list(d.glob("*.json"))
        check("dump: payload written when enabled", len(files) == 1, files)
        if files:
            body = _json.loads(files[0].read_text(encoding="utf-8"))
            check("dump: contents round-trip", body["messages"][0]["content"] == "hi",
                  body)
            check("dump: session key is filename-safe", ":" not in files[0].name,
                  files[0].name)
        # a bad path must not take the run down with it
        fb.CONFIG["agent"]["debug_dump_dir"] = r"Z:\nope\<bad>|path"
        try:
            fb.dump_payload({"model": "main"}, "mm-test")
            check("dump: bad path is swallowed", True)
        except Exception as exc:
            check("dump: bad path is swallowed", False, repr(exc))
        for f in d.glob("*"):
            f.unlink()
        d.rmdir()
    finally:
        fb.CONFIG["agent"].clear()
        fb.CONFIG["agent"].update(saved)


def test_the_state_block_never_lands_on_the_operator_request():
    """Live failure 2026-09-10: the volatile state block was appended AFTER the
    operator's message, so the last thing the model saw was 4.8k chars of live
    state — and it answered that ("Nothing new to chase — the refreshed state
    just confirms everything I've reported still holds") instead of the question.
    The block belongs before the request; the request must stay last."""
    def payload(msgs, state=True):
        return fb.AGENT._payload([dict(m) for m in msgs], state=state)

    # the block's content is irrelevant here, only its position is: stub it so
    # the test does not depend on notes.md or the ledger existing on this box
    orig = fb.volatile_context
    _stub_n = [0]

    def _stub_state(*a, **k):
        # volatile ON PURPOSE: a stub that returns the same bytes every call would
        # hide exactly the bug this test now guards (a block that moves the whole
        # prompt each turn)
        _stub_n[0] += 1
        return fb._STATE_MARKER + f"notes: stub {_stub_n[0]}\nstate: stub"

    fb.volatile_context = _stub_state

    msgs = [{"role": "system", "content": "sys"},
            {"role": "user", "content": "first ask"},
            {"role": "assistant", "content": "first answer"},
            {"role": "user", "content": "List the Docker containers. Read-only."}]
    out = payload(msgs)
    check("payload: the operator request is still last",
          out[-1]["content"] == "List the Docker containers. Read-only.",
          [m["role"] + ":" + str(m["content"])[:40] for m in out])
    idx = [i for i, m in enumerate(out) if fb._STATE_PREFIX in str(m["content"])]
    check("payload: the state block is present exactly once", len(idx) == 1, idx)
    check("payload: it sits before the request", idx and idx[0] == len(out) - 2,
          f"state at {idx}, request at {len(out) - 1}")
    check("payload: it is a user message", out[idx[0]]["role"] == "user")

    # mid-run (tool rounds appended) the block must TRAIL: the model is answering
    # the task, not the block, and a payload that does not extend the previous
    # call's prompt makes the server re-prefill the whole conversation every round.
    # Measured 2026-09-18 with the block before the last user message: a mid-run
    # prompt grew 57k -> 67k tokens while the reusable prefix stayed pinned at the
    # system prompt (6,993 of 67k, 11% reused) = 193-219 s of prefill per call, and
    # that is the wall clock a long job died on.
    def tool_round(n):
        return [{"role": "assistant", "content": None,
                 "tool_calls": [{"id": n, "type": "function",
                                 "function": {"name": "shell", "arguments": "{}"}}]},
                {"role": "tool", "tool_call_id": n, "content": "out " + n}]

    mid = msgs + tool_round("1")
    out2 = payload(mid)
    check("payload: mid-run the block trails the tool result",
          fb._STATE_PREFIX in str(out2[-1]["content"])
          and fb._STATE_PREFIX not in str(out2[-2]["content"]),
          [m["role"] for m in out2])
    check("payload: no duplicate block on repeated calls",
          len([m for m in out2 if fb._STATE_PREFIX in str(m["content"])]) == 1)
    # the property that buys the cache: the next round's payload carries this round's
    # messages as an exact prefix (the volatile block is last and by design differs,
    # so it is the one message excluded from the comparison)
    out2b = payload(mid + tool_round("2"))
    check("payload: a mid-run payload extends the previous one as a prefix",
          [m["content"] for m in out2b[:len(out2) - 1]]
          == [m["content"] for m in out2[:len(out2) - 1]],
          [str(m["content"])[:30] for m in out2b])

    # the forced wrap-up passes state=False
    out3 = payload(msgs, state=False)
    check("payload: state=False adds nothing",
          not any(fb._STATE_PREFIX in str(m["content"]) for m in out3))

    # if there is no operator turn to sit before, it appends rather than vanishing
    sysonly = [{"role": "system", "content": "sys"}]
    out4 = payload(sysonly)
    check("payload: falls back to appending with no user turn",
          len(out4) == 2 and fb._STATE_PREFIX in out4[-1]["content"], out4)
    fb.volatile_context = orig


def test_an_inline_tool_call_is_executed_not_posted_as_the_answer():
    """Live 2026-09-10: an assistant turn arrived as raw text
    '<tool_call><function=shell>...' — it was posted to chat verbatim and stored
    in history, so the operator saw markup instead of work being done. It must be
    parsed and executed, and the markup must never reach the reply."""
    leaked = ("<tool_call>\n<function=shell>\n<parameter=command>\n"
              "Write-Output inline-parsed-ok\n</parameter>\n</function>\n"
              "</tool_call>")
    out, calls, payloads = _scripted_run([
        {"content": leaked},
        {"content": "Done - the command was executed."},
    ])
    check("inline: the leaked call was parsed, not posted",
          "Done - the command was executed." in out, out[:200])
    check("inline: no raw tool-call markup in the reply",
          "<tool_call>" not in out and "<function=" not in out, out[:200])
    check("inline: the call actually ran", calls >= 1, f"{calls} tool call(s)")
    joined = json.dumps(payloads[-1])
    check("inline: markup is stripped from the stored turn",
          "<function=shell>" not in joined, "markup in history")
    check("inline: the transcript shows a real tool call",
          '"tool_calls"' in joined
          and '"name": "shell"' in joined.replace('\\"', '"'), "no tool_calls")

    # parsing shape, against the exact form the template emitted
    parsed = fb.parse_inline_tool_calls(leaked)
    check("inline: parser finds one call", len(parsed) == 1, parsed)
    check("inline: parser reads the name and argument",
          parsed and parsed[0]["name"] == "shell"
          and parsed[0]["arguments"] == {"command": "Write-Output inline-parsed-ok"},
          parsed)
    check("inline: a JSON body is accepted too",
          fb.parse_inline_tool_calls(
              '<tool_call><function=task>{"action": "list"}</function>'
              '</tool_call>')[0]["arguments"] == {"action": "list"},
          fb.parse_inline_tool_calls(
              '<tool_call><function=task>{"action": "list"}</function>'
              '</tool_call>'))
    check("inline: plain prose is left alone",
          fb.parse_inline_tool_calls("just an answer") == []
          and fb.strip_inline_tool_calls("just an answer") == "just an answer",
          "prose")


def test_startup_validation_catches_an_unconfigured_host():
    """The first thing a fresh install hits. It must fail with a sentence a human
    can act on, not a traceback from inside the Mattermost driver."""
    if importlib.util.find_spec("mmpy_bot") is None:
        # validate_startup_config() reports the missing driver BEFORE it reads any
        # config value, so not one of the messages below can be produced here.
        # Skipping beats five failures that blame the code for the environment.
        skip("startup validation catches an unconfigured host",
             "mmpy_bot is not installed for this python — run the suite with the "
             "venv the bot uses, or pip install -r requirements.txt")
        return
    saved = dict(fb.CONFIG["mattermost"])
    saved_token = None
    try:
        # a real config: fine
        fb.CONFIG["mattermost"].update({"url": "chat.example.org", "token": "abc123",
                                        "allowed_users": ["u1"]})
        check("startup: a configured host passes", fb.validate_startup_config() is None,
              fb.validate_startup_config())

        # empty or placeholder host
        fb.CONFIG["mattermost"]["url"] = ""
        msg = fb.validate_startup_config() or ""
        check("startup: empty url is rejected with instructions",
              "mattermost.url" in msg and "Set it to your Mattermost host" in msg, msg)
        fb.CONFIG["mattermost"]["url"] = "CHANGE-ME.example.com"
        check("startup: the CHANGE-ME placeholder is rejected",
              "mattermost.url" in (fb.validate_startup_config() or ""),
              fb.validate_startup_config())

        # example.com warns but does not abort (a host may legitimately use it)
        fb.CONFIG["mattermost"]["url"] = "chat.example.com"
        check("startup: example.com warns instead of aborting",
              fb.validate_startup_config() is None, fb.validate_startup_config())

        # no token anywhere
        fb.CONFIG["mattermost"]["url"] = "chat.example.org"
        fb.CONFIG["mattermost"]["token"] = ""
        msg = fb.validate_startup_config() or ""
        check("startup: a missing token names .env", "TINYCMDR_MM_TOKEN" in msg
              and ".env" in msg, msg)

        # placeholder allowlist
        fb.CONFIG["mattermost"]["token"] = "abc123"
        fb.CONFIG["mattermost"]["allowed_users"] = ["your-mattermost-user-id"]
        check("startup: the placeholder allowlist is rejected",
              "allowed_users" in (fb.validate_startup_config() or ""),
              fb.validate_startup_config())
    finally:
        fb.CONFIG["mattermost"].clear()
        fb.CONFIG["mattermost"].update(saved)


def test_console_output_survives_a_legacy_code_page():
    """Live the Windows bed, first install: `tinycmdr.py --once` died printing its own
    banner. A plain PowerShell console uses a legacy code page (cp437), which
    cannot encode the em dash, and a bare print() raises UnicodeEncodeError and
    takes the process with it. Reproduced by forcing PYTHONIOENCODING=cp437;
    fixed by reconfiguring the console streams with errors=replace."""
    import os as _os
    import subprocess as _sp

    target = str(SRC)
    child = chr(10).join([
        "import importlib.util",
        "spec = importlib.util.spec_from_file_location('fb', " + repr(target) + ")",
        "m = importlib.util.module_from_spec(spec)",
        "spec.loader.exec_module(m)",
        "print('tinycmdr CLI ' + chr(0x2014) + ' model x')",
        "print(chr(0x26a0) + ' warning glyph')",
        "print('PRINTED-OK')",
    ])

    env = dict(_os.environ)
    env["PYTHONIOENCODING"] = "cp437"
    r = _sp.run([sys.executable, "-c", child], capture_output=True, text=True,
                env=env, timeout=180)
    check("console: no UnicodeEncodeError under a legacy code page",
          "UnicodeEncodeError" not in r.stderr, (r.stderr or r.stdout)[-300:])
    check("console: the banner still prints", "PRINTED-OK" in r.stdout,
          (r.stdout or r.stderr)[-200:])

    env2 = dict(_os.environ)
    env2["PYTHONIOENCODING"] = "utf-8"
    r2 = _sp.run([sys.executable, "-c", child], capture_output=True, text=True,
                 env=env2, timeout=180)
    check("console: utf-8 consoles keep printing too", "PRINTED-OK" in r2.stdout,
          r2.stdout[-200:])
    check("console: no traceback on utf-8",
          "Traceback" not in r2.stderr and "UnicodeEncodeError" not in r2.stderr,
          r2.stderr[-200:])

    # and the glyphs must come out as real UTF-8, not cp437 mojibake: "~=" used to
    # print as the three characters "Gamma-e-quote" in a PowerShell window
    rb = _sp.run([sys.executable, "-c", child], capture_output=True, env=env,
                 timeout=180)
    check("console: the em dash is emitted as UTF-8 bytes",
          b"\xe2\x80\x94" in rb.stdout, rb.stdout[-120:])


def test_a_hand_broken_config_says_what_is_wrong():
    """Live: a config.json edited by hand had "allowed_users": [abc123] - no
    quotes - which is not JSON. That must produce a sentence naming the mistake,
    not a traceback at import, and startup validation has to report it."""
    broken = '{"mattermost": {"url": "chat.example.com", '\
             '"allowed_users": [<mattermost-user-id>]}}'
    data, err = fb.parse_config_text(broken, "config.json")
    check("json: a missing quote is reported, not raised", data is None and err,
          err)
    check("json: the message names the file and the fix",
          "not valid JSON" in err and "double quotes" in err, err)
    check("json: a good file parses quietly",
          fb.parse_config_text('{"a": [1]}')[1] is None)

    saved = dict(fb.CONFIG["mattermost"])
    saved_err = fb.CONFIG_ERROR
    try:
        fb.CONFIG_ERROR = err
        check("startup: a broken config is surfaced by validation",
              fb.validate_startup_config() == err, fb.validate_startup_config())
    finally:
        fb.CONFIG_ERROR = saved_err
        fb.CONFIG["mattermost"].clear()
        fb.CONFIG["mattermost"].update(saved)


def test_the_allowlist_tolrates_null_and_a_bare_string():
    """Two failure modes with security consequences: `uid not in None` raises,
    and a single-user *string* makes `x in allowed` a substring test, so any name
    containing it would be let through."""
    saved = dict(fb.CONFIG["mattermost"])
    try:
        fb.CONFIG["mattermost"]["allowed_users"] = None
        check("allowlist: null is deny-all, not a crash",
              fb.user_is_allowed("<mattermost-user-id>", "u1") is False)

        fb.CONFIG["mattermost"]["allowed_users"] = "<mattermost-user-id>"
        check("allowlist: a bare string still allows the exact id",
              fb.user_is_allowed("<mattermost-user-id>", None) is True)
        check("allowlist: a bare string does not allow a substring",
              fb.user_is_allowed("<unknown-id>", None) is False)
        check("allowlist: nor a longer name containing it",
              fb.user_is_allowed("x<mattermost-user-id>y", None) is False)

        fb.CONFIG["mattermost"]["allowed_users"] = ["a1", "b2"]
        check("allowlist: list membership by name", fb.user_is_allowed("a1", None) is True)
        check("allowlist: list membership by user id",
              fb.user_is_allowed("someone", "b2") is True)
        check("allowlist: an unknown user is refused",
              fb.user_is_allowed("nobody", "zz") is False)
    finally:
        fb.CONFIG["mattermost"].clear()
        fb.CONFIG["mattermost"].update(saved)


def test_startup_refuses_an_empty_allowlist():
    """An empty allowlist means the bot ignores everyone - that looks like a
    broken bot, so say it out loud instead of starting deaf."""
    if importlib.util.find_spec("mmpy_bot") is None:
        # same reason as the suite above: the missing driver is reported before
        # any allowlist message, so these checks cannot produce their subject here
        skip("startup refuses an empty allowlist",
             "mmpy_bot is not installed for this python")
        return
    saved = dict(fb.CONFIG["mattermost"])
    saved_err = fb.CONFIG_ERROR
    try:
        fb.CONFIG_ERROR = None
        fb.CONFIG["mattermost"].update({"url": "chat.example.org",
                                        "token": "abc123", "allowed_users": []})
        msg = fb.validate_startup_config() or ""
        check("startup: an empty allowlist is refused with instructions",
              "allowed_users is empty" in msg and "deny-by-default" in msg, msg)
        fb.CONFIG["mattermost"]["allowed_users"] = None
        check("startup: null is refused too",
              "allowed_users is empty" in (fb.validate_startup_config() or ""),
              fb.validate_startup_config())
        fb.CONFIG["mattermost"]["allowed_users"] = ["u1"]
        check("startup: one real id passes", fb.validate_startup_config() is None,
              fb.validate_startup_config())
    finally:
        fb.CONFIG_ERROR = saved_err
        fb.CONFIG["mattermost"].clear()
        fb.CONFIG["mattermost"].update(saved)


def test_shipped_requirements_cover_what_the_code_declares():
    """A fresh host got tinycmdr installed with only `requests`, so the Mattermost
    client (mmpy_bot) was absent: the bot started, logged two warnings and never
    connected. The declared set lives in the code header, the shipped set in
    requirements.txt, and the installer installs from that file - keep them in
    step, and require mmpy_bot specifically."""
    src = (SRC).read_text(encoding="utf-8", errors="replace")
    header = re.search(r"Dependencies:\s*pip install ([^\n]+)", src)
    check("deps: the code header declares its dependencies", bool(header),
          header.group(0) if header else "no header line")
    declared = set((header.group(1) if header else "").split())
    reqs = (BASE / "requirements.txt").read_text(encoding="utf-8")
    listed = set()
    for line in reqs.splitlines():
        line = line.split("#")[0].strip()
        if line:
            listed.add(re.split(r"[<>=!]", line)[0].strip())
    check("deps: requirements.txt covers every declared dependency",
          declared <= listed, f"declared={sorted(declared)} listed={sorted(listed)}")
    check("deps: mmpy_bot is required, not optional",
          "mmpy_bot" in listed, sorted(listed))
    installer = BASE / "install" / "install-tinycmdr.ps1"
    if installer.exists():
        # The installer must install from requirements.txt rather than a hand-written
        # list. 1.0.2 changed the SHAPE: the dependencies now go into the install's own
        # venv and the pip argv is built as an array, so the pin names the file
        # variable and its use instead of one exact command line.
        itext = installer.read_text(encoding="utf-8", errors="replace")
        check("deps: the installer installs from requirements.txt",
              '$reqFile = Join-Path $InstallDir "requirements.txt"' in itext
              and '"-r", $reqFile' in itext,
              "installer does not use the file")
    else:
        # The installer is Windows-only, so a Mac or Linux checkout has no install/:
        # asserting it there fails for the wrong reason (found on the macOS bed 2026-09-19).
        check("deps: installer absent from this tree -> installer check skipped", True)
    check("deps: a missing Mattermost client is a loud failure",
          "needs mmpy_bot" in src and "sys.exit(2)" in src, "no loud path")


def test_stall_thresholds_are_configurable_and_sane():
    fb.CONFIG["agent"]["stall_warn_minutes"] = 6
    fb.CONFIG["agent"]["stall_abandon_minutes"] = 15
    d = _dispatcher()
    check("warn threshold read from config", d._stall_warn_minutes() == 6,
          d._stall_warn_minutes())
    check("abandon threshold read from config",
          d._stall_abandon_minutes() == 15, d._stall_abandon_minutes())
    check("abandon must come after the warning",
          d._stall_abandon_minutes() > d._stall_warn_minutes())
    check("defaults exist in DEFAULT_CONFIG",
          "stall_warn_minutes" in fb.DEFAULT_CONFIG["agent"]
          and "stall_abandon_minutes" in fb.DEFAULT_CONFIG["agent"])
    fb.CONFIG["agent"]["stall_warn_minutes"] = 8
    fb.CONFIG["agent"]["stall_abandon_minutes"] = 20



# ------------------------------------------------ /stop immediacy + steering

def _redirect_state():
    """Every file write from a test goes to tmp/, never beside the package."""
    fb.NOTES_FILE = TMP / "notes.md"
    fb.NOTES_ARCHIVE_FILE = TMP / "notes-archive.md"
    fb.TASKS_FILE = TMP / "tasks.json"
    fb.TASKS_DOC = TMP / "tasks.md"
    fb.SESSIONS_DIR = TMP / "sessions"
    fb.SESSIONS_DIR.mkdir(exist_ok=True)
    fb.NOTES_FILE.write_text("", encoding="utf-8")


def test_stop_aborts_the_wait_on_an_in_flight_model_call():
    """Live on a Linux bot 2026-09-11: /stop at 19:58:29 and "Leave it alone" at
    19:58:37, and the run still executed a shell step at 19:58:57 that paused 32 curl processes
    on another host. The cancel flag was only read at the TOP of a turn, so a stop that landed
    during a model call waited for the whole reply. It has to abandon the wait instead."""
    import socket

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    held = []

    def accept_and_hang():
        try:
            conn, _ = srv.accept()
            held.append(conn)            # accepted and never answered: the client waits
            time.sleep(8)
        except OSError:
            pass

    threading.Thread(target=accept_and_hang, daemon=True).start()
    ev = threading.Event()
    timer = threading.Timer(0.4, ev.set)          # the operator's /stop, mid-call

    def call():
        timer.start()
        try:
            fb._post_watchdog(f"http://127.0.0.1:{port}/v1/chat/completions",
                              {"Content-Type": "application/json"},
                              {"model": "x", "messages": []},
                              timeout=30, grace=30, cancel_event=ev)
        except fb.OperatorStop:
            return "stopped"
        except BaseException as e:                # noqa: BLE001 - report it, never hang
            return f"{type(e).__name__}: {e}"
        return "returned"

    t0 = time.time()
    finished, out = timed(call, 25)
    elapsed = time.time() - t0
    for c in held:
        try:
            c.close()
        except OSError:
            pass
    srv.close()
    check("stop: a stop during a model call abandons the wait", finished,
          "the call was still blocked")
    check("stop: it raises OperatorStop, not an ordinary error", out == "stopped", str(out))
    check("stop: it took about as long as the operator's message, not the reply",
          elapsed < 8, f"{elapsed:.1f}s for a 30s call")
    check("stop: OperatorStop is not an Exception, so a fallback cannot swallow it",
          issubclass(fb.OperatorStop, BaseException)
          and not issubclass(fb.OperatorStop, Exception), fb.OperatorStop.__mro__)


def test_stop_landing_with_the_reply_prevents_the_tool_batch_from_running():
    """The exact shape of the 2026-09-11 mess: the model was already replying when the stop
    arrived, and the reply asked for a tool call. Running that batch is what paused the curls."""
    probe = TMP / "stop_batch_probe.txt"
    if probe.exists():
        probe.unlink()
    _redirect_state()
    fb.AGENT.histories.clear()
    saved_chat = fb.AGENT._chat
    calls = {"n": 0}
    ev = threading.Event()

    def fake_chat(messages, model=None, use_tools=True, usage=None,
                  max_tokens=None, cancel_event=None, on_delta=None, session_key=None, **kwargs):
        calls["n"] += 1
        ev.set()                     # the /stop lands while the model is replying
        return {"role": "assistant", "content": "Pausing them now.",
                "tool_calls": [{"id": "c1", "function": {
                    "name": "shell",
                    "arguments": json.dumps({"command": shell_append(probe, "ran")})}}]}

    fb.AGENT._chat = fake_chat
    try:
        out = fb.AGENT.run("stop-batch", "pause the curls",
                           cancel_event=ev, channel_id="chan-stop-batch")
    finally:
        fb.AGENT._chat = saved_chat
    check("stop: the run answers with the stop", str(out).startswith("🛑 Stopped"), str(out)[:140])
    check("stop: the tool the reply asked for was NOT executed", not probe.exists(),
          probe.read_text(encoding="utf-8", errors="replace") if probe.exists()
          else "not created")
    check("stop: the model was not asked again", calls["n"] == 1, calls)


def test_stop_guard_sits_inside_the_batch_worker():
    """Source-level on purpose. A model's tool calls are dispatched in parallel, so no run-level
    test can order "the stop arrives between call 1 and call 2" deterministically; what can be
    pinned is that the guard is there, inside work(), before anything executes."""
    src = (SRC).read_text(encoding="utf-8", errors="replace")
    i = src.find("def work(i, tc):")
    check("stop: the batch worker has a body", i > 0, "work() not found")
    if i > 0:
        body = src[i:i + 1400]
        j = body.find("cancel_event.is_set()")
        k = body.find("progress_cb(name, raw_args)")
        check("stop: the worker checks the cancel flag", j > 0, body[:200])
        check("stop: it checks BEFORE the call is announced or run",
              0 < j < k if k > 0 else j > 0, f"check@{j} announce@{k}")
        check("stop: the skipped call still gets an answer (pairing)",
              "NOT EXECUTED" in body, "no answer for the skipped call")


def test_a_correction_that_arrives_with_the_answer_gets_another_turn():
    """An answer composed before the correction is an answer to the old instruction. The run has
    to notice and take another turn, and the correction has to reach the model."""
    _redirect_state()
    fb.AGENT.histories.clear()
    saved_chat = fb.AGENT._chat
    payloads = []
    seq = [{"role": "assistant", "content": "Starting the cleanup now."},
           {"role": "assistant", "content": "Understood, leaving it alone."}]
    drains = {"n": 0}

    def fake_chat(messages, model=None, use_tools=True, usage=None,
                  max_tokens=None, cancel_event=None, on_delta=None, session_key=None, **kwargs):
        payloads.append(copy.deepcopy(messages))
        return seq.pop(0)

    def steer():
        drains["n"] += 1
        # second drain = the one that runs as the model's answer comes back
        return [("david", "Leave it alone")] if drains["n"] == 2 else []

    fb.AGENT._chat = fake_chat
    try:
        out = fb.AGENT.run("steer-session", "clean up the orphaned curls", steer_cb=steer)
    finally:
        fb.AGENT._chat = saved_chat
    check("steering: the run took another turn instead of finishing",
          len(payloads) == 2, len(payloads))
    check("steering: the correction reached the model",
          len(payloads) > 1 and any("Leave it alone" in str(m.get("content", ""))
                                    for m in payloads[1]), payloads[1] if len(payloads) > 1 else None)
    check("steering: the correction is marked as delivered-already steering",
          len(payloads) > 1 and any("delivered to the operator already" in str(m.get("content", ""))
                                    for m in payloads[1]), "")
    check("steering: with no say_cb the composed answer RIDES with the final one",
          out == "Starting the cleanup now.\n\nUnderstood, leaving it alone.",
          str(out)[:200])



def test_an_answer_that_arrives_with_the_correction_is_delivered():
    """The measured loss this pins (drive 2026-09-23, the Windows bed): the model composed
    the full task answer, steering landed in the same second, the run took another
    turn - and the run's DELIVERED response carried only the steering reply. The
    composed answer survived only as the leftover streamed draft (its text lives in
    the post's attachment props, easy to misread as an erased post): no lane treats
    draft residue as a delivery, and lanes without draft posts lose it outright.
    The composed answer must be DELIVERED (say_cb) and must not duplicate in the
    run's return."""
    _redirect_state()
    fb.AGENT.histories.clear()
    saved_chat = fb.AGENT._chat
    seq = [{"role": "assistant", "content": "The full task answer."},
           {"role": "assistant", "content": "Steering handled."}]
    said = []
    drains = {"n": 0}

    def fake_chat(messages, model=None, use_tools=True, usage=None,
                  max_tokens=None, cancel_event=None, on_delta=None, session_key=None, **kwargs):
        return seq.pop(0)

    def steer():
        drains["n"] += 1
        return [("david", "one more thing")] if drains["n"] == 2 else []

    fb.AGENT._chat = fake_chat
    try:
        out = fb.AGENT.run("steer-deliver", "do the task", steer_cb=steer,
                           say_cb=said.append)
    finally:
        fb.AGENT._chat = saved_chat
    check("steering: the composed answer was delivered through say_cb",
          "The full task answer." in said, said)
    check("steering: the steering turn's answer is the run's return",
          out == "Steering handled.", str(out)[:140])
    check("steering: with say_cb the return carries no duplicate of the answer",
          "The full task answer." not in (out or ""), str(out)[:140])


def test_a_mutation_lets_the_same_call_run_again():
    """The repeat guard must not outlive the change it was measured against.

    Found in the Windows bed's own log: it ran a tool, edited that tool with edit_file,
    re-ran the check, got identical output, and read the repeat as "the harness caching
    a pre-edit call". Had it tried a third time, the guard would have REFUSED it and
    handed back the PRE-EDIT result - after which a fixed tool looks broken. So any
    real mutation (write_file/edit_file/create_tool) clears the repeat memory.
    """
    counter = TMP / "mutation_dedupe.txt"
    if counter.exists():
        counter.unlink()
    marker = TMP / "mutation_marker.txt"
    if marker.exists():
        marker.unlink()
    cmd = shell_append(counter)
    call = {"role": "assistant", "content": "",
            "tool_calls": [{"id": "1", "function": {
                "name": "shell",
                "arguments": json.dumps({"command": cmd})}}]}
    fix = {"role": "assistant", "content": "",
           "tool_calls": [{"id": "2", "function": {
               "name": "write_file",
               "arguments": json.dumps({"path": str(marker),
                                        "content": "the fix"})}}]}
    fb.CONFIG["agent"]["loop_dedupe_after"] = 2
    fb.CONFIG["agent"]["loop_stop_repeats"] = 6
    fb.CONFIG["agent"]["max_steps"] = 100
    fb.CONFIG["agent"]["max_minutes"] = 30
    out, calls, payloads = _scripted_run([
        dict(call), dict(call), dict(fix), dict(call),
        {"role": "assistant", "content": "Done: re-checked after the fix."}])
    ticks = (counter.read_text(encoding="utf-8").count("tick")
             if counter.exists() else 0)
    transcript = json.dumps(payloads[-1] if payloads else [])
    check("mutation: the same call ran again after the change", ticks == 3, ticks)
    check("mutation: it was NOT answered from the cache",
          "NOT RE-EXECUTED" not in transcript, transcript[:300])
    check("mutation: the change itself still happened", marker.exists(),
          "the write did not happen")
    check("mutation: the run finished with an answer",
          "Done: re-checked after the fix." in out, out[:160])


def test_the_guard_still_refuses_when_nothing_changed():
    """The complement: without an intervening mutation the third identical call is still
    refused, so the anti-spin behaviour this guard exists for is intact."""
    counter = TMP / "mutation_control.txt"
    if counter.exists():
        counter.unlink()
    cmd = shell_append(counter)
    call = {"role": "assistant", "content": "",
            "tool_calls": [{"id": "1", "function": {
                "name": "shell",
                "arguments": json.dumps({"command": cmd})}}]}
    fb.CONFIG["agent"]["loop_dedupe_after"] = 2
    fb.CONFIG["agent"]["loop_stop_repeats"] = 6
    fb.CONFIG["agent"]["max_steps"] = 100
    fb.CONFIG["agent"]["max_minutes"] = 30
    out, calls, payloads = _scripted_run([dict(call), dict(call), dict(call),
                                          {"role": "assistant", "content": "Done."}])
    ticks = (counter.read_text(encoding="utf-8").count("tick")
             if counter.exists() else 0)
    check("control: still only two real executions", ticks == 2, ticks)
    check("control: the third attempt was refused",
          "NOT RE-EXECUTED" in json.dumps(payloads[-1] if payloads else []),
          "the guard stopped working")


def test_an_infrastructure_failure_files_a_red_done_line():
    """The operator asked for red to mean something is actually wrong. An endpoint that
    could not be reached is exactly that, however cleanly the run reports it, so the
    Done line must not come out white."""
    d = FakeDispatcher()
    saved = copy.deepcopy(fb.CONFIG["agent"])
    fb.CONFIG["agent"].update(progress_updates=True, colour_coded=True)
    try:
        fb.AGENT.last_usage["infra-sess"] = {"infra_failed": True}
        rep = fb.ProgressReporter(d, "chan", "root", "infra-sess")
        rep.finish(ok=True)
        check("infra failure: the Done line is red",
              d.edit_colors[-1] == fb.COLOR_FAIL, d.edit_colors)
        check("infra failure: the line still reports honestly",
              "Done" in d.edits[-1][2], d.edits[-1])
        fb.AGENT.last_usage["clean-sess"] = {}
        rep2 = fb.ProgressReporter(d, "chan", "root", "clean-sess")
        rep2.finish(ok=True)
        check("a clean run stays white", d.edit_colors[-1] == fb.COLOR_STATUS,
              d.edit_colors)
    finally:
        fb.AGENT.last_usage.pop("infra-sess", None)
        fb.AGENT.last_usage.pop("clean-sess", None)
        fb.CONFIG["agent"].clear()
        fb.CONFIG["agent"].update(saved)

def test_a_cap_with_work_left_continues_the_task_instead_of_ending_it():
    """Measured 2026-09-17: 17 real runs ended on a step or wall-clock cap, and every one of
    them cost the operator a "continue". A cap with plan steps still open is a checkpoint:
    the run must open a fresh segment on the same task, say so once, and stop only when the
    continuation budget is spent (or the plan is complete)."""
    probes = []
    for i in range(6):
        p = TMP / f"continue_probe_{i}.txt"
        p.write_text(f"stable {i}\n", encoding="utf-8")
        probes.append(p)
    scripted = [{"role": "assistant", "content": "",
                 "tool_calls": [{"id": str(i), "function": {
                     "name": "read_file",
                     "arguments": json.dumps({"path": str(p)})}}]}
                for i, p in enumerate(probes)]
    scripted.append({"role": "assistant", "content": "Final: read all six."})

    saved_chat = fb.AGENT._chat
    saved = dict(fb.CONFIG["agent"])
    said, used = [], {"n": 0}
    seq = list(scripted)

    def fake_chat(messages, model=None, use_tools=True, usage=None, max_tokens=None,
                  cancel_event=None, on_delta=None, session_key=None, **kw):
        if used["n"] >= len(seq):
            raise AssertionError("the run kept asking the model past its segments")
        r = seq[used["n"]]
        used["n"] += 1
        return r

    key = "continue-session"
    fb.AGENT._chat = fake_chat
    fb.reset_read_counts(key)
    fb.run_state(key, create=True)["plan"] = [{"id": 1, "text": "read them all",
                                               "status": "doing", "note": ""}]
    fb.CONFIG["agent"].update(max_steps=2, max_minutes=30, auto_continue=True,
                              auto_continue_max=1, plan_from_request=False,
                              progress_updates=False)
    try:
        out = fb.AGENT.run(key, "read the probe files one by one", say_cb=said.append)
    finally:
        fb.AGENT._chat = saved_chat
        fb.CONFIG["agent"].clear()
        fb.CONFIG["agent"].update(saved)
        fb.run_state(key, create=True)["plan"] = []

    check("continuing: the operator is told once", len(said) == 1, said)
    check("continuing: the notice names the segment and the open work",
          said and "segment 2 of 2" in said[0] and "plan step" in said[0], said)
    check("continuing: work carried on past the first cap", used["n"] > 3, used["n"])
    check("continuing: it stops when the segments run out",
          "Budget reached" in out, out[:120])


def test_a_finished_plan_ends_the_run_at_the_cap_without_continuing():
    """The other half of the rule: when every plan step is done there is nothing to
    continue, so the cap must end the run (a continuation there would burn a budget on
    work nobody asked for)."""
    p = TMP / "finished_probe.txt"
    p.write_text("stable\n", encoding="utf-8")
    call = {"role": "assistant", "content": "",
            "tool_calls": [{"id": "1", "function": {
                "name": "read_file", "arguments": json.dumps({"path": str(p)})}}]}
    saved_chat = fb.AGENT._chat
    saved = dict(fb.CONFIG["agent"])
    said, used = [], {"n": 0}
    seq = [dict(call), dict(call), {"role": "assistant", "content": "Final: done."}]

    def fake_chat(messages, model=None, use_tools=True, usage=None, max_tokens=None,
                  cancel_event=None, on_delta=None, session_key=None, **kw):
        if used["n"] >= len(seq):
            raise AssertionError("a finished plan must not continue")
        r = seq[used["n"]]
        used["n"] += 1
        return r

    key = "done-session"
    fb.AGENT._chat = fake_chat
    fb.run_state(key, create=True)["plan"] = [{"id": 1, "text": "the only step",
                                               "status": "done", "note": ""}]
    fb.CONFIG["agent"].update(max_steps=1, max_minutes=30, auto_continue=True,
                              auto_continue_max=2, plan_from_request=False,
                              progress_updates=False)
    try:
        out = fb.AGENT.run(key, "read it twice", say_cb=said.append)
    finally:
        fb.AGENT._chat = saved_chat
        fb.CONFIG["agent"].clear()
        fb.CONFIG["agent"].update(saved)
        fb.run_state(key, create=True)["plan"] = []

    check("finished plan: nothing is continued", said == [], said)
    check("finished plan: the cap still ends the run", "Budget reached" in out, out[:120])


def test_the_second_read_of_a_file_leaves_with_its_map():
    """24 of the 40 code calls in the 2026-09-18 run were another whole-file read of the
    SAME 8,700-line source (measured on the Windows bed). A path read twice in one run leaves
    with its index attached, so the next question goes to a region instead of through the
    whole file."""
    big = TMP / "big_source.py"
    if not big.exists():
        big.write_text("import os\n\n\ndef alpha():\n    return 1\n"
                       + "\n".join(f"def f{i}():\n    return {i}" for i in range(300)),
                       encoding="utf-8")
    fb.reset_read_counts("readmap-session")
    ctx = {"session_key": "readmap-session"}
    first = fb.annotate_repeat_read("read_file", {"path": str(big)}, "BODY", ctx)
    second = fb.annotate_repeat_read("read_file", {"path": str(big)}, "BODY", ctx)
    check("read map: the first read is untouched", first == "BODY", first[:60])
    check("read map: the second read carries the index",
          "alpha" in second and "f200" in second, second[-160:])
    check("read map: it says how often and what to do instead",
          "read 2 times" in second and "offset/limit" in second, second[:160])
    check("read map: a write to the same path is not a read",
          fb.annotate_repeat_read("write_file", {"path": str(big)}, "BODY", ctx) == "BODY")
    check("read map: a small file has no map worth attaching",
          fb.annotate_repeat_read("read_file", {"path": str(TMP / "continue_probe_0.txt")},
                                  "BODY", ctx) == "BODY")


def test_a_source_map_never_slurps_a_big_file():
    """The 2026-09-19 OOM, in one line: a tool call that named the model path twice
    attached a source map, source_map_text() read_bytes() the whole GGUF, and the bot
    died. a bot account measured it: 45,979,016 kB mapped against a 47,039,860,096 B file (the
    fnx IQ3_XXS shard), 194 GB anon-rss at the 15:23 kill. Gate the SIZE, before the read.
    """
    cap = fb._MAP_MAX_BYTES
    big = TMP / "huge-model.gguf"
    with open(big, "wb") as fh:
        fh.truncate(cap * 2 + 4096)              # sparse: no disk cost, real st_size
    fb._MAP_CACHE.clear()
    check("a file over the ceiling gets no source map",
          fb.source_map_text(str(big)) == "", fb.source_map_text(str(big))[:80])
    check("and nothing was cached for it",
          str(big) not in fb._MAP_CACHE, list(fb._MAP_CACHE))
    check("_read_text_any refuses it before reading",
          fb._read_text_any(fb.Path(big), cap) == "", "read it anyway")


def test_the_map_cache_stays_bounded():
    """An entry holds a whole file's text, so an unbounded cache is its own leak."""
    fb._MAP_CACHE.clear()
    for i in range(fb._MAP_CACHE_MAX + 3):
        f = TMP / f"map-probe-{i}.txt"
        f.write_text("def f():\n    return 1\n" * 250, encoding="utf-8")
        fb.source_map_text(str(f))
    check("the map cache is capped", len(fb._MAP_CACHE) <= fb._MAP_CACHE_MAX,
          len(fb._MAP_CACHE))


def test_list_tools_is_one_line_and_still_useful():
    """615 calls and 0.46 MB of context over ten days on the manager box were spent re-reading a
    list that is already in the model's own schema block. The one thing it cannot see is
    the CUSTOM tools on this box, so that is what the answer carries now."""
    out = fb.tool_list_tools({}, {})
    check("list_tools answers in one short line", len(out) < 220, len(out))
    check("it does not re-list the core tools", "shell," not in out and "read_file," not in out,
          out[:160])
    check("it points at the schema block instead", "schema list" in out, out[:160])


def test_fetch_url_cannot_blow_up_the_context():
    """The largest page in the Windows bed's ten-day log was 30,048 chars, 17.6% of that host's
    result chars: the model may ask for more than the 8k default, not for 30 KB."""
    had = hasattr(fb, "requests")
    saved_get = getattr(fb.requests, "get", None) if had else None

    class R:
        """A streaming response: encoding + iter_content + close, the interface
        tool_fetch_url reads since the cap landed (2026-09-22)."""

        status_code = 200
        encoding = "utf-8"

        def raise_for_status(self):
            return None

        def iter_content(self, size=65536):
            body = b"x" * 40000
            for i in range(0, len(body), size):
                yield body[i:i + size]

        def close(self):
            return None

    saved_ceil = fb.CONFIG["agent"].get("fetch_max_chars")
    fb.CONFIG["agent"]["fetch_max_chars"] = 12000
    fb.requests.get = lambda *a, **kw: R()
    try:
        out = fb.tool_fetch_url({"url": "https://example.com/big", "max_chars": 30000}, {})
        check("a 30k request is clamped to the ceiling", len(out) < 14000, len(out))
        check("and the spill pointer is there for the rest", "spill" in out.lower() or
              "read_file" in out, out[-200:])

        # the CAP: a response far larger than the tool can use must not be read into
        # memory. 2,000 chunks of 64 KB is 128 MB if it is drained; the budget for an 8k
        # result is 64 KB, so this fails loudly on the old materialise-then-trim path.
        class Big:
            status_code = 200
            encoding = "utf-8"

            def __init__(self):
                self.pulled = 0
                self.closed = False

            def raise_for_status(self):
                return None

            def iter_content(self, size=65536):
                for _ in range(2000):
                    self.pulled += 65536
                    yield b"x" * 65536

            def close(self):
                self.closed = True

        big = Big()
        fb.requests.get = lambda *a, **kw: big
        out = fb.tool_fetch_url({"url": "https://example.com/huge", "max_chars": 8000}, {})
        # one chunk of slack: the budget is checked after each chunk lands
        check("a huge response is read only as far as the cap allows",
              big.pulled <= 8000 * 8 + 65536, big.pulled)
        check("and it is NOT drained (the whole body was 128 MB)",
              big.pulled < 200000, big.pulled)
        check("and the response is closed, not left hanging", big.closed)
    finally:
        if had and saved_get is not None:
            fb.requests.get = saved_get
        fb.CONFIG["agent"]["fetch_max_chars"] = saved_ceil


def test_the_safety_seatbelt_covers_execute_code_too():
    """2026-09-18: `is_blocked` had exactly one call site, in tool_shell, so every pattern in
    blocked_patterns was one `execute_code` away from being bypassed - and the system prompt
    admitted it. The check on the source text is a seatbelt, not a boundary, but it has to be
    there."""
    safe = fb.tool_execute_code({"code": "print('harmless')"}, {"session_key": "belt-s"})
    check("execute_code: ordinary code still runs", "harmless" in safe, safe[:120])
    for bad in ("import os; os.system('rm -rf /')",
                "import subprocess; subprocess.run('mkfs.ext4 /dev/sda1', shell=True)"):
        out = fb.tool_execute_code({"code": bad}, {"session_key": "belt-s"})
        check(f"execute_code: blocked -> {bad[:32]}...", out.startswith("BLOCKED:"), out[:160])
    out = fb.tool_shell({"command": "Remove-Item -Recurse -Force C:\\Windows"},
                        {"session_key": "belt-s"})
    # The tier moved on 2026-09-22 (operator-approved): a reversible recursive
    # delete now ASKS instead of being refused outright. What must never happen
    # is a pass-through, so this asserts the shape is still caught - by whichever
    # tier owns it - and that disk-level shapes are still unappealable blocks.
    check("shell: the same shape is still caught, never passed through",
          out.startswith(("DECLINED", "BLOCKED")), out[:160])
    check("shell: Remove-Item -Recurse is the CONFIRM tier now",
          out.startswith("DECLINED") and "confirmation" in out, out[:200])
    check("shell: a filesystem-level wipe is STILL an unappealable block",
          fb.tool_shell({"command": "mkfs.ext4 /dev/sda1"},
                        {"session_key": "belt-s"}).startswith("BLOCKED:"))

    # The write paths take the CONFIRM tier too (security review 2026-09-23):
    # the fastest route for a steered model is a file or a tool, not a command.
    wp = TMP / "belt-write.txt"
    # TMP persists between runs: a leftover here made "declined before it lands"
    # fail on the second sweep (2026-09-23). Start from no file.
    if wp.exists():
        wp.unlink()
    out = fb.tool_write_file(
        {"path": str(wp), "content": "step one\nRemove-Item -Recurse -Force C:\\Temp\n"},
        {"session_key": "belt-s"})
    check("write_file: a confirm-tier payload is declined before it lands",
          out.startswith("DECLINED") and not wp.exists(), out[:160])
    out = fb.tool_write_file({"path": str(wp), "content": "plain line\n"},
                             {"session_key": "belt-s"})
    check("write_file: ordinary content still writes",
          wp.exists() and out.startswith("OK:"), out[:160])
    out = fb.tool_edit_file(
        {"path": str(wp), "old_string": "plain line",
         "new_string": "rd /s /q C:\\Temp"},
        {"session_key": "belt-s"})
    check("edit_file: a confirm-tier new_string is declined",
          out.startswith("DECLINED") and "plain line" in wp.read_text(
              encoding="utf-8"), out[:160])
    out = fb.tool_create_tool(
        {"name": "belt_probe",
         "code": "NAME = 'belt_probe'\n# Remove-Item -Recurse -Force boom\n"},
        {"session_key": "belt-s"})
    check("create_tool: confirm-tier code is declined before it is written",
          out.startswith("DECLINED")
          and not (fb.TOOLS_DIR / "belt_probe.py").exists(), out[:160])

    # The pattern guard (security review 2026-09-23): a catastrophic operator
    # regex is flagged when the tier compiles, before it can stall a run.
    import logging as _logging
    seen = []
    h = _logging.Handler()
    h.emit = lambda r: seen.append(r.getMessage())
    fb.log.addHandler(h)
    saved_pats = fb.CONFIG["agent"]["blocked_patterns"]
    try:
        fb.CONFIG["agent"]["blocked_patterns"] = saved_pats + [r"(a+)+$"]
        fb._PATTERN_CACHE.clear()
        fb.is_blocked("b")     # compiles the tier; "b" fails every pattern fast
        check("guard: a catastrophic pattern is flagged at compile time",
              any("catastrophic" in m for m in seen), seen)
    finally:
        fb.CONFIG["agent"]["blocked_patterns"] = saved_pats
        fb._PATTERN_CACHE.clear()
        fb.log.removeHandler(h)


def test_a_run_that_keeps_announcing_completion_is_forced_to_deliver():
    """Measured live on the Windows bed 2026-09-18: the model announced "fresh pass complete" five
    times in 25 minutes and answered every announcement with another tool round, while every
    call was distinct, so no repeat-based guard could see it. The delivery guard counts
    announcements that arrive with tool calls queued, demands the report, then forces it."""
    import copy as _copy
    probe = TMP / "announce_probe.txt"
    probe.write_text("stable\n", encoding="utf-8")
    def round_n(n):
        return {"role": "assistant",
                "content": f"Fresh pass {n} is complete. One last check before writing it up.",
                "tool_calls": [{"id": str(n), "function": {
                    "name": "read_file",
                    "arguments": json.dumps({"path": str(probe) + "-" + str(n)})}}]}
    # distinct paths so the loop guard cannot fire first, and one honest answer at the end
    for n in range(1, 8):
        (TMP / f"announce_probe.txt-{n}").write_text(f"stable {n}\n", encoding="utf-8")
    scripted = [round_n(n) for n in range(1, 9)]
    scripted.append({"role": "assistant", "content": "Final report: nothing left undone."})

    saved_chat = fb.AGENT._chat
    saved = dict(fb.CONFIG["agent"])
    seen, payloads, used = [], [], {"n": 0}
    seq = list(scripted)

    def fake_chat(messages, model=None, use_tools=True, usage=None, max_tokens=None,
                  cancel_event=None, on_delta=None, session_key=None, **kw):
        payloads.append(_copy.deepcopy(messages))
        if used["n"] >= len(seq):
            raise AssertionError("the run kept asking the model past the forced wrap-up")
        r = seq[used["n"]]
        used["n"] += 1
        return r

    key = "announce-session"
    fb.AGENT._chat = fake_chat
    fb.run_state(key, create=True)["plan"] = [{"id": 1, "text": "the pass", "status": "doing"}]
    fb.CONFIG["agent"].update(max_steps=100, max_minutes=30, auto_continue=True,
                              auto_continue_max=2, plan_from_request=False,
                              deliver_after_announcements=3, progress_updates=False)
    try:
        out = fb.AGENT.run(key, "do the pass and report", say_cb=seen.append)
    finally:
        fb.AGENT._chat = saved_chat
        fb.CONFIG["agent"].clear()
        fb.CONFIG["agent"].update(saved)
        fb.run_state(key, create=True)["plan"] = []
        fb.run_state(key, create=True)["announced"] = 0

    blobs = [json.dumps(p) for p in payloads]
    check("delivery guard: the announcement nudge reached the model",
          any("announced this work as complete" in b for b in blobs), len(payloads))
    check("delivery guard: the wrap-up was forced, not a budget",
          "Delivery guard" in out, out[:160])
    check("delivery guard: no continuation was granted to it",
          all("segment" not in (s or "") for s in seen), seen)


# --------------------------------------------------------------------------
# the restatement guard (macOS bed, 2026-09-24)
# --------------------------------------------------------------------------
# Measured: a stuck run described the SAME status EIGHT times in three minutes
# while every call it made failed and nothing changed. The calls were different,
# so the identical-call guard never fired; the sentences were reworded after the
# first few words, so the exact-match guard never fired either. The operator read
# the same sentence eight times with no way to tell it apart from progress.

def _restate_script(head, tails):
    """N turns that open with the same status and end in a call that FAILS."""
    scripted = []
    for i, tail in enumerate(tails):
        scripted.append({"role": "assistant", "content": f"{head} {tail}",
                         "tool_calls": [{"id": f"r{i}", "function": {
                             "name": "read_file",
                             "arguments": json.dumps(
                                 {"path": str(TMP / f"missing-{i}.txt")})}}]})
    return scripted


def test_a_run_that_only_restates_itself_ends_itself():
    # The MEASURED shape: every line opens "The video is already downloaded (9.2 MB"
    # and diverges after it, which is what made an exact-match guard useless.
    tails = ["at /tmp/download/Interesting.mp4)",
             ", from an earlier run)",
             " in /tmp/download, unchanged)",
             ", still there from the earlier run)",
             " at /tmp/download/Interesting.mp4, nothing new)",
             ", the same file I downloaded before)",
             " in /tmp/download, untouched)",
             ", unchanged since the last check)",
             " - the same download, nothing on disk changed)",
             ", still 9.2 MB, still nothing to do)"]
    fb.CONFIG["agent"]["restate_nudge_after"] = 3
    fb.CONFIG["agent"]["restate_stop_after"] = 6
    fb.CONFIG["agent"]["max_steps"] = 60
    fb.CONFIG["agent"]["max_minutes"] = 20
    out, calls, payloads = _scripted_run(
        _restate_script("The video is already downloaded (9.2 MB", tails))
    sent = json.dumps(payloads)
    check("restated run: the model is told once, in the results it reads",
          "described the same status 3 times" in sent, sent[-300:])
    check("restated run: the run ends with its own reason",
          "kept restating the same status" in out, out[:300])
    # Ten turns are scripted and the stop belongs on the EIGHTH: the first turn
    # STATES the status, the sixth restatement of it is the one that ends the run.
    # Two turns left over is what proves the run ended itself rather than running
    # the script out (the stub raises if it is asked for an eleventh).
    check("restated run: it stops at the threshold, not the step ladder",
          calls == 8, f"model calls: {calls}")
    check("restated run: the report it already had still goes out",
          "Stopped a loop" in out, out[:200])


def test_a_run_whose_status_changes_is_not_stopped_by_it():
    scripted = [{"role": "assistant",
                 "content": f"Step {i}: checking item {i} of the list",
                 "tool_calls": [{"id": f"s{i}", "function": {
                     "name": "read_file",
                     "arguments": json.dumps(
                         {"path": str(TMP / f"missing-{i}.txt")})}}]}
                for i in range(6)]
    scripted.append({"role": "assistant",
                     "content": "Done: nothing else to check."})
    fb.CONFIG["agent"]["max_steps"] = 60
    fb.CONFIG["agent"]["max_minutes"] = 20
    out, calls, payloads = _scripted_run(scripted)
    sent = json.dumps(payloads)
    check("changing status: no restatement nudge",
          "described the same status" not in sent, sent[-200:])
    check("changing status: the run ends on its own answer",
          "nothing else to check" in out, out[:200])
    check("changing status: every scripted turn was used",
          calls == 7, f"model calls: {calls}")


def test_a_write_clears_the_restatement_count():
    """Real work resets the count: the promise is that any write or edit starts
    the count over, so a legitimate verify-after-fix is never read as a stuck
    status. Counted straight through, the identical status would trip a stop of 3
    in the middle of the task."""
    probe = TMP / "restate_probe.txt"
    probe.write_text("constant\n", encoding="utf-8")
    status = "Checking whether the file is still the one I downloaded"

    def rd(cid):
        return {"role": "assistant", "content": f"{status} (9.2 MB)",
                "tool_calls": [{"id": cid, "function": {
                    "name": "read_file",
                    "arguments": json.dumps({"path": str(probe)})}}]}

    write = {"role": "assistant", "content": f"{status} (9.2 MB)",
             "tool_calls": [{"id": "w", "function": {
                 "name": "write_file",
                 "arguments": json.dumps({"path": str(probe),
                                          "content": "constant\n",
                                          "no_backup": True})}}]}
    fb.CONFIG["agent"]["restate_nudge_after"] = 2
    fb.CONFIG["agent"]["restate_stop_after"] = 3
    fb.CONFIG["agent"]["max_steps"] = 60
    fb.CONFIG["agent"]["max_minutes"] = 20
    out, calls, payloads = _scripted_run(
        [rd("1"), rd("2"), write, rd("3"), rd("4"),
         {"role": "assistant", "content": "Done: re-checked after the write."}])
    check("a write: the count starts over instead of stopping the run",
          "Stopped a loop" not in out, out[:300])
    check("a write: the run ends with its own answer",
          "re-checked after the write" in out, out[:200])
    check("a write: every scripted turn was used", calls == 6,
          f"model calls: {calls}")


def test_the_restatement_nudge_is_queued_with_the_batch():
    """Source shape, because the failure it prevents is a 400 from a strict
    provider: a user message landing between the tool results of a batched turn.
    The loop guard's nudge stays the FIRST `nudges.append(` in the file; the
    restatement nudge goes in with the batch, after it."""
    src = (SRC).read_text(encoding="utf-8")
    i = src.find("nudges.append(")
    j = src.find("nudges.append(_restate_note)")
    k = src.find("for _nudge in nudges:")
    check("the loop guard's nudge is still the first one queued",
          0 <= i < j, f"{i} {j}")
    check("the restatement nudge is queued with the batch, after it",
          i < j < k, f"{i} {j} {k}")
    check("and it is not appended inside the per-call loop",
          src.find("if _restate_note:") < k, f"{src.find('if _restate_note:')} {k}")

def main():
    # The chat-only tests are skipped when this build has no chat layer at all.
    CHATLESS = not hasattr(fb, "MattermostDispatcher")
    CHAT_ONLY = ("test_restart_", "test_an_explicit_cloud_model", "test_cloud_failover",
                 "test_a_cloud_stream_without_usage", "test_a_local_model_stays_local", "test_a_spawned_replacement", "test_streaming_asks_for_usage")
    chatless_skips = []
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    only = sys.argv[1] if len(sys.argv) > 1 else ""
    for t in tests:
        if only and only not in t.__name__:
            continue
        if CHATLESS and t.__name__.startswith(CHAT_ONLY):
            chatless_skips.append(t.__name__)
            continue
        try:
            t()
        except Exception as e:
            import traceback
            FAILURES.append(f"{t.__name__} raised: {e}")
            traceback.print_exc()
    _tail = ("" if not chatless_skips
             else f", {len(chatless_skips)} skipped (chat build only)")
    if SKIPPED:
        _tail += f", {len(SKIPPED)} skipped (subject not available on this host)"
    print(f"\n{len(PASSES)} passed, {len(FAILURES)} failed{_tail}")
    for f in FAILURES:
        print("  FAIL:", f)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())

"""process: background a long job, then poll, wait, read or kill it.

The shell tool's timeout kills whatever it started, so builds, downloads and
servers are unrunnable there. This keeps a job table (logs/process-jobs.json)
and one log per job (logs/proc-<id>.log) under the install folder, so a status
survives a restart of the agent - the child runs on, and the table says where
its output is.

  start   command -> id + pid + log path (output lands in the log as it runs)
  status  running or finished, and the exit code when this session saw it
  wait    block up to timeout seconds for the job to finish
  output  tail the job's log
  kill    stop the job AND whatever it started (taskkill /T, killpg)
  list    the whole table
"""
import ctypes
import contextlib
import json
import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

NAME = "process"
DESCRIPTION = ("Run a long job in the background and come back for it: start "
               "returns an id, then status/wait/output/kill/send. For builds and "
               "servers that outlive a shell timeout. The table survives a "
               "restart and status still reports the real exit code; kill stops "
               "the job's whole process tree; start can wait for readiness "
               "(wait_for) and send answers a prompt on the job's stdin.")
SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string",
                   "enum": ["start", "status", "wait", "output", "kill", "send",
                            "list"]},
        "command": {"type": "string",
                    "description": "start: the command - a shell string, or an "
                                   "argv list for no shell"},
        "id": {"type": "string", "description": "the job id from start, e.g. b3"},
        "timeout": {"type": "integer",
                    "description": "wait: seconds to block (default 60, max 300)"},
        "lines": {"type": "integer", "description": "output: tail size (default 40)"},
        "text": {"type": "string",
                 "description": "send: a line of stdin for the running job (a "
                                "prompt answer, a 'y', a stop word)"},
        "wait_for": {"type": "object",
                     "description": "start: wait until the job is READY before "
                                    "returning - {\"log\": \"<regex>\", \"port\": "
                                    "1234, \"timeout\": 30}. Both, when both are "
                                    "given, must pass; timedOut is reported without "
                                    "killing the job."},
    },
    "required": ["action"],
}
MUTATES = True

BASE = Path(__file__).resolve().parent.parent
JOBS_FILE = BASE / "logs" / "process-jobs.json"
_PROCS = {}          # id -> Popen, for jobs THIS session started
STILL_ACTIVE = 259

# ONE lock around every job-table read-modify-write. The table used to be read and
# written with no lock at all, so six concurrent starts each read the same copy, each
# picked `b1`, and six jobs shared one log. A sibling lock
# file (flock/msvcrt) covers other processes, a re-entrant lock covers the threads a
# batch's tool calls run on, and a bounded wait means a lock that cannot be taken
# proceeds anyway - the save is still an atomic replace, so a reader never sees a
# spliced table.
_JOBS_LOCK = threading.RLock()
_JOBS_LOCK_DEPTH = threading.local()
_JOBS_LOCK_FILE = JOBS_FILE.with_name(JOBS_FILE.name + ".lock")
_JOBS_LOCK_WAIT = 20.0
_JOBS_LOCK_POLL = 0.02


def _jobs_file_lock():
    """The table's lock file, taken non-blocking with a bounded wait. (fd, held)."""
    try:
        _JOBS_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(_JOBS_LOCK_FILE),
                     os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o666)
    except OSError:
        return None, False
    deadline = time.time() + _JOBS_LOCK_WAIT
    while True:
        try:
            if os.name == "nt":
                import msvcrt
                os.lseek(fd, 0, 0)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd, True
        except OSError:
            if time.time() >= deadline:
                return fd, False       # never fatal: a lock must not freeze the tool
            time.sleep(_JOBS_LOCK_POLL)


def _jobs_file_unlock(fd, held):
    if fd is None:
        return
    try:
        if held:
            try:
                if os.name == "nt":
                    import msvcrt
                    os.lseek(fd, 0, 0)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


@contextlib.contextmanager
def _table_lock():
    """Hold the job table for one read-modify-write. Re-entrant per thread, so `start`
    can allocate an id and `adopt` can take it again inside the same call."""
    with _JOBS_LOCK:
        depth = getattr(_JOBS_LOCK_DEPTH, "n", 0)
        if depth:
            _JOBS_LOCK_DEPTH.n = depth + 1
            try:
                yield
            finally:
                _JOBS_LOCK_DEPTH.n -= 1
            return
        fd, held = _jobs_file_lock()
        _JOBS_LOCK_DEPTH.n = 1
        try:
            yield
        finally:
            _JOBS_LOCK_DEPTH.n = 0
            _jobs_file_unlock(fd, held)

# Every job runs through this tiny wrapper, for two things the bare Popen could not do:
#   * write `__EXIT__<rc>` into the log, so a job that outlives the process that started it
#     still reports its real exit code (the table used to say "code not captured");
#   * forward a spool file to the child's stdin, so `action=send` can answer a prompt
#     (an installer, a REPL, a 'y/n') on any platform - no FIFO, no daemon.
_WRAPPER = r'''
import json, subprocess, sys, time
argv = json.loads(sys.argv[1])
shell = sys.argv[2] == "1"
spool = sys.argv[3] or ""
proc = subprocess.Popen(argv, shell=shell, stdout=sys.stdout.fileno(),
                        stderr=subprocess.STDOUT, stdin=subprocess.PIPE)
offset = 0
try:
    while True:
        if spool:
            try:
                with open(spool, "rb") as fh:
                    fh.seek(offset)
                    chunk = fh.read()
                if chunk:
                    offset += len(chunk)
                    proc.stdin.write(chunk)
                    proc.stdin.flush()
            except (OSError, ValueError):
                pass
        if proc.poll() is not None:
            break
        time.sleep(0.2)
finally:
    try:
        proc.stdin.close()
    except Exception:
        pass
rc = proc.wait()
sys.stdout.flush()
print("__EXIT__%d" % rc, flush=True)
sys.exit(rc if rc >= 0 else 1)
'''


def _log_exit_code(log):
    """The last `__EXIT__<n>` marker in a job log, or None (an old job has none)."""
    try:
        size = os.path.getsize(log)
        with open(log, "rb") as fh:
            fh.seek(max(0, size - 4096))
            tail = fh.read().decode("utf-8", "replace")
    except OSError:
        return None
    last = None
    for hit in re.finditer(r"__EXIT__(-?\d+)", tail):
        last = hit
    return int(last.group(1)) if last else None


def _ready(job, wait_for):
    """(ready, why) for a start's readiness condition, checked NOW."""
    ok = True
    why = []
    pat = str(wait_for.get("log") or "").strip()
    if pat:
        try:
            text = Path(job["log"]).read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        if re.search(pat, text):
            why.append("log matched")
        else:
            ok = False
            why.append("log has not matched %r yet" % pat[:40])
    port = wait_for.get("port")
    if port:
        try:
            with socket.create_connection(("127.0.0.1", int(port)), timeout=0.5):
                why.append("port %s open" % port)
        except OSError:
            ok = False
            why.append("port %s not open yet" % port)
    return ok, ", ".join(why) or "no condition"


def _free_jid(jobs):
    n = 1
    while f"b{n}" in jobs:
        n += 1
    return f"b{n}"


def _new_row(jid, cmd_text):
    """A job's row with the shape `list`/`status` expect. `pid` is None until the
    process is recorded (a reserved id is a row, not a hole)."""
    return {"pid": None, "command": cmd_text,
            "log": str(BASE / "logs" / f"proc-{jid}.log"),
            "in": str(BASE / "logs" / f"proc-{jid}.in"),
            "started": time.strftime("%Y-%m-%d %H:%M:%S")}


def next_jid():
    """The next free id, RESERVED in the table before it is returned.

    Allocation and the save are one locked read-modify-write: two callers can never be
    handed the same id and then share one log. The reservation is what closes the gap
    in this tool's own caller - it allocates here and records the process in
    promote_probe() a moment later - and an id reserved by a start that never finished
    is a visible row rather than a collision."""
    with _table_lock():
        jobs = _load()
        jid = _free_jid(jobs)
        jobs[jid] = _new_row(jid, "")
        _save(jobs)
        return jid


def _drop_reserved(jid):
    """Forget a reserved id whose process never started."""
    with _table_lock():
        jobs = _load()
        if jobs.pop(jid, None) is not None:
            _save(jobs)


def spawn_detached(jid, argv, shell):
    """Start a wrapped job WITHOUT touching the table. Returns (proc, log_path, spool).

    The shell tool's auto-background probes a command with this: a command that finishes
    inside the window is discarded with no table entry and no log left behind, and only
    one that actually outlives the window is adopted.
    """
    log_path = BASE / "logs" / f"proc-{jid}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    spool = BASE / "logs" / f"proc-{jid}.in"
    try:
        spool.write_bytes(b"")
    except OSError:
        pass
    with open(log_path, "wb") as fout:
        proc = subprocess.Popen(
            [sys.executable, "-c", _WRAPPER, json.dumps(argv),
             "1" if shell else "0", str(spool)],
            stdout=fout, stderr=fout, stdin=subprocess.DEVNULL,
            cwd=str(BASE), **_hidden())
    return proc, log_path, spool


def adopt(jid, proc, cmd_text, log_path, spool):
    """Record a detached job in the table, the moment it outlives its probe window."""
    with _table_lock():
        jobs = _load()
        jobs[jid] = {"pid": proc.pid, "command": cmd_text, "log": str(log_path),
                     "in": str(spool),
                     "started": time.strftime("%Y-%m-%d %H:%M:%S")}
        _PROCS[jid] = proc
        _save(jobs)


def discard_detached(log_path, spool):
    """A probe that finished in time leaves nothing behind: no row, no files."""
    for p in (log_path, spool):
        try:
            Path(p).unlink()
        except OSError:
            pass


def read_job_log(log_path, strip_marker=True):
    """The job log's text, with the wrapper's `__EXIT__` marker line removed."""
    try:
        text = Path(log_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if strip_marker:
        text = re.sub(r"\n?__EXIT__-?\d+\s*$", "", text)
    return text


def spawn_probe(argv, shell):
    """Start a wrapped job with its output held IN MEMORY; nothing touches the tree.

    Returns (proc, state). The shell tool probes every command with this: one that
    finishes inside the window is discarded with no row, no log and no directory; one
    that outlives it is promoted (promote_probe) into the real table.

    ONE reader thread owns the pipe for the probe's whole life. While `sink` is None its
    chunks land in `state["buf"]`; promote_probe sets a file sink and the same thread
    keeps writing there - so there is no handover between two readers (that handover was
    a 2-second stall while the drain thread sat in a blocking read).

    The stdin spool lives in the temp dir for the same reason: a probe that never
    becomes a job must not leave a file where jobs live.
    """
    spool = Path(tempfile.gettempdir()) / ("tinycmdr-probe-%d.in" % os.getpid())
    state = {"buf": bytearray(), "lock": threading.Lock(), "sink": None,
             "done": threading.Event(), "spool": None}
    try:
        spool.write_bytes(b"")
        state["spool"] = str(spool)
    except OSError:
        state["spool"] = ""
    proc = subprocess.Popen(
        [sys.executable, "-c", _WRAPPER, json.dumps(argv), "1" if shell else "0",
         state["spool"] or ""],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL, cwd=str(BASE), **_hidden())

    def drain():
        sink = None
        try:
            while True:
                chunk = proc.stdout.read(4096)
                if not chunk:
                    break
                with state["lock"]:
                    if sink is None:
                        sink = state["sink"]
                if sink is None:
                    with state["lock"]:
                        state["buf"].extend(chunk)
                else:
                    sink.write(chunk)
        finally:
            if sink is not None:
                sink.close()
            state["done"].set()

    threading.Thread(target=drain, daemon=True, name="job-probe").start()
    return proc, state


def promote_probe(jid, proc, state, cmd_text):
    """Adopt a probe: everything read so far lands in logs/proc-<jid>.log and the same
    reader keeps writing there until the job ends. Returns (log_path, spool)."""
    log_path = BASE / "logs" / f"proc-{jid}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with state["lock"]:
        data = bytes(state["buf"])
        state["buf"].clear()
    log_path.write_bytes(data)
    fh = open(log_path, "ab", buffering=0)
    with state["lock"]:
        state["sink"] = fh
        rest = bytes(state["buf"])
        state["buf"].clear()
    if rest:
        fh.write(rest)
    with _table_lock():
        jobs = _load()
        jobs[jid] = {"pid": proc.pid, "command": cmd_text, "log": str(log_path),
                     "in": state.get("spool") or "",
                     "started": time.strftime("%Y-%m-%d %H:%M:%S")}
        _PROCS[jid] = proc
        _save(jobs)
    return log_path, state.get("spool") or ""


def discard_probe(state):
    """A probe that finished in time leaves nothing behind."""
    spool = state.get("spool")
    if spool:
        try:
            Path(spool).unlink()
        except OSError:
            pass


def _load():
    try:
        return json.loads(JOBS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


_SAVE_LOCK = threading.Lock()      # one writer at a time; see _save


def _save(jobs):
    JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
    # One unique temp per save: a batch's tool calls run in a thread pool, and a FIXED
    # temp name loses one of two saves - the first os.replace moves the shared tmp away
    # and the second dies with ENOENT on its own path (measured 2026-10-10 driving the
    # stage: a status+output batch, and the operator got `ERROR in tool 'process'` for a
    # job that had finished cleanly). The same defect _census_save fixed for itself;
    # mkstemp in the target directory keeps the replace atomic on one filesystem.
    #
    # And ONE writer at a time, because a unique temp is not enough on Windows: two
    # os.replace calls racing onto the same destination collide there - the loser gets
    # [WinError 5] Access is denied on its own replace (measured 2026-10-10: the install
    # surface's Windows job failed this suite's four-thread save member with exactly
    # that). Readers never take this lock; the replace stays atomic for them.
    with _SAVE_LOCK:
        fd, tmp = tempfile.mkstemp(prefix="process-jobs.", suffix=".tmp",
                                   dir=str(JOBS_FILE.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(jobs, indent=2))
            os.replace(tmp, JOBS_FILE)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


def _hidden():
    if os.name != "nt":
        return {"start_new_session": True}
    kw = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}
    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = subprocess.SW_HIDE
        kw["startupinfo"] = si
    except Exception:
        pass
    return kw


def _alive(pid):
    if pid is None:
        # A reserved row (an id allocated, its process not recorded yet): no pid, so
        # nothing to probe - never a TypeError out of `list`.
        return False
    if os.name == "nt":
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(h)
        return bool(ok) and code.value == STILL_ACTIVE
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def _record_end(jid, rc):
    """Persist a finished job's exit code. A whole-table save from a caller's stale
    snapshot is how one writer loses another's row, so re-read under the lock."""
    with _table_lock():
        jobs = _load()
        row = jobs.get(jid)
        if row is None:
            return
        row["rc"] = rc
        row["ended"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _save(jobs)


def _state(jobs, jid):
    """(alive, rc) for a job: exact for jobs this session started, liveness
    only for one from an earlier session (its exit code is gone with the
    process that would have collected it)."""
    job = jobs.get(jid)
    if not job:
        return None, None
    proc = _PROCS.get(jid)
    if proc is not None:
        rc = proc.poll()
        if rc is None:
            return True, None
        job["rc"] = rc
        job["ended"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _record_end(jid, rc)
        _PROCS.pop(jid, None)
        return False, rc
    alive = _alive(job.get("pid"))
    if not alive and job.get("rc") is None:
        # The collector is gone (a restart) - the wrapper's marker in the log is the
        # surviving record of what the job actually exited with.
        rc = _log_exit_code(job.get("log") or "")
        if rc is not None:
            job["rc"] = rc
            job["ended"] = time.strftime("%Y-%m-%d %H:%M:%S")
            _record_end(jid, rc)
            return False, rc
    return alive, job.get("rc")


ACTIONS = ("start", "status", "wait", "output", "kill", "send", "list")
# The shape a wait_for spec may take; named in every refusal so the caller has the fix.
WAIT_FOR_SHAPE = '{"log": "<regex>", "port": 1234, "timeout": 30}'


def _wait_for_refusal(wait_for):
    """Why this wait_for spec cannot be used, or None. Checked BEFORE the job starts:
    an unguarded int()/re.compile() reached a ValueError or re.error from inside the
    readiness loop, after the job had already been spawned."""
    if not isinstance(wait_for, dict):
        return "wait_for must be an object like %s" % WAIT_FOR_SHAPE
    unknown = [k for k in wait_for if k not in ("log", "port", "timeout")]
    if unknown:
        return ("wait_for takes only \"log\", \"port\" and \"timeout\" (got %s); "
                "the shape is %s" % (", ".join(repr(k) for k in unknown), WAIT_FOR_SHAPE))
    log = wait_for.get("log")
    if log is not None:
        if not isinstance(log, str):
            return ('wait_for "log" is a regex string, got %r; the shape is %s'
                    % (log, WAIT_FOR_SHAPE))
        if log.strip():
            try:
                re.compile(log)
            except re.error as e:
                return ('wait_for "log" is not a valid regex (%s); the shape is %s'
                        % (e, WAIT_FOR_SHAPE))
    port = wait_for.get("port")
    if port is not None:
        if isinstance(port, bool) or not isinstance(port, int) \
                or not 0 < port < 65536:
            return ('wait_for "port" is a TCP port number, got %r; the shape is %s'
                    % (port, WAIT_FOR_SHAPE))
    timeout = wait_for.get("timeout")
    if timeout is not None and (isinstance(timeout, bool)
                                or not isinstance(timeout, (int, float))):
        return ('wait_for "timeout" is seconds, got %r; the shape is %s'
                % (timeout, WAIT_FOR_SHAPE))
    return None


def run(args, ctx):
    action = str(args.get("action") or "").strip().lower()
    if action not in ACTIONS:
        # Before any job lookup: an unknown (or missing) action used to answer
        # "no job ''", which reads as an id problem and names nothing actionable.
        return ("ERROR: unknown action %r (expected one of: %s)"
                % (action, ", ".join(ACTIONS)))
    jobs = _load()
    jid = str(args.get("id") or "").strip()
    if action == "start":
        cmd = args.get("command")
        if isinstance(cmd, str):
            cmd = cmd.strip()
        # A dict, a number or a list with a non-string in it must be refused HERE: it
        # used to pass this check (a dict iterates its keys), then `spawn_detached`
        # json.dumps'd it or died, and `list` afterwards died on `job['command'][:70]`
        #.
        if not ((isinstance(cmd, str) and cmd)
                or (isinstance(cmd, list) and cmd
                    and all(isinstance(a, str) for a in cmd))):
            return "ERROR: start needs a command (a shell string or an argv list)"
        wait_for = args.get("wait_for")
        if wait_for:
            bad = _wait_for_refusal(wait_for)
            if bad:
                return f"ERROR: {bad}"
        # A string is a SHELL command, a list is argv verbatim.
        if isinstance(cmd, str) and cmd.startswith("["):
            # A model whose session never had this schema sends the ARGV LIST as a JSON
            # string, and a string runs through the shell: measured 2026-09-25 on a live install,
            # '["powershell.exe","-File","C:\Users\..."]' reached cmd.exe and died with
            # '"[powershell.exe"' is not recognized as an internal or external command'.
            # Repairing the shape costs nothing; the alternative is a thrown-away step.
            try:
                parsed = json.loads(cmd)
            except ValueError:
                parsed = None
            if (isinstance(parsed, list) and parsed
                    and all(isinstance(a, str) for a in parsed)):
                cmd = parsed
        # A string is a SHELL command, and it has to run in the box's real shell: the
        # harness hands us the same argv the shell tool uses (PowerShell on Windows, bash
        # elsewhere) plus the guard its own tier applies. Measured 2026-09-25 on a live install: with
        # cmd.exe underneath, a bash-style and a PowerShell-style loop both died inside cmd
        # and a third form "succeeded" (exit 0) having echoed the command as text.
        if isinstance(cmd, str):
            make_argv = (ctx or {}).get("shell_argv")
            refusal = None
            guard = (ctx or {}).get("shell_guard")
            if guard:
                refusal = guard(cmd)
            if refusal:
                return refusal
            argv = make_argv(cmd) if make_argv else cmd
            shell = make_argv is None        # older harness: the raw shell string
        else:
            guard = (ctx or {}).get("shell_guard")
            refusal = guard(" ".join(str(a) for a in cmd)) if guard else None
            if refusal:
                return refusal
            argv = cmd
            shell = False
        jid = next_jid()               # allocated AND saved under the table lock
        try:
            proc, log_path, spool = spawn_detached(jid, argv, shell)
        except OSError as e:
            _drop_reserved(jid)
            return f"ERROR: could not start: {e}"
        adopt(jid, proc, cmd, log_path, spool)
        jobs = _load()                 # adopt wrote to disk; this dict is a snapshot
        note = (f"started {jid} (pid {proc.pid})\nlog: {log_path}\n"
                f"Poll with process status/wait; read the log with output or "
                f"read_file. Answer a prompt with action=send.\n"
                f"note: a string command runs through the shell, so QUOTE any path "
                f"with a space in it; an argv list skips the shell\n"
                f"note: stdout to a file is block-buffered in the child, so a "
                f"log fills as the child flushes - python needs -u for "
                f"line-live output")
        if wait_for:
            deadline = time.time() + max(1, min(int(wait_for.get("timeout") or 30),
                                                300))
            ready, why = False, "not checked"
            while time.time() < deadline:
                ready, why = _ready(jobs[jid], wait_for)
                if ready:
                    break
                if not _state(jobs, jid)[0]:
                    break
                time.sleep(0.5)
            return ("%s\nreadiness: %s - %s"
                    % (note, "ready" if ready else "NOT ready (timedOut; the job is "
                             "still running)", why))
        return note
    if action == "list":
        if not jobs:
            return "no background jobs"
        out = []
        for k, job in sorted(jobs.items()):
            alive, rc = _state(jobs, k)
            if job.get("pid") is None and rc is None:
                state = "starting (id reserved; no pid yet)"
            else:
                state = "running" if alive else (f"exit {rc}" if rc is not None
                                                 else "finished (code not captured)")
            # A legacy row whose command is not a string (a dict, a number) used to kill
            # the whole listing with KeyError/TypeError on the slice (A-126).
            out.append(f"{k}: {state} - {str(job.get('command') or '')[:70]}")
        return "\n".join(out)
    if jid not in jobs:
        return f"ERROR: no job {jid!r} (see action=list)"
    alive, rc = _state(jobs, jid)
    job = jobs[jid]
    if action == "status":
        if alive:
            return f"{jid} running (pid {job['pid']}, started {job['started']})"
        return (f"{jid} finished, exit {rc}" if rc is not None
                else f"{jid} finished (code not captured; see its log)")
    if action == "wait":
        cap = max(1, min(int(args.get("timeout") or 60), 300))
        deadline = time.time() + cap
        while alive and time.time() < deadline:
            time.sleep(0.5)
            alive, rc = _state(jobs, jid)
        if alive:
            return f"{jid} still running after {cap}s"
        return (f"{jid} finished, exit {rc}" if rc is not None
                else f"{jid} finished (code not captured; see its log)")
    if action == "output":
        log_path = str(job.get("log") or "")
        if not log_path:
            return f"ERROR: {jid} has no log recorded"
        if not Path(log_path).is_file():
            return f"ERROR: no log at {log_path}"
        # read_job_log closes the handle and strips the wrapper's `__EXIT__` marker line.
        # This went through a bare open(...).read() - the handle was never closed (five
        # ResourceWarnings; on Windows the log stayed locked until the GC ran) and the
        # marker was echoed back as output.
        lines = read_job_log(log_path).splitlines()
        n = max(1, min(int(args.get("lines") or 40), 200))
        tail = lines[-n:]
        return (f"{jid} log tail ({len(tail)} of {len(lines)} lines, "
                f"{log_path}):\n" + "\n".join(tail))
    if action == "send":
        if not alive:
            return (f"{jid} is not running%s - nothing to send"
                    % (f" (exit {rc})" if rc is not None else ""))
        text = str(args.get("text") or "")
        if not text:
            return "ERROR: send needs `text` (a line for the job's stdin)"
        spool = Path(job.get("in") or (BASE / "logs" / f"proc-{jid}.in"))
        try:
            spool.parent.mkdir(parents=True, exist_ok=True)
            with open(spool, "ab") as fh:
                fh.write((text.rstrip("\n") + "\n").encode("utf-8"))
        except OSError as e:
            return f"ERROR: could not write to the job's stdin spool: {e}"
        return (f"sent {len(text)} char(s) to {jid}'s stdin (forwarded within ~0.2s; "
                f"read the answer with output)")
    if action == "kill":
        if not alive:
            return f"{jid} was already finished"
        pid = job.get("pid")
        if not pid:
            return (f"{jid} has no pid recorded (an id reserved by a start that did not "
                    f"finish); nothing to kill")
        pid = int(pid)
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=20, **_hidden())
        else:
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except OSError:
                os.kill(pid, signal.SIGKILL)
        time.sleep(0.5)
        jobs = _load()
        alive, rc = _state(jobs, jid)
        # Report exactly what was confirmed. `taskkill /F /T` and killpg kill the tree
        # they can see, and a process the job detached itself is not in it - "killed" was
        # a claim this tool could not check.
        if alive:
            return (f"{jid}: kill sent to the wrapper's tree (pid {pid}), but the "
                    f"wrapper is still alive - nothing is confirmed killed. A process "
                    f"the job detached itself is outside that tree; kill its pid "
                    f"directly if it persists")
        return (f"{jid}: the wrapper is gone after the kill sent to its tree "
                f"(pid {pid}); a process the job detached itself is not confirmed "
                f"killed")

if __name__ == "__main__":
    # Standalone smoke test: `python process.py action=list` prints run()'s result.
    # ctx is empty here; a tool that needs the run's ctx (shell, send_file, report)
    # is called through the harness.
    import json
    import sys
    args = dict()
    for tok in sys.argv[1:]:
        k, _, v = tok.partition("=")
        try:
            args[k] = json.loads(v)
        except ValueError:
            args[k] = v
    print(run(args, dict()))

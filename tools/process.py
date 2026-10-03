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


def next_jid():
    """The next free job id, from the current table."""
    jobs = _load()
    n = 1
    while f"b{n}" in jobs:
        n += 1
    return f"b{n}"


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


def _save(jobs):
    JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = JOBS_FILE.with_name("process-jobs.json.tmp")
    tmp.write_text(json.dumps(jobs, indent=2), encoding="utf-8")
    os.replace(tmp, JOBS_FILE)


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
        _save(jobs)
        _PROCS.pop(jid, None)
        return False, rc
    alive = _alive(job["pid"])
    if not alive and job.get("rc") is None:
        # The collector is gone (a restart) - the wrapper's marker in the log is the
        # surviving record of what the job actually exited with.
        rc = _log_exit_code(job.get("log") or "")
        if rc is not None:
            job["rc"] = rc
            job["ended"] = time.strftime("%Y-%m-%d %H:%M:%S")
            _save(jobs)
            return False, rc
    return alive, job.get("rc")


def run(args, ctx):
    action = str(args.get("action") or "").strip().lower()
    jobs = _load()
    jid = str(args.get("id") or "").strip()
    if action == "start":
        cmd = args.get("command")
        if isinstance(cmd, str):
            cmd = cmd.strip()
        if not cmd or (isinstance(cmd, str) is False
                       and not all(isinstance(a, str) for a in cmd)):
            return "ERROR: start needs a command (a shell string or an argv list)"
        n = 1
        while f"b{n}" in jobs:
            n += 1
        jid = f"b{n}"
        log_path = (BASE / "logs" / f"proc-{jid}.log")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        # A string is a SHELL command, a list is argv verbatim.
        if isinstance(cmd, str) and cmd.startswith("["):
            # A model whose session never had this schema sends the ARGV LIST as a JSON
            # string, and a string runs through the shell: measured 2026-09-25 on the fleet box,
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
        # elsewhere) plus the guard its own tier applies. Measured 2026-09-25 on the fleet box: with
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
        try:
            proc, log_path, spool = spawn_detached(jid, argv, shell)
        except OSError as e:
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
        wait_for = args.get("wait_for")
        if isinstance(wait_for, dict) and wait_for:
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
            state = "running" if alive else (f"exit {rc}" if rc is not None
                                            else "finished (code not captured)")
            out.append(f"{k}: {state} - {job['command'][:70]}")
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
        try:
            lines = job["log"] and open(job["log"], encoding="utf-8",
                                        errors="replace").read().splitlines()
        except OSError as e:
            return f"ERROR: {e}"
        n = max(1, min(int(args.get("lines") or 40), 200))
        tail = lines[-n:]
        return (f"{jid} log tail ({len(tail)} of {len(lines)} lines, "
                f"{job['log']}):\n" + "\n".join(tail))
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
        pid = int(job["pid"])
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
        return f"{jid} killed" + ("" if not alive else " (still alive?!)")
    return f"ERROR: unknown action {action!r}"

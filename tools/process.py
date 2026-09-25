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
import signal
import subprocess
import time
from pathlib import Path

NAME = "process"
DESCRIPTION = ("Run a long job in the background and come back for it: start "
               "returns an id, then status/wait/output/kill. For builds and "
               "servers that outlive a shell timeout. The table survives a "
               "restart; kill stops the job's whole process tree.")
SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string",
                   "enum": ["start", "status", "wait", "output", "kill", "list"]},
        "command": {"type": "string",
                    "description": "start: the command - a shell string, or an "
                                   "argv list for no shell"},
        "id": {"type": "string", "description": "the job id from start, e.g. b3"},
        "timeout": {"type": "integer",
                    "description": "wait: seconds to block (default 60, max 300)"},
        "lines": {"type": "integer", "description": "output: tail size (default 40)"},
    },
    "required": ["action"],
}
MUTATES = True

BASE = Path(__file__).resolve().parent.parent
JOBS_FILE = BASE / "logs" / "process-jobs.json"
_PROCS = {}          # id -> Popen, for jobs THIS session started
STILL_ACTIVE = 259


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
    return _alive(job["pid"]), job.get("rc")


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
            with open(log_path, "wb") as fout:
                proc = subprocess.Popen(argv, shell=shell, stdout=fout,
                                        stderr=fout,
                                        stdin=subprocess.DEVNULL,
                                        cwd=str(BASE), **_hidden())
        except OSError as e:
            return f"ERROR: could not start: {e}"
        jobs[jid] = {"pid": proc.pid, "command": cmd, "log": str(log_path),
                     "started": time.strftime("%Y-%m-%d %H:%M:%S")}
        _PROCS[jid] = proc
        _save(jobs)
        return (f"started {jid} (pid {proc.pid})\nlog: {log_path}\n"
                f"Poll with process status/wait; read the log with output or "
                f"read_file.\n"
                f"note: a string command runs through the shell, so QUOTE any path "
                f"with a space in it; an argv list skips the shell\n"
                f"note: stdout to a file is block-buffered in the child, so a "
                f"log fills as the child flushes - python needs -u for "
                f"line-live output")
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

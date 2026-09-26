"""The guard battery: every destructive spelling is gated, and nothing else is.

BUGREPORT §S1. Measured at HEAD before this change: `rm -rf /etc`, `rm -rf ~/Documents`,
`rm -r -f /`, `rm --recursive --force /`, `find / -delete`, `find / -exec rm -rf {} +` and
`: () { :|:& };:` were all `is_blocked=False, confirm=None` - the only POSIX patterns
required `r` and `f` in one flag word immediately before a bare `/`. The same run's
Windows battery (taskkill, diskpart, takeown, icacls, net user, schtasks /delete,
Set-ExecutionPolicy, Stop-Service, Stop-Process, reg delete, Clear-EventLog, wmic
shadowcopy delete, git reset --hard, git clean -xfd) ran with no gate either. The
mirror-image false positives were real too: `dd ... of=/dev/null`, `ls /sbin/mkfs*` and
`grep -rn mkfs` were refused.

Two lists, no exceptions: `must_gate` may be blocked or confirmed, `must_allow` may be
neither. A guard battery that only asserts the blocking half lets a new pattern quietly
refuse ordinary work, which is the failure this tier already paid for twice.

    python tests/test_guard_battery.py
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

STAGE = Path(tempfile.gettempdir()) / "tinycmdr-test-stage-guards"
STAGE.mkdir(parents=True, exist_ok=True)
shutil.copy2(SRC, STAGE / "tinycmdr.py")
shutil.copy2(Path(__file__).resolve().parent / "fixture-config.json",
             STAGE / "config.json")
spec = importlib.util.spec_from_file_location("tinycmdr_guards",
                                              STAGE / "tinycmdr.py")
fb = importlib.util.module_from_spec(spec)
sys.modules["tinycmdr_guards"] = fb
spec.loader.exec_module(fb)

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"ok   {name}")
    else:
        FAILURES.append(name)
        print(f"FAIL {name}: {detail}")


def gated(cmd):
    """'block' | 'confirm' | None, using the same functions the shell path uses."""
    if fb.is_blocked(cmd):
        return "block"
    risk = fb.destructive_risk(cmd)
    if risk:
        return risk[0]
    if fb._confirm_hit(cmd):
        return "confirm"
    return None


MUST_GATE = [
    # POSIX recursive deletes, every spelling of the flags, whole-tree and named targets
    "rm -rf /", "rm -rf /etc", "rm -r -f /", "rm --recursive --force /",
    "rm -fr /var", "rm -rvf /usr/local", "rm -rf ~", "rm -rf $HOME",
    "rm -rf %USERPROFILE%", "rmdir -r /usr", "sudo rm -rf /etc",
    "bash -c 'rm -rf /etc'", "sh -c 'rm -rf /home/user'",
    "rm -rf ~/Documents", "rm -rf /tmp/scratch", "rm -r ./build",
    "find / -delete", "find / -exec rm -rf {} +", "find /etc -delete",
    # fork bomb, with and without the spaces people type
    ": () { :|:& };:", ":(){ :|:& };:",
    # disks and filesystems
    "dd if=/dev/zero of=/dev/sda", "mkfs.ext4 /dev/sda1", "sudo mkfs /dev/sda1",
    "format C:", "> /dev/sda", "Format-Volume -DriveLetter D", "Clear-Disk -Number 1",
    "cipher /w:C", "vssadmin delete shadows /all", "Initialize-Disk -Number 2",
    # machine verbs
    "shutdown /r /t 0", "poweroff", "reboot", "Stop-Computer", "Restart-Computer",
    # Windows machine-verb class (BUGREPORT §S1 second battery)
    "taskkill /f /im explorer.exe", "diskpart /s wipe.txt",
    "takeown /f C:\\ /r /d y", "icacls C:\\ /grant Everyone:F",
    "net user evil P@ss /add", "New-LocalUser -Name evil",
    "schtasks /delete /tn Tinycmdr /f",
    "Set-ExecutionPolicy Bypass -Scope LocalMachine",
    "Stop-Service com.tinycmdr", "Stop-Process -Name llama-server -Force",
    "reg delete HKLM\\Software\\x /f", "Clear-EventLog -LogName System",
    "wmic shadowcopy delete", "git reset --hard HEAD~5", "git clean -xfd",
    "rd /s /q C:\\Windows", "rmdir /s /q C:\\Temp", "del /s /q C:\\x",
    "erase /s /q C:\\x", "Remove-Item -Recurse -Force C:\\Temp",
    "robocopy C:\\a C:\\b /MOVE",
    # PowerShell aliases and short parameter forms (BUGREPORT §S2)
    "ri -r -fo C:\\x", "rm -r -fo C:\\x", "ri -Recurse -Force C:\\Temp",
    "gci C:\\x | ri -Recurse",
    # An encoded command, in all three spellings, with a base64 argument
    "powershell -e SQBFAFgA", "pwsh -ec SQBFAFgA",
    "powershell -EncodedCommand SQBFAFgA",
]

MUST_ALLOW = [
    # the three false positives the report measured
    "dd if=/dev/zero of=/dev/null bs=1M count=100",
    "ls /sbin/mkfs*",
    "grep -rn mkfs /usr/share/doc",
    # ordinary file work
    "rm file.txt", "rm -f build.log", "unlink stale.sock",
    "mkdir -p /tmp/x", "ls -la /", "find /etc -name '*.conf'",
    "grep -rn atomic_write_text .", "echo hi", "cat /etc/hosts",
    "systemctl status nginx", "git status", "git diff", "git log --oneline -5",
    "Remove-Item C:\\Temp\\file.txt", "Get-ChildItem C:\\Temp",
    "ri C:\\Temp\\file.txt", "erase C:\\Temp\\file.txt",
    # the encoded-command false positive: the WORD in text is not a command
    "echo 'this note mentions -EncodedCommand in text'",
    "Select-String encodedcommand",
    "powershell -NoProfile -Command 'Get-Date'",
    "python tinycmdr.py --version",
]


def test_must_gate():
    for cmd in MUST_GATE:
        verdict = gated(cmd)
        check(f"gated: {cmd!r}", verdict in ("block", "confirm"), verdict)


def test_must_allow():
    for cmd in MUST_ALLOW:
        verdict = gated(cmd)
        check(f"allowed: {cmd!r}", verdict is None, verdict)


def test_broad_root_escalates():
    """A whole tree is the absolute tier; a named directory is a question."""
    check("a whole-tree delete is BLOCKED, not confirmed",
          gated("rm -rf /") == "block", gated("rm -rf /"))
    check("a named directory is CONFIRMED, not blocked",
          gated("rm -rf ~/Documents") == "confirm", gated("rm -rf ~/Documents"))


def test_file_door_and_shell_door_agree():
    """BUGREPORT §S3: a write to this bot's own notes.md is gated through write_file
    exactly as it is through the shell, and an ordinary file is not gated at all."""
    d = Path(tempfile.mkdtemp(prefix="tc-surface-"))
    try:
        notes = d / "notes.md"
        notes.write_text("- [2026-01-01 00:00] original\n", encoding="utf-8")
        asks = []

        def door(subject):
            asks.append(subject)
            return False            # nobody at the door: DECLINED

        out = fb.tool_write_file({"path": str(notes), "content": "WIPED"},
                                 {"confirm_cb": door})
        check("write_file on notes.md is gated",
              str(out).startswith("DECLINED"), str(out)[:140])
        check("the operator was asked once", len(asks) == 1, asks)
        check("the file was NOT replaced",
              "original" in notes.read_text(encoding="utf-8"))

        shell = fb._prompt_surface_write("printf x > %s" % notes)
        check("the shell door flags the same file", bool(shell), shell)

        scratch = d / "scratch.txt"
        out2 = fb.tool_write_file({"path": str(scratch), "content": "hello"},
                                  {"confirm_cb": door})
        check("an ordinary file is written without a question",
              str(out2).startswith("OK") and len(asks) == 1, str(out2)[:100])

        tasks = d / "tasks.json"
        tasks.write_text('{"items": []}\n', encoding="utf-8")
        out3 = fb.tool_edit_file({"path": str(tasks), "old_string": "[]",
                                  "new_string": "[1]"}, {"confirm_cb": door})
        check("edit_file on tasks.json is gated too",
              str(out3).startswith("DECLINED"), str(out3)[:140])
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_manifest_command_walks_the_shell_tier():
    """BUGREPORT §S6: a .tool.json whose command is `rm -rf /` must not load, and a
    confirm-tier command must be asked at call time - the manifest door is the shell door."""
    d = Path(tempfile.mkdtemp(prefix="tc-manifest-"))
    try:
        man = d / "wipe.tool.json"
        man.write_text(json.dumps({
            "name": "wipe", "description": "p",
            "schema": {"type": "object", "properties": {}},
            "command": "rm -rf /"}), encoding="utf-8")
        refused = None
        try:
            fb.load_tool_defs(man)
        except Exception as e:
            refused = e
        check("a manifest carrying `rm -rf /` is refused at LOAD", refused is not None,
              "it loaded")
        check("the load refusal names the reason",
              refused is not None and ("safety pattern" in str(refused)
                                       or "cannot be approved" in str(refused)),
              refused)

        man2 = d / "tidy.tool.json"
        man2.write_text(json.dumps({
            "name": "tidy", "description": "p",
            "schema": {"type": "object", "properties": {}},
            "command": "rm -rf ./build"}), encoding="utf-8")
        defs = fb.load_tool_defs(man2)
        out = defs[0][3]({}, {})
        check("a confirm-tier manifest command is DECLINED at call time with no door",
              str(out).startswith("DECLINED"), str(out)[:140])
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_short_guard_list_is_named_not_silent():
    """BUGREPORT §S18: a config.json that REPLACED a shipped tier must not silently
    weaken this box - the drift is named, and `doctor` exits non-zero on it."""
    saved = dict(fb.CONFIG["agent"])
    try:
        fb.CONFIG["agent"]["confirm_patterns"] = ["\\bshutdown\\b"]
        fb.CONFIG["agent"]["blocked_patterns"] = ["\\bmkfs\\b"]
        drift = fb.guard_list_drift()
        note = fb.guard_drift_note(drift)
        check("a replaced confirm list is reported as drift", bool(drift), drift)
        check("the note names the list and counts what is missing",
              "confirm_patterns is missing" in note and "shipped pattern" in note, note)
        check("the note names the version it was written against",
              "guard list v%d" % fb.GUARD_LIST_VERSION in note, note)
    finally:
        fb.CONFIG["agent"].clear()
        fb.CONFIG["agent"].update(saved)
    # with the shipped lists back, there is nothing to report
    check("the restored config has no drift", fb.guard_list_drift() == [],
          fb.guard_list_drift())


def test_guard_extras_append_and_never_substitute():
    """`<list>_extra` is the safe way to add a pattern: the shipped list stays."""
    agent = {"confirm_patterns": ["mine"], "confirm_patterns_extra": ["yours", "mine"]}
    fb._merge_guard_extras(agent)
    check("extras are appended, never substituted",
          agent["confirm_patterns"] == ["mine", "yours"], agent["confirm_patterns"])
    agent2 = {"confirm_patterns_extra": ["my-pattern"]}
    fb._merge_guard_extras(agent2)
    check("extras alone keep every shipped pattern",
          len(agent2["confirm_patterns"])
          == len(fb.SHIPPED_GUARD_LISTS["confirm_patterns"]) + 1,
          len(agent2["confirm_patterns"]))


def test_doctor_reports_the_short_guard_list_and_exits_nonzero():
    """End to end: a staged install whose config.json has a 4-entry confirm list."""
    import subprocess
    d = Path(tempfile.mkdtemp(prefix="tc-doctor-"))
    try:
        shutil.copy2(SRC, d / "tinycmdr.py")
        fixture = json.loads((Path(__file__).resolve().parent
                              / "fixture-config.json").read_text(encoding="utf-8-sig"))
        cfg = {k: v for k, v in fixture.items() if not k.startswith("_")}
        # exactly what the accept describes: a list that is missing FIVE shipped entries
        shipped = list(fb.SHIPPED_GUARD_LISTS["confirm_patterns"])
        dropped = [p for p in shipped if "diskpart" in p or "takeown" in p
                   or "icacls" in p or "taskkill" in p
                   or "new-localuser" in p]
        cfg.setdefault("agent", {})["confirm_patterns"] = [
            p for p in shipped if p not in dropped]
        # no endpoint: the test does not depend on a mock, and the guard line must appear
        # whatever the probe says
        cfg["llm"]["base_url"] = "http://127.0.0.1:9/v1"
        (d / "config.json").write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        proc = subprocess.run([sys.executable, str(d / "tinycmdr.py"), "doctor"],
                              cwd=str(d), capture_output=True, text=True, timeout=120)
        out = proc.stdout + proc.stderr
        check("doctor names the short guard list", "shipped pattern(s) missing" in out,
              out[-400:])
        check("doctor lists the 5 missing patterns, by name",
              "diskpart" in out and "takeown" in out and "icacls" in out
              and "taskkill" in out, out[-400:])
        check("doctor counts them", "missing 5 shipped pattern(s)" in out, out[-400:])
        check("doctor exits non-zero on it", proc.returncode != 0, proc.returncode)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def main():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed")
        for f in FAILURES:
            print("  FAIL:", f)
        sys.exit(1)
    print(f"all guard-battery checks passed ({len(MUST_GATE)} gated, "
          f"{len(MUST_ALLOW)} allowed)")


if __name__ == "__main__":
    main()

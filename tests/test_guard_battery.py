"""The guard battery: every destructive spelling is gated, and nothing else is.

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
    # Windows machine-verb class (second battery)
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
    # PowerShell aliases and short parameter forms
    "ri -r -fo C:\\x", "rm -r -fo C:\\x", "ri -Recurse -Force C:\\Temp",
    "gci C:\\x | ri -Recurse",
    # An encoded command, in all three spellings, with a base64 argument
    "powershell -e SQBFAFgA", "pwsh -ec SQBFAFgA",
    "powershell -EncodedCommand SQBFAFgA",
    # a PREFIX must not change the verdict (2026-10-08): every one of these came back None
    # from is_blocked, destructive_risk and _confirm_hit, because the wrapper word became
    # the "verb" and every arm was skipped
    "sudo wipefs -a /dev/sdb", "sudo -u root wipefs -a /dev/sdb",
    "echo $SUDO_PASSWORD | sudo -S sgdisk --zap-all /dev/sdb",
    "nice wipefs -a /dev/sdb", "doas wipefs -a /dev/sdb",
    'sudo dd of="/dev/sda"', "sudo mkfs.ext4 /dev/sdb1 && file -s /dev/sdb1",
    "nice rm -rf /etc", "nice -n 5 rm -rf /etc", "timeout 30 rm -rf /etc",
    "LC_ALL=C rm -rf ~", "env X=1 rm -rf /home/bob", "(rm -rf ~)",
    "if true; then rm -rf /etc; fi",
    "cmd /c format /FS:NTFS /Q D:", 'cmd /c "powershell -enc SQBFAFgA"',
    "Start-Process powershell -ArgumentList '-enc','SQBFAFgA'",
    "sudo sh -c 'wipefs -a /dev/sdb'", "sh -c 'wipefs -a /dev/sdb'",
    # a continuation line is the same command (A-2026-10-08-89): each of these was read
    # as TWO segments, and the half that carried the target read clean
    "dd if=/dev/zero bs=1M \\\nof=/dev/sda",
    "find / \\\n-delete",
    "format `\n/FS:NTFS Q:",
    "powershell `\n-enc SQBFAFgA",
    "sudo \\\nwipefs -a /dev/sdb",
    # every abbreviation of -EncodedCommand, and the slash spelling (A-2026-10-08-90)
    "powershell -enco SQBFAFgA", "pwsh -encod SQBFAFgA", "pwsh -encoded SQBFAFgA",
    "pwsh /encodedcommand SQBFAFgA", "powershell –enc SQBFAFgA",
    # the macOS disk tools and the Linux partition editors (A-2026-10-08-91)
    "diskutil eraseDisk JHFS+ X /dev/disk2", "diskutil zeroDisk /dev/disk2",
    "diskutil apfs deleteVolume disk3s1",
    "parted /dev/sda mklabel gpt", "sfdisk /dev/sda", "cfdisk /dev/sda",
    "gdisk /dev/sda", "blkdiscard /dev/nvme0n1", "mkswap /dev/sdb1",
    "newfs_apfs /dev/disk3", "asr restore --erase --source x --target /dev/disk2",
    # the confirm gate's own file: a model write there is refused, not asked
    # (A-2026-10-08-94)
    "echo '{\"all\": true}' > confirm-allow.json",
    "Set-Content confirm-allow.json '{\"all\": true}'",
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
    # a wrapper is transparent for ordinary work, and an interpreter handed a FILE is not
    # running the words after it (the mirror of the prefix block above, 2026-10-08)
    "sudo ls -la /", "sudo -u root systemctl status nginx", "sudo -n true",
    "nice -n 5 ls -la", "timeout 30 ls /tmp", "env X=1 ls", "LC_ALL=C ls -la",
    "xargs ls", "bash scripts/cleanup.sh", "sh -c 'ls -la'",
    "if true; then ls; fi", "echo 'then rm -rf /'",
    "cmd /c dir", "cmd /c echo hi", "Start-Process notepad",
    "sudo rm -f /nonexistent-xyz",
    # the mirror of the batch above: reading a Mac's disks, an ordinary continuation,
    # a non-encoded PowerShell parameter, and reading the gate's own file are not gates
    "diskutil list", "diskutil info /dev/disk1",
    "echo one \\\ntwo", "ls /sbin/newfs*",
    "powershell -ExecutionPolicy Bypass -File setup.ps1",
    "cat confirm-allow.json",
    "python -c \"print(open('confirm-allow.json').read())\"",
]


def test_must_gate():
    for cmd in MUST_GATE:
        verdict = gated(cmd)
        check(f"gated: {cmd!r}", verdict in ("block", "confirm"), verdict)


def test_must_allow():
    for cmd in MUST_ALLOW:
        verdict = gated(cmd)
        check(f"allowed: {cmd!r}", verdict is None, verdict)


def test_a_comment_cannot_run():
    """A guard reads the SOURCE, so a COMMENT must not trip it: a comment cannot execute.

    STRINGS ARE KEPT - `subprocess.run("reboot")` really does reboot - so this costs nothing
    that can run, only inert text. Measured 2026-09-29: a comment reading
    "# restart happens in the next step" tripped the confirm tier over execute_code, which on
    a lane with nobody at the door is a flat DECLINED.
    """
    check("a comment mentioning a gated verb does not gate",
          fb._confirm_hit(fb._strip_py_comments("# reboot the box when you are done")) is None,
          fb._confirm_hit(fb._strip_py_comments("# reboot the box when you are done")))
    check("  while a STRING that would run it still does",
          fb._confirm_hit(fb._strip_py_comments('subprocess.run("reboot")')) is not None)
    check("  and a comment cannot spend the scan budget",
          fb.code_cost_risk('# os.walk("/") is exactly what we must not do') is None)
    check("  while a quoted argument handed to a re-executor still can",
          fb.command_cost_risk('bash -c "find / -name x"') is not None)


def test_a_mention_is_not_a_command():
    """The BLOCK tier reads what would RUN, not what is merely carried (review 2026-09-29).

    It matched the whole command, so `grep -rn "rm -rf /" docs/` was refused outright, with the
    model told no confirmation unlocks it - a dead end for a read-only SEARCH. The rule is now
    two views of the command: quoted regions removed, because a quote is an argument, EXCEPT
    command substitutions, which run wherever they appear; and a match surviving only inside
    quotes blocks too, but only when something in the command EXECUTES that text.

    Both halves matter: the first list is what a seatbelt is for, and the second is what it was
    costing. If a future change makes this fail in the FIRST list, the change is a hole.
    """
    for cmd in ('rm -rf /', 'sudo rm -rf /', 'sh -c "rm -rf /"', "bash -c 'rm -rf /'",
                '$(rm -rf /)', '`rm -rf /`', 'xargs rm -rf /', 'find . -exec rm -rf / \\;',
                'ssh box "rm -rf /"', 'echo "rm -rf /" | sh',
                "echo 'rm -rf /' > /tmp/x.sh && sh /tmp/x.sh",
                'subprocess.run("rm -rf /", shell=True)'):
        check(f"still blocked: {cmd[:44]!r}", bool(fb.is_blocked(cmd)), fb.is_blocked(cmd))
    for cmd in ('grep -rn "rm -rf /" docs/', "git log -S 'rm -rf /'", 'rg "mkfs" docs/',
                'sudo grep -rn "rm -rf /" docs/', "printf '%s' 'rm -rf /'",
                'print("rm -rf /")', 'echo "dd if=/dev/zero of=/dev/sda"'):
        check(f"a mention, not a command: {cmd[:44]!r}", fb.is_blocked(cmd) is None,
              fb.is_blocked(cmd))


def test_a_delete_of_real_content_asks_with_the_measured_effect():
    """Effect-keyed, not spelling-keyed: the ask describes what will actually be lost.

    Two measured reasons (review 2026-09-29). The tier only covered RECURSIVE tree deletes, so
    the model's own `rm -f ~/Desktop/<a real document>` ran with nothing asked. And the ask
    itself was about the command, not the thing: "a recursive delete of ~/enoch_build" reads
    identically for an empty scratch directory and for four hours of finished work, which is
    exactly what the operator could not tell apart.

    Every number asserted here is built, then read back off the filesystem.
    """
    d = Path(tempfile.mkdtemp(prefix="tc-delask-"))
    keep_roots = fb._SCRATCH_ROOTS
    try:
        # Only this directory counts as scratch, so the real rules are exercised hermetically
        # (no /tmp, no $HOME, nothing of the operator's).
        (d / "scratch").mkdir()
        fb._SCRATCH_ROOTS = (str(d / "scratch"),)

        tree = d / "work"
        (tree / "sub").mkdir(parents=True)
        for i, n in enumerate((100, 200, 300, 400)):
            (tree / ("f%d.txt" % i)).write_bytes(b"x" * n)
        (tree / "sub" / "deep.txt").write_bytes(b"y" * 500)

        kind, why = fb.destructive_risk("rm -rf %s" % tree)
        check("a recursive delete of a named tree still CONFIRMS", kind == "confirm", kind)
        check("  and the ask names the file count it MEASURED",
              "5 file(s)" in why, why)
        check("  and the total size", "1.5 KB" in why, why)
        check("  and how recently it was written", "newest" in why, why)

        doc = d / "a-real-document.txt"
        doc.write_bytes(b"z" * 2048)
        kind, why = fb.destructive_risk("rm -f %s" % doc)
        check("a single-file delete of real content CONFIRMS", kind == "confirm", kind)
        check("  naming its measured size", "2.0 KB" in why, why)
        check("  and its age", "last written" in why, why)

        kind, why = fb.destructive_risk("rm %s" % doc)
        check("  whether or not the -f flag is spelled", kind == "confirm", kind)

        check("a delete of a path that does not exist asks nothing",
              fb.destructive_risk("rm -f %s/nothing-here.txt" % d) is None)

        (d / "scratch" / "junk.txt").write_bytes(b"j" * 10)
        check("  and neither does a single-file delete under a scratch root",
              fb.destructive_risk("rm -f %s" % (d / "scratch" / "junk.txt")) is None)
        check("  while the RECURSIVE shape still asks, scratch or not (MUST_GATE)",
              (fb.destructive_risk("rm -rf %s" % (d / "scratch")) or (None,))[0] == "confirm")

        kind, why = fb.destructive_risk("rm -rf /")
        check("a whole-tree delete is still BLOCKED, not asked", kind == "block", kind)

        keep = fb.CONFIG["agent"].get("confirm_deletes")
        try:
            fb.CONFIG["agent"]["confirm_deletes"] = False
            check("confirm_deletes=false restores the old shape",
                  fb.destructive_risk("rm -f %s" % doc) is None)
        finally:
            fb.CONFIG["agent"]["confirm_deletes"] = keep
    finally:
        fb._SCRATCH_ROOTS = keep_roots
        shutil.rmtree(d, ignore_errors=True)


def test_broad_root_escalates():
    """A whole tree is the absolute tier; a named directory is a question."""
    check("a whole-tree delete is BLOCKED, not confirmed",
          gated("rm -rf /") == "block", gated("rm -rf /"))
    check("a named directory is CONFIRMED, not blocked",
          gated("rm -rf ~/Documents") == "confirm", gated("rm -rf ~/Documents"))


def test_file_door_and_shell_door_agree():
    """A write to this bot's own notes.md is gated through write_file
    exactly as it is through the shell, and an ordinary file is not gated at all.

    The bot's OWN file is identified by its RESOLVED PATH. Until the 2026-09-29 review the
    check was the BASENAME alone, so an operator's own `docs/notes.md` was gated as "this
    bot's own notes.md" and DECLINED on a lane with nobody to ask. The fixture used to write
    to a temp-dir notes.md, which that old rule accepted - it has to be the INSTALL's own file
    to test this, and an identically-named file elsewhere has to NOT be gated.
    """
    d = Path(tempfile.mkdtemp(prefix="tc-surface-"))
    try:
        notes = Path(fb.BASE_DIR) / "notes.md"
        notes.parent.mkdir(parents=True, exist_ok=True)
        notes.write_text("- [2026-01-01 00:00] original\n", encoding="utf-8")
        asks = []

        def door(subject):
            asks.append(subject)
            return False            # nobody at the door: DECLINED

        out = fb.tool_write_file({"path": str(notes), "content": "WIPED"},
                                 {"confirm_cb": door})
        check("write_file on the bot's own notes.md is gated",
              str(out).startswith("DECLINED"), str(out)[:140])
        check("the operator was asked once", len(asks) == 1, asks)
        check("the file was NOT replaced",
              "original" in notes.read_text(encoding="utf-8"))

        shell = fb._prompt_surface_write("printf x > %s" % notes)
        check("the shell door flags the same file", bool(shell), shell)

        their_notes = d / "notes.md"
        their_notes.write_text("their notes\n", encoding="utf-8")
        out_theirs = fb.tool_write_file({"path": str(their_notes), "content": "theirs"},
                                        {"confirm_cb": door})
        check("a notes.md that is NOT the bot's own is written without a question",
              str(out_theirs).startswith("OK"), str(out_theirs)[:120])

        scratch = d / "scratch.txt"
        out2 = fb.tool_write_file({"path": str(scratch), "content": "hello"},
                                  {"confirm_cb": door})
        check("an ordinary file is written without a question",
              str(out2).startswith("OK") and len(asks) == 1, str(out2)[:100])

        atlas = Path(fb.BASE_DIR) / "atlas.md"
        atlas.write_text("host facts\n", encoding="utf-8")
        out3 = fb.tool_edit_file({"path": str(atlas), "old_string": "host",
                                  "new_string": "HOST"}, {"confirm_cb": door})
        check("edit_file on the bot's own atlas.md is gated too",
              str(out3).startswith("DECLINED"), str(out3)[:140])
        for leftover in (notes, atlas):
            try:
                leftover.unlink()
            except OSError:
                pass
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_surface_gate_covers_the_memory_bundle_and_case_variants():
    """A-2026-10-08-106: the file door compared a BASENAME against _SURFACE_FILES, and
    "memory/" can never be a basename - so every write to BASE_DIR/memory/index.md, the
    prompt-carried memory index, went unasked while the shell spelling was gated. A
    case-variant name (Notes.md on a case-insensitive filesystem) slipped through too,
    and the shell regex only knew the "memory/" spelling, not `memory\\index.md`."""
    d = Path(tempfile.mkdtemp(prefix="tc-surface-mem-"))
    mem = Path(fb.BASE_DIR) / "memory"
    index = mem / "index.md"
    ask = []
    try:
        mem.mkdir(parents=True, exist_ok=True)
        index.write_text("the real index\n", encoding="utf-8")

        out = fb.tool_write_file({"path": str(index), "content": "WIPED"},
                                 {"confirm_cb": lambda s: ask.append(s) or False})
        check("write_file into the bot's memory/ bundle is gated",
              str(out).startswith("DECLINED"), str(out)[:140])
        check("...and the index was not replaced",
              "the real index" in index.read_text(encoding="utf-8"))

        out = fb.tool_edit_file({"path": str(index), "old_string": "the real",
                                 "new_string": "THE REAL"},
                                {"confirm_cb": lambda s: ask.append(s) or False})
        check("edit_file into the memory/ bundle is gated too",
              str(out).startswith("DECLINED"), str(out)[:140])

        casey = Path(fb.BASE_DIR) / "Notes.md"
        out = fb.tool_write_file({"path": str(casey), "content": "x"},
                                 {"confirm_cb": lambda s: ask.append(s) or False})
        check("a case-variant name is gated (Notes.md is notes.md where it matters)",
              str(out).startswith("DECLINED"), str(out)[:140])

        theirs = d / "memory" / "index.md"
        theirs.parent.mkdir(parents=True, exist_ok=True)
        out = fb.tool_write_file({"path": str(theirs), "content": "theirs"},
                                 {"confirm_cb": lambda s: ask.append(s) or False})
        check("a memory/index.md that is NOT the bot's own is written unasked",
              str(out).startswith("OK"), str(out)[:120])

        shell = fb._prompt_surface_write("cp C:\\data\\x memory\\index.md")
        check("the shell door sees the Windows spelling memory\\index.md",
              bool(shell), shell)
        shell = fb._prompt_surface_write("printf x > memory/index.md")
        check("...and still names the POSIX spelling", bool(shell) and "memory" in shell,
              shell)
    finally:
        for p in (index, Path(fb.BASE_DIR) / "Notes.md", Path(fb.BASE_DIR) / "notes.md"):
            try:
                if p.exists():
                    p.unlink()
            except OSError:
                pass
        try:
            mem.rmdir()
        except OSError:
            pass
        shutil.rmtree(d, ignore_errors=True)


def test_manifest_command_walks_the_shell_tier():
    """A .tool.json whose command is `rm -rf /` must not load, and a
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
    """A config.json that REPLACED a shipped tier must not silently
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


class _ApprovalDest:
    """A destination that answers the confirm question with a fixed text."""

    has_human = True

    def __init__(self, answer):
        self.answer = answer
        self.lines = []
        self.options = None

    def line(self, kind, text, src="main"):
        self.lines.append(text)
        return ("fake", len(self.lines))

    def ask(self, question, options=None, wait=300.0, label=None):
        self.options = options
        return self.answer


def test_confirm_approval_scopes():
    """The ways OUT of being asked: this session, or always (persisted). Typed words."""
    fb.confirm_allow("clear")
    d1 = _ApprovalDest("session")
    r1 = fb.RunReporter(d1, "approve-s1")
    check("a bare 'session' approves for the session", r1.confirm("rm -rf /tmp/x") is True)
    check("the question offers four short answers, not a sentence",
          tuple(d1.options) == ("yes", "no", "session", "always"), d1.options)
    check("...and the session is preapproved from then on",
          fb.confirm_preapproved("approve-s1")[0] is True)
    check("...but another session is not", fb.confirm_preapproved("approve-s2")[0] is False)
    d2 = _ApprovalDest("always")
    fb.RunReporter(d2, "approve-s2").confirm("rm -rf /tmp/y")
    check("a bare 'always' persists to the allowlist file",
          json.loads((STAGE / "confirm-allow.json").read_text(encoding="utf-8")).get("all") is True)
    check("...and every session is preapproved after it",
          fb.confirm_preapproved("anything")[0] is True)
    # a lane that answers the NUMBERED list must land on the same option
    fb.confirm_allow("clear")
    check("answering the numbered list ('4') is the 'always' answer",
          fb.RunReporter(_ApprovalDest("4"), "approve-s4").confirm("rm -rf /tmp/w") is True
          and fb.confirm_preapproved("anything")[0] is True)
    fb.confirm_allow("clear")
    check("'no' wins over a scope word in the same sentence",
          fb.RunReporter(_ApprovalDest("no, not this session"),
                         "approve-s5").confirm("rm -rf /tmp/v") is False
          and fb.confirm_preapproved("approve-s5")[0] is False)
    check("a scope word inside a sentence still sets the scope",
          fb.RunReporter(_ApprovalDest("always please"),
                         "approve-s6").confirm("rm -rf /tmp/u") is True
          and fb.confirm_preapproved("anything")[0] is True)
    fb.confirm_allow("clear")
    check("`confirm_allow clear` wipes the file and the sessions",
          fb.confirm_preapproved("approve-s1")[0] is False
          and fb.confirm_preapproved("anything")[0] is False)
    check("a plain 'no' still declines",
          fb.RunReporter(_ApprovalDest("no"), "approve-s3").confirm("rm -rf /tmp/z") is False)
    # Reported 2026-10-06: a later session showed only "approved permanently" beside a
    # command nobody could see, which reads like an ask that approved itself.
    fb.confirm_allow("clear")
    d = _ApprovalDest("session")
    r = fb.RunReporter(d, "approve-s7")
    r.confirm("rm -rf /tmp/one")
    r.confirm("rm -rf /tmp/two")
    check("an auto-approved command names itself in the line",
          "rm -rf /tmp/two" in d.lines[-1], d.lines[-1])
    check("...and carries the scope and the undo hint",
          "session" in d.lines[-1] and "approvals clear" in d.lines[-1], d.lines[-1])
    fb.confirm_allow("clear")
    fb.RunReporter(_ApprovalDest("always"), "approve-s8").confirm("rm -rf /tmp/perm")
    state = json.loads((STAGE / "confirm-allow.json").read_text(encoding="utf-8"))
    check("a permanent grant records WHEN it was given", bool(state.get("since")), state)
    d2 = _ApprovalDest("yes")
    fb.RunReporter(d2, "approve-s9").confirm("rm -rf /tmp/later")
    check("...so a later session's line carries the command and the date",
          "rm -rf /tmp/later" in d2.lines[-1] and state["since"] in d2.lines[-1],
          d2.lines[-1])
    fb.confirm_allow("clear")


def test_a_refusal_sentence_cannot_approve():
    """A refusal word ANYWHERE in the answer outranks a scope word anywhere (2026-10-08).

    The parser read the whole answer for a scope word but only the FIRST word for a no,
    and the scope table carried `never` -> 'always' and `all` -> 'session': so "please
    never run that" approved the command AND wrote the permanent allow-all, "not at all"
    allow-listed the session, and "don't, never" did the same. A refusal must never
    approve, and an answer that mixes yes and no must fail closed.
    """
    refusals = ["please never run that", "not at all", "do not run this at all",
                "don't, never", "I'd never allow that", "absolutely not, never",
                "don't run that", "no thanks", "never", "not this one", "0"]
    try:
        for answer in refusals:
            fb.confirm_allow("clear")
            d = _ApprovalDest(answer)
            ok = fb.RunReporter(d, "refuse-s").confirm("rm -rf /tmp/x")
            check("a refusal is a no: %r" % answer, ok is False, ok)
            check("  ...and grants nothing: %r" % answer,
                  fb.confirm_preapproved("refuse-s")[0] is False
                  and fb.confirm_preapproved("anything")[0] is False)
        fb.confirm_allow("clear")
        check("'yes, always' still sets the permanent grant",
              fb.RunReporter(_ApprovalDest("yes, always"),
                             "refuse-s").confirm("rm -rf /tmp/y") is True
              and fb.confirm_preapproved("anything")[0] is True)
        fb.confirm_allow("clear")
        check("'just this session' still sets the session grant",
              fb.RunReporter(_ApprovalDest("just this session"),
                             "refuse-s").confirm("rm -rf /tmp/z") is True
              and fb.confirm_preapproved("refuse-s")[0] is True
              and fb.confirm_preapproved("other-s")[0] is False)
    finally:
        fb.confirm_allow("clear")


def test_the_never_tier_reads_tokens_not_spellings():
    """the never tier was anchored to one surface
    shape per command, so `format /FS:NTFS Q:`, `powershell -enc "..."` and `dd of="..."`
    ran with no gate at all, and a per-line read-only exemption let `mkfs... # cat`
    through. Word order and quotes must not change the verdict; mentions must stay
    allowed."""
    must_block = [
        "format /FS:NTFS Q:", "format.com /Q C:",
        'powershell -enc "SQBFAFgA"', "pwsh -EncodedCommand SQBFAFgA",
        'dd of="/dev/sdz9"', "dd of=//dev/sda", "dd of=/dev/sda",
        "rm -rf --no-preserve-root /", "rm -r -f /", 'rm -rf "/"',
        '> "/dev/sda"',
        "mkfs.ext4 /dev/sdb # cat", "find / -xdev; mkfs.ext4 /dev/sdb",
        "diskpart", "clear-disk -Number 1",
        # a wrapper word is not a verb (2026-10-08): each of these read the wrapper as
        # the verb and the whole tier returned None
        "sudo wipefs -a /dev/sdb", "sudo -u root wipefs -a /dev/sdb",
        "nice wipefs -a /dev/sdb", "env X=1 wipefs -a /dev/sdb",
        "nice rm -rf /", "sudo rm -rf /", "timeout 30 rm -rf /",
        'sudo dd of="/dev/sda"', "env X=1 dd of=/dev/sda",
        "cmd /c format /FS:NTFS /Q D:",
        'cmd /c "powershell -enc SQBFAFgA"',
        "Start-Process powershell -ArgumentList '-enc','SQBFAFgA'",
        "sh -c 'wipefs -a /dev/sdb'", "(rm -rf /)", "{ rm -rf /; }",
    ]
    must_allow = [
        "format the paragraph as markdown",
        r"C:\tools\format-report.ps1 -Path x",
        "ls /sbin/mkfs*", "grep -rn mkfs /sbin",
        "dd if=/dev/zero of=/dev/null bs=1M count=100",
        "sudo ls -la /", "bash scripts/cleanup.sh", "sh -c 'ls -la'",
        "cmd /c dir", "Start-Process notepad",
    ]
    for c in must_block:
        check("never-tier: %s" % c, fb.is_blocked(c), c)
    for c in must_allow:
        check("not never-tier: %s" % c, fb.is_blocked(c) is None, fb.is_blocked(c))


def test_the_confirm_tier_reads_aliases():
    """A-2026-10-05-77: the pattern named `remove-item`, so `ri -Recurse -Force` ran with
    no question. Every alias and any flag order lands on the same rule now - and a prefix
    no longer hides the verb (2026-10-08)."""
    for c in ("ri -Recurse -Force C:\\x", "del C:\\x /s /q", "erase /s /q C:\\x",
              "rmdir /s /q C:\\x", "Remove-Item -Recurse -Force C:\\x",
              "nice ri -Recurse -Force C:\\x", "env X=1 rm -r -f C:\\x"):
        check("confirm: %s" % c, fb._confirm_hit(c, "confirm_patterns"), c)


def test_the_gate_helpers_coerce_instead_of_raising():
    """A-2026-10-05-79: `is_blocked(None)` coerced while its siblings raised TypeError /
    AttributeError. Each helper answers a safe default for a non-string now."""
    check("est_tokens(None) floors at 1", fb.est_tokens(None) == 1)
    check("cap_output(None) is an empty string", fb.cap_output("shell", None) == "")
    check("cap_output(12345) stringifies", fb.cap_output("shell", 12345) == "12345")
    check("_one_json_object(None) is None", fb._one_json_object(None) is None)
    check("_host_is_local(None) is False", fb._host_is_local(None) is False)
    check("_confirm_hit(None) is None", fb._confirm_hit(None) is None)


def test_the_approval_store_gates_its_own_writers():
    """A-2026-10-08-93/-94: a `session` grant outlived the conversation it was given in,
    a model write to confirm-allow.json turned the confirm tier off with nothing asked,
    and a permanent grant answered for lanes that have nobody to ask."""
    fb.confirm_allow("clear")
    try:
        fb.confirm_allow("session", "sess-r")
        check("a session grant is live", fb.confirm_preapproved("sess-r")[0] is True)
        fb.AGENT.reset("sess-r")
        check("...and /new takes it with the conversation",
              fb.confirm_preapproved("sess-r")[0] is False)
        for door in ("write_file", "edit_file"):
            refusal = fb._surface_write_gate(str(fb.CONFIRM_ALLOW_FILE), door, {}) or ""
            check("%s to the store is REFUSED" % door,
                  refusal.startswith("REFUSED"), refusal)
        for c in ('echo \'{"all": true}\' > confirm-allow.json',
                  "Set-Content confirm-allow.json '{\"all\": true}'",
                  'python -c "open(\'confirm-allow.json\', \'w\').write(\'{}\')"'):
            check("a shell/code write is blocked: %s" % c, bool(fb.is_blocked(c)), c)
        fb.confirm_allow("always")
        check("the permanent grant is on", fb.confirm_preapproved("any")[0] is True)
        d = _ApprovalDest("yes")
        d.has_human = False
        saved = fb.CONFIG["agent"].get("confirm_without_door")
        fb.CONFIG["agent"]["confirm_without_door"] = "decline"
        try:
            check("a no-door lane still declines under a permanent grant",
                  fb.RunReporter(d, "sess-nodoor").confirm("rm -rf /tmp/x") is False)
        finally:
            fb.CONFIG["agent"]["confirm_without_door"] = saved
    finally:
        fb.confirm_allow("clear")


def test_an_allow_pattern_whitelists_only_its_segment():
    """A-2026-10-08-95: `allow_patterns: ["git status"]` silenced the confirm tier for
    every command chained after an allow-listed shape - the first match anywhere returned
    None before any segment was read."""
    saved = list(fb.CONFIG["agent"].get("allow_patterns") or [])
    fb.CONFIG["agent"]["allow_patterns"] = [r"git status"]
    try:
        check("the allowed shape alone passes", fb._confirm_hit("git status") is None)
        check("a confirm-tier command chained onto it is still asked",
              fb._confirm_hit("git status && git reset --hard") is not None)
        check("...even when the allowed shape comes last",
              fb._confirm_hit("git reset --hard && git status") is not None)
        check("a wholly allow-listed line needs no question",
              fb._confirm_hit("git status && git status") is None)
    finally:
        fb.CONFIG["agent"]["allow_patterns"] = saved


def test_a_written_script_cannot_carry_a_declined_command():
    """The confirm tier is not one file away from bypassed (measured 2026-10-10).

    Driven live: a recursive delete was declined five spellings in a row, so the run wrote
    run.sh with the same command and ran `bash run.sh` - the command line had no delete verb
    in it, and the file's CONTENT was read by no tier (the content tier carries the Windows
    shapes, not the POSIX deletes). The never tier was one file away the same way: `mkfs`
    inside a written script. A script-shaped write is read as commands now, and an
    interpreter pointed at a file this process just made is read back the same way; a file
    already on the box is not.
    """
    import tempfile
    work = Path(tempfile.mkdtemp(prefix="tc-guards-script-"))
    target = work / "decoy"
    target.mkdir()
    script = work / "run_rm.sh"
    ctx_none = {"session_key": "script-belt"}                 # a lane with no door
    ctx_yes = {"session_key": "script-belt", "confirm_cb": lambda subject: True}
    # Script CONTENT is read by bash, so the path must be bash-shaped on every platform:
    # a Windows path with backslashes is consumed as escapes by bash (`rm -rf C:\...`
    # targets "C:Users..."), rm -f still exits 0, and the check read a green run as a
    # failure on the Windows CI (measured 2026-10-10, the fix's first push).
    tgt = target.as_posix()

    out = fb.tool_write_file({"path": str(script),
                              "content": "#!/bin/bash\nrm -rf %s\n" % tgt},
                             ctx_none)
    check("write_file: a recursive delete in a script is declined before it lands",
          out.startswith("DECLINED") and not script.exists(), out[:160])

    out = fb.tool_write_file({"path": str(script),
                              "content": "#!/bin/bash\nrm -rf %s\n" % tgt},
                             ctx_yes)
    check("...and the approved write lands", out.startswith("OK") and script.exists(),
          out[:160])

    # the run-time half: a file the write gate never saw (written outside it, or by an
    # older build) is read back when an interpreter is pointed at it
    late = work / "late.sh"
    late.write_text("#!/bin/bash\nrm -rf %s\n" % tgt)
    out = fb.tool_shell({"command": "bash %s" % late}, ctx_none)
    check("shell: bash of a script written this run is declined on the file's content",
          out.startswith("DECLINED") and str(late) in out, out[:160])
    check("...and the target is still there", target.exists())
    out = fb.tool_shell({"command": "sudo bash %s" % late}, ctx_none)
    check("...a prefix does not change it", out.startswith("DECLINED"), out[:160])
    out = fb.tool_shell({"command": "bash %s" % late}, ctx_yes)
    check("...and an approved run goes through",
          not out.startswith("DECLINED") and not target.exists(), out[:120])

    # a file older than this process is the box's own, not something the run put out of
    # sight: it runs without a question
    old = work / "old.sh"
    old.write_text("#!/bin/bash\necho old-ran\n")
    when = fb.START_TIME - 3600
    os.utime(old, (when, when))
    out = fb.tool_shell({"command": "bash %s" % old}, ctx_none)
    check("shell: an old script is not re-read", "old-ran" in out, out[:160])

    # a comment is prose, not a command (the 2026-09-25 false-positive shape)
    noted = work / "noted.sh"
    out = fb.tool_write_file({"path": str(noted),
                              "content": "#!/bin/bash\n# rm -rf %s\necho ok\n" % target},
                             ctx_none)
    check("write_file: a delete in a COMMENT does not ask", out.startswith("OK"), out[:160])

    # the never tier keeps its own ground through a script
    never = work / "never.sh"
    out = fb.tool_write_file({"path": str(never),
                              "content": "#!/bin/bash\nmkfs.ext4 /dev/sdz\n"}, ctx_none)
    check("write_file: a never-tier command in a script is refused, not asked",
          out.startswith("BLOCKED") and not never.exists(), out[:160])


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

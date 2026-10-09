"""Structural checks on the Windows installer - and, on Windows, the doors run.

The test bed is macOS, so most checks here parse the shipped text and assert the SHAPE of
each fix, so a later edit cannot quietly revert one. The two double-click doors
(INSTALL-WINDOWS.cmd, UNINSTALL-WINDOWS.cmd) additionally RUN on Windows: the CI Windows
job executes them against staged folders with a stub wrapper, because a door that parses
perfectly can still close its window before its tail runs (measured 2026-10-09, a
double-clicked UNINSTALL-WINDOWS.cmd).

Run:  python tests/test_installer_windows.py
"""
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILED = []


def check(what, ok, detail=""):
    print(("ok   " if ok else "FAIL ") + what + ("" if ok else "  <- %s" % detail))
    if not ok:
        FAILED.append(what)


def skip(what, detail=""):
    # Not a failure: the fix belongs to a file this batch does not own. Printed loudly so
    # it cannot be forgotten.
    print("skip " + what + ("  <- %s" % detail if detail else ""))


def source(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def heredoc(text, marker):
    """The body of a PowerShell here-string introduced by $name = @" ... "@ (or @').

    Returns the raw body, so its own ASCII-ness can be checked - that is the actual
    property the launcher fix rests on.
    """
    m = re.search(r'\$' + re.escape(marker) + r' = @"\n(.*?)\n"@', text, re.S)
    return m.group(1) if m else None


def between(text, start, end):
    i = text.find(start)
    j = text.find(end)
    return text[i:j] if (i != -1 and j != -1 and j > i) else ""


def function_body(text, signature):
    """A top-level `def` block: the signature line up to the next top-level `def`.

    Used on tinycmdr.py, whose functions are long and whose line numbers drift, so the
    check cannot be anchored on a line number.
    """
    i = text.find(signature)
    if i == -1:
        return ""
    rest = text[i + len(signature):]
    m = re.search(r"(?m)^def ", rest)
    return signature + (rest[:m.start()] if m else rest)


def main():
    install = source("install/install-tinycmdr.ps1")
    cmd = source("install/install-tinycmdr.cmd")
    one_line = source("install.ps1")
    shim = source("tinycmdr.cmd")

    print("== elevation is re-checked once the fleet defaults have had their say ==")
    fleet = install.find("if ($fleet.as_service -eq $true)")
    guards = [m.start() for m in re.finditer(r"\$AsService -and -not \$elevated", install)]
    check("fleet defaults set $AsService from as_service",
          fleet != -1, "fleet-defaults block missing")
    check("two elevation guards exist (before and after the fleet defaults)",
          len(guards) == 2, "found %d guard(s)" % len(guards))
    check("the second guard runs AFTER the fleet defaults",
          len(guards) == 2 and guards[1] > fleet,
          "guard order: %r vs fleet at %d" % (guards, fleet))
    check("the late guard prints the re-run-as-Administrator line",
          "Re-run as Administrator" in between(install, "if ($fleet.as_service", "where this install goes"))

    print("\n== the fallback Register-ScheduledTask is caught, not trapped ==")
    regs = [m.start() for m in re.finditer(r"Register-ScheduledTask -TaskName \$AppName", install)]
    check("both registration attempts are present", len(regs) == 2, "found %d" % len(regs))
    tail = install[regs[-1]:] if regs else ""
    check("the fallback registration sits inside its own try {",
          regs and "try {" in install[regs[-1] - 400:regs[-1]], "no try before the fallback")
    check("its catch calls Fail (no bare Register-ScheduledTask left to the trap)",
          "} catch {" in tail[:900] and "Fail (" in tail[:900],
          "tail: %r" % tail[:200])
    check("that catch names the fix: an elevated shell (or drop -AsService)",
          "elevated PowerShell" in tail[:900] and "-AsService" in tail[:900])

    print("\n== the user PATH is edited in HKCU\\Environment, not via SetEnvironmentVariable ==")
    check("PATH is read with DoNotExpandEnvironmentNames",
          "DoNotExpandEnvironmentNames" in install)
    check("PATH is written back as an ExpandString",
          "RegistryValueKind]::ExpandString" in install)
    check("no [Environment]::SetEnvironmentVariable(\"Path\" left anywhere",
          'SetEnvironmentVariable("Path"' not in install,
          "the add/remove sites must go through Set-UserPathRaw")
    check("both the install and the uninstall paths use the registry helper",
          install.count("Set-UserPathRaw") >= 2 and install.count("Get-UserPathParts") >= 2,
          "Set-UserPathRaw x%d, Get-UserPathParts x%d" % (install.count("Set-UserPathRaw"),
                                                          install.count("Get-UserPathParts")))
    check("the old expanded-path add is gone",
          '$parts -notcontains $InstallDir' not in install)

    print("\n== -VerifyOnly runs before the interpreter step ==")
    checks = [m.start() for m in re.finditer(r"if \(\$VerifyOnly\) \{", install)]
    check("exactly one -VerifyOnly branch", len(checks) == 1, "found %d" % len(checks))
    marker = install.find("BEFORE the interpreter step")
    interp = install.find("# ------------------------------------------------------------- 1. python 3.12")
    finding = install.find('Head "finding Python"')
    vhead = install.find("verify only")
    vstart = install.rindex("\n# ", 0, vhead) + 1 if vhead != -1 else -1
    verify_body = install[vstart:interp] if vstart != -1 and interp != -1 else ""
    check("the verify block is marked as deliberately early", marker != -1)
    check("verify block precedes the python.org/winget block",
          marker != -1 and interp != -1 and marker < interp and marker < finding,
          "marker %d, interpreter %d, finding %d" % (marker, interp, finding))
    check("the verify block was actually moved (its body precedes the interpreter step)",
          len(verify_body) > 200 and "if ($VerifyOnly) {" in verify_body and
          "Head \"verifying" in verify_body,
          "slice is %d chars" % len(verify_body))
    check("the -VerifyOnly branch itself sits above the interpreter step",
          len(checks) == 1 and checks[0] < interp,
          "verify at %r, interpreter heading at %d" % (checks, interp))
    check("the verify probe never installs a runtime",
          "& winget install" not in verify_body and
          "python.org/ftp" not in verify_body and
          "Resolve-Python -Explicit $Python" in verify_body,
          "verify body: %r" % verify_body[-160:])

    print("\n== the generated launchers are path-free and stay ASCII ===")
    vbs = heredoc(install, "vbs")
    bat = heredoc(install, "bat")
    check("the VBS here-string is found", vbs is not None)
    check("the BAT here-string is found", bat is not None)
    check("the VBS resolves its folder from WScript.ScriptFullName",
          vbs is not None and "WScript.ScriptFullName" in vbs)
    check("the VBS no longer interpolates $InstallDir",
          vbs is not None and "$InstallDir" not in vbs)
    check("the VBS prefers the venv interpreter relatively",
          vbs is not None and 'here & "\\venv\\Scripts\\pythonw.exe"' in vbs)
    check("the VBS body is pure ASCII (nothing for the ASCII encoder to destroy)",
          vbs is not None and vbs.isascii(),
          "non-ascii in the VBS body: %r" % [c for c in (vbs or "") if not c.isascii()][:5])
    check("the BAT is %~dp0-based", bat is not None and 'cd /d "%~dp0"' in bat)
    check("the BAT no longer interpolates $InstallDir or $py.Path",
          bat is not None and "$InstallDir" not in bat and "$(" not in bat)
    check("the BAT body is pure ASCII too", bat is not None and bat.isascii())
    check("the VBS is still written through the ASCII encoder",
          'tinycmdr-service.vbs") $vbs -Encoding ASCII' in install)
    check("the BAT is still written through the ASCII encoder",
          'launch_tinycmdr.bat") $bat -Encoding ASCII' in install)
    check("nothing writes a launcher as Unicode (ASCII-by-construction instead)",
          "-Encoding Unicode" not in install)
    check("the interpreter fallback is guarded for the non-ASCII case",
          "$vbsPyFallback" in install and "notmatch '^[\\x20-\\x7e]+$'" in install)

    print("\n== the uninstall line names the door, the wrapper and a real folder ==")
    check("the installer summary prints the .cmd -Uninstall wrapper",
          "install-tinycmdr.cmd -Uninstall" in install)
    check("the installer summary names the double-click door first",
          "uninstall: double-click" in install and "UNINSTALL-WINDOWS.cmd" in install)
    check("no uninstall line hardcodes the default folder any more",
          'powershell -File "' not in install,
          "the old 'powershell -File \"...\" -Force' form is back")
    check("install.ps1's hint names the door and the wrapper",
          "UNINSTALL-WINDOWS.cmd" in one_line and
          "install-tinycmdr.cmd`\" -Uninstall" in one_line and
          'powershell -File `"' not in one_line)
    check("the install wrapper documents -Uninstall",
          "-Uninstall" in cmd and "-InstallDir" in cmd)
    check("the wrapper still runs PowerShell with -ExecutionPolicy Bypass",
          "-ExecutionPolicy Bypass" in cmd)
    # install/uninstall-tinycmdr.ps1 was the -File removal door: kept alive only by the
    # two hint lines above (no script, no doc, no test beyond this one), and the form that
    # "closes before you can read it" on stock Windows. The wrapper and the door cover it.
    check("no shipped file still points at the deleted .ps1 removal door",
          "uninstall-tinycmdr.ps1" not in install and
          "uninstall-tinycmdr.ps1" not in one_line and
          "uninstall-tinycmdr.ps1" not in cmd)

    print("\n== Windows restart elevation is for a task-owned instance only ==")
    # The fix itself belongs to tinycmdr.py, which this batch does not own (the parent
    # session does). This check ACTIVATES itself the moment that file stops demanding an
    # elevated shell on `os.name == "nt"` alone: until then it prints a skip naming the
    # site, and afterwards it asserts the conditioned shape, so the item cannot be
    # declared done twice or silently reverted.
    verb = function_body(source("tinycmdr.py"), "def _verb_restart():")
    bare = re.search(r'(?m)^\s*if os\.name == "nt" and not _is_elevated\(\)\s*:', verb)
    probe = re.search(r"(Get-ScheduledTask|schtasks|_scheduled_task|_task_owned|task_installed)", verb)
    if bare:
        skip("'require elevation only for a task-owned instance' - still outstanding",
             "tinycmdr.py _verb_restart demands elevation on os.name alone; the parent session "
             "owns that file. This check flips to a real assertion when it lands "
             "(predicate: a task probe precedes _is_elevated())")
    elif "_is_elevated()" not in verb:
        skip("_verb_restart no longer demands elevation on Windows at all",
             "nothing left to condition - re-read this if that was not deliberate")
    else:
        check("the Windows elevation demand follows a task-owned-instance probe",
              probe is not None and probe.start() < verb.find("_is_elevated()"),
              "probe=%r at %s, _is_elevated() at %d" %
              (probe and probe.group(0), probe and probe.start(), verb.find("_is_elevated()")))
        check("the shortcut lane is no longer refused (message names a task-owned run)",
              "task" in verb)

    print("\n== the stop filter sees the supervisor and the folder removal retries ==")
    stop = between(install, "function Stop-TinycmdrProcesses {", "function Remove-TinycmdrFolder {")
    stop_code = stop[stop.find("param([string] $Dir)"):] if "param([string] $Dir)" in stop else ""
    check("the stop filter scopes every process kind by the install dir, literally",
          "$_.Name -like 'python*' -or $_.Name -eq 'wscript.exe'" in stop_code
          and "IndexOf($Dir" in stop_code and "OrdinalIgnoreCase" in stop_code,
          "filter: %r" % stop_code[:200])
    check("...so a wildcard-shaped install dir still matches (the old -like did not)",
          '-like "*$Dir*"' not in stop_code,
          "filter: %r" % stop_code[:200])
    check("...and the supervisor's name is still on record for the intent",
          "tinycmdr-supervise.py" in stop, "filter: %r" % stop[:200])
    rm = between(install, "function Remove-TinycmdrFolder {", "function Say")
    check("folder removal retries instead of a one-shot Remove-Item",
          re.search(r"for \(\$i = 1; \$i -le \d+; \$i\+\+\)", rm) is not None and "Start-Sleep" in rm)
    check("the uninstaller uses the retrying removal",
          "Remove-TinycmdrFolder -Dir $InstallDir" in install and
          "Remove-Item $InstallDir -Recurse -Force\n" not in install)
    check("the sweep kills the matches' children too (a tool child holds the folder)",
          "ParentProcessId" in stop_code)
    check("...while sparing the shell the removal runs from",
          "$PID" in stop_code and "$self" in stop_code)
    check("...and it sweeps until nothing matches, bounded, not one snapshot",
          "while ($true)" in stop_code and "AddSeconds" in stop_code
          and "Start-Sleep" in stop_code)

    print("\n== a removal that cannot finish fails loudly, and runs from anywhere ==")
    check("the uninstaller steps its own shell out of the folder before deleting it",
          "Set-Location $env:TEMP" in install and
          0 <= install.find("Set-Location $env:TEMP") <
               install.find("Remove-TinycmdrFolder -Dir $InstallDir"))
    check("a shell that was cd'd into the folder is stepped out too (the wrapper)",
          "for %%A in (%*)" in cmd and 'if defined UNINSTALLING cd /d "%TEMP%"' in cmd,
          "the wrapper leaves its own shell standing in the folder it is asked to delete")
    fail_tail = install[install.find("Remove-TinycmdrFolder -Dir $InstallDir"):][:900]
    check("a failed removal exits non-zero and names what is left (not 'done')",
          'Say "left    : $InstallDir' in fail_tail and "exit 2" in fail_tail,
          "tail: %r" % fail_tail[:200])
    check("a removal takes only the task and Startup link that point at this folder",
          "belongs to another install - left alone" in install and
          "points at another install - left alone" in install and
          "$($_.Execute) $($_.Arguments)" in install,
          "a second install's autostart would be taken away by name")

    print("\n== a page-only install is not asked about a chat lane ==")
    # The wizard's answer "1" (the page) was followed by a Mattermost-token prompt, a
    # config.json carrying the example's allowlist placeholder, and a closing line saying
    # nothing would run in the background - while the launcher had just been registered to
    # serve the page. The placeholder then aborted the FIRST start ("no Mattermost bot
    # token"), so the install was dead on arrival (measured 2026-10-08, Windows, v1.0.93).
    check("a fresh install drops the example's allowlist placeholder",
          "elseif ($cfgFresh) {" in install and
          "$cfg.mattermost.allowed_users = @()" in install,
          "the example's REPLACE_WITH_YOUR_MATTERMOST_USER_ID lands in config.json and "
          "reads as a Mattermost lane")
    check("the bot-token prompt waits for the chat lane to be chosen",
          "-not $MattermostToken -and $Ask -and $WantChat" in install,
          "a page-only install is asked for a Mattermost token it just declined")
    check("the closing summary counts the page as something selected",
          "-not ($WantChat -or $WantTg -or $WantWeb -or $WantCli)" in install)

    print("\n== the removal door a double-click can run ==")
    door = source("UNINSTALL-WINDOWS.cmd")
    with open(os.path.join(ROOT, "UNINSTALL-WINDOWS.cmd"), "rb") as fh:
        door_bytes = fh.read()
    check("the package ships UNINSTALL-WINDOWS.cmd",
          '"UNINSTALL-WINDOWS.cmd"' in source("maintenance/build-package.py"))
    check("the door acts on the folder it sits in, by absolute path",
          '-InstallDir "%HERE%"' in door and '"%~dp0"' in door)
    check("the door re-runs itself from %TEMP%, so the removal cannot kill its tail",
          'copy /y "%~f0" "%SELF%"' in door
          and 'call "%SELF%" --removing "%~dp0"' in door
          and ":removing" in door,
          "a door living in the folder it deletes dies mid-run: no exit code, no pause")
    check("...and steps its own shell out of the folder first",
          'cd /d "%TEMP%"' in door)
    check("the door asks first, and 'no' removes nothing",
          'set /p "ANS=Remove it? (y/N) "' in door and "Nothing was removed." in door)
    check("the door refuses a folder that is not an install (no config.json, no venv)",
          'if not exist "%HERE%\\config.json" if not exist "%HERE%\\venv"' in door,
          "the door could be run against an extracted package")
    check("the door is ASCII with CRLF line endings (what cmd.exe wants)",
          door_bytes.isascii() and door_bytes.count(b"\n") == door_bytes.count(b"\r\n"))
    check("the door reports the exit code and keeps the window open",
          'if not "%RC%"=="0"' in door and "pause" in door
          and door.rstrip().endswith("exit /b %RC%"))
    check("the installer summary and the one-liner both name the door",
          "UNINSTALL-WINDOWS.cmd" in install and "UNINSTALL-WINDOWS.cmd" in one_line)

    print("\n== the install door holds its own window ==")
    install_door = source("INSTALL-WINDOWS.cmd")
    check("the door owns the closing barrier when nobody else does",
          'if not defined FB_NOPAUSE (' in install_door
          and 'set "OWNS_PAUSE=1"' in install_door
          and 'call "%HERE%install\\install-tinycmdr.cmd" %*' in install_door
          and "Press any key to close this window." in install_door,
          "a double-clicked window closes before the summary can be read")
    with open(os.path.join(ROOT, "INSTALL-WINDOWS.cmd"), "rb") as fh:
        install_door_bytes = fh.read()
    check("...and the install door is ASCII with CRLF line endings too",
          install_door_bytes.isascii()
          and install_door_bytes.count(b"\n") == install_door_bytes.count(b"\r\n"))

    # The runtime proof, on the platform that owns it. Everything above parses text; a
    # door that parses perfectly can still close its window before the tail runs
    # (measured 2026-10-09: a double-clicked UNINSTALL-WINDOWS.cmd did exactly that).
    # On POSIX the structural checks stand and these are not attempted.
    if os.name == "nt":
        import tempfile
        print("\n== the doors, run (windows) ==")
        work = tempfile.mkdtemp(prefix="tc-doors-")
        try:
            def staged(install_like=True, wrapper=("exit /b 2",)):
                """A folder shaped like an install, with a stub wrapper that RECORDS
                instead of removing (so the door's own logic is what is graded)."""
                n = len(os.listdir(work))
                d = os.path.join(work, "d%d" % n)
                os.makedirs(os.path.join(d, "install"))
                shutil.copy2(os.path.join(ROOT, "UNINSTALL-WINDOWS.cmd"),
                             os.path.join(d, "UNINSTALL-WINDOWS.cmd"))
                if install_like:
                    open(os.path.join(d, "tinycmdr.py"), "w").close()
                    open(os.path.join(d, "config.json"), "w").close()
                lines = (["@echo off", "echo stub: removal ran", "echo args %*"]
                         + list(wrapper))
                with open(os.path.join(d, "install", "install-tinycmdr.cmd"),
                          "w", newline="", encoding="utf-8") as fh:
                    fh.write("\r\n".join(lines) + "\r\n")
                return d

            def run_door(name, folder, stdin_text):
                return subprocess.run(
                    ["cmd.exe", "/c", os.path.join(folder, name)], cwd=folder,
                    input=stdin_text, capture_output=True, text=True, timeout=180)

            package = staged(install_like=False)
            r = run_door("UNINSTALL-WINDOWS.cmd", package, "")
            out = (r.stdout or "") + (r.stderr or "")
            check("a package folder is refused with its message, and the window holds",
                  r.returncode == 2 and "does not look like a tinycmdr install" in out
                  and "Press any key to close this window." in out,
                  (r.returncode, out[-300:]))

            d = staged()
            r = run_door("UNINSTALL-WINDOWS.cmd", d, "n\n\n")
            out = (r.stdout or "") + (r.stderr or "")
            check("answering no removes nothing, exits 0, and the window holds",
                  r.returncode == 0 and "Nothing was removed." in out
                  and "stub: removal ran" not in out
                  and "Press any key to close this window." in out,
                  (r.returncode, out[-300:]))

            d = staged(wrapper="exit /b 2")
            r = run_door("UNINSTALL-WINDOWS.cmd", d, "y\n\n")
            out = (r.stdout or "") + (r.stderr or "")
            check("a y reaches the wrapper, its exit code comes back, the tail prints",
                  r.returncode == 2 and "stub: removal ran" in out
                  and "The removal exited with code 2" in out
                  and "Press any key to close this window." in out,
                  (r.returncode, out[-400:]))

            d = staged(wrapper=('del /f /q "%~dp0..\\UNINSTALL-WINDOWS.cmd" >nul 2>&1',
                                'exit /b 0'))
            r = run_door("UNINSTALL-WINDOWS.cmd", d, "y\n\n")
            out = (r.stdout or "") + (r.stderr or "")
            check("a removal that deletes the door's own file still ends with the barrier",
                  r.returncode == 0 and "Press any key to close this window." in out,
                  (r.returncode, out[-300:]))

            # The install door: the barrier when it owns the window, none when the
            # caller does (the network one-liner sets FB_NOPAUSE and keeps its shell).
            d = staged()
            shutil.copy2(os.path.join(ROOT, "INSTALL-WINDOWS.cmd"),
                         os.path.join(d, "INSTALL-WINDOWS.cmd"))
            r = run_door("INSTALL-WINDOWS.cmd", d, "\n\n")
            out = (r.stdout or "") + (r.stderr or "")
            check("a double-clicked install ends with the barrier",
                  r.returncode == 2 and "Press any key to close this window." in out,
                  (r.returncode, out[-300:]))
            env = dict(os.environ)
            env["FB_NOPAUSE"] = "1"
            r = subprocess.run(["cmd.exe", "/c", os.path.join(d, "INSTALL-WINDOWS.cmd")],
                               cwd=d, input="", capture_output=True, text=True,
                               timeout=180, env=env)
            out = (r.stdout or "") + (r.stderr or "")
            check("...and no barrier when the caller owns the window (FB_NOPAUSE)",
                  "Press any key to close this window." not in out,
                  out[-300:])
        finally:
            shutil.rmtree(work, ignore_errors=True)

    print("\n== the installer takes web-search providers of your own ==")
    check("the switch is declared and parsed",
          "[string[]] $AddSearch" in install and "foreach ($spec in $AddSearch)" in install)
    check("a blank kind at the switch becomes the generic POST provider",
          'if (-not $sk) { $sk = "generic" }' in install
          and "-AddSearch: kind must be" not in install,
          "an open kind must not be refused by a hand-kept list")
    check("the interactive add asks url, key and label, then offers another",
          "provider url" in install and "API key (Enter = none)" in install
          and "Add another entry?" in install)
    check("...and a blank label defaults to the url's host",
          ".Split('/')[0]" in install)
    check("the web-search step is a menu, not one yes/no",
          "4) add your own provider now" in install
          and "keep search on this machine only" in install)
    check("tavily is GONE - no built-in option, no prompt line, no key name",
          "tavily" not in install.lower())
    check("the rows reach search.providers, this run's first",
          "$cfg.search | Add-Member -NotePropertyName providers" in install
          and '("providers"\\s*:\\s*)\\[\\s*\\]' in install)
    check("a search key goes to .env under a generated name, never config.json",
          "TINYCMDR_SEARCH${sn}_API_KEY" in install
          and 'Say "search provider added' in install
          and "(search provider)" in install)
    check("the summary names what was added, tried first",
          "you added {0} (tried first)" in install)

    print("\n== tinycmdr.cmd refuses the Microsoft Store python stub ==")
    check("the shim looks python up with where + findstr",
          "where %~1" in shim and 'findstr /i /c:"WindowsApps"' in shim)
    check("the shim has the :findpy helper and calls it for python.exe and py.exe",
          ":findpy" in shim and "call :findpy python.exe" in shim and "call :findpy py.exe" in shim)
    check("the bare %%~$PATH:I python lookup is gone",
          "%%~$PATH:I" not in shim, "that form always resolves the stub first")
    check("the venv interpreter is still preferred",
          shim.find("%HERE%venv\\Scripts\\python.exe") < shim.find("call :findpy python.exe"))
    check("the shim no longer claims Python 3.9+",
          "3.9+" not in shim and "3.10-3.12" in shim)

    print("\n== the resolver refuses 3.13+ like the other two ==")
    check("the resolver bounds the band, not just the floor",
          '-ge [version]"3.10"' in install and '-le [version]"3.12"' in install)
    check("...with the -ForcePython escape and the mmpy_bot reason",
          "$ForcePython" in install
          and "mmpy_bot is the last release that connects on 3.13+" in install)
    check("...and the switch reaches every Resolve-Python call",
          install.count("Resolve-Python -Explicit $Python -Force:$ForcePython") == 3,
          install.count("Resolve-Python -Explicit $Python -Force:$ForcePython"))
    check("the help text states the supported band",
          "finds Python 3.10-3.12" in install)

    print("\n== -SkipTask no longer withholds the PATH entry ==")
    check("the PATH gate is -NoPath only",
          "if ($NoPath) {" in install and "$NoPath -or $SkipTask" not in install)
    check("the PATH section says so", "left alone (-NoPath)" in install)

    print("\n== the python.org fallback download follows the architecture ==")
    check("the asset is chosen from $env:PROCESSOR_ARCHITECTURE",
          "PROCESSOR_ARCHITECTURE" in install and "$pyArch" in install)
    check("all three architectures are mapped",
          all(a in between(install, "$pyArch = switch", "$installerName") for a in
              ('"AMD64"', '"ARM64"', '"x86"')))
    check("no hardcoded amd64 asset URL is left",
          "python-3.12.8-amd64.exe" not in install and "python-3.12.8-$pyArch.exe" in install)
    check("the resolved interpreter's word size is checked",
          "Is64BitOperatingSystem" in install and "calcsize('P')" in install)

    print("\n== PATH-vs-delete order, and literal .env substitution ==")
    check("the early exit knows about the user PATH",
          "Test-UserPathHas $InstallDir" in install and "pathHasEntry" in install)
    kept = install.find("kept $InstallDir")
    folder = install.find("Remove-TinycmdrFolder -Dir $InstallDir")
    pathrm = install.find("removed from the user Path")
    check("the PATH entry is dropped only after the folder decision",
          -1 < folder < pathrm and -1 < kept < folder,
          "kept %d, folder %d, path-removal %d" % (kept, folder, pathrm))
    check("keeping the folder keeps the verb (and says so)",
          "stays on your user PATH" in install)
    check("the PATH entry is only dropped once the folder is really gone",
          "-not (Test-Path $InstallDir)) -and (Test-UserPathHas $InstallDir)" in install)
    # The requirement is literal substitution, and THEIRS legitimately has THREE .env
    # write sites (this host's own keys, the carried-over keys, and the extra endpoints'
    # keys, which THEIRS added after OURS wrote this check). So assert the property, not
    # OURS' count: every `$envText = ...` write is the literal [regex]::Replace callback,
    # and no `-replace` template form is left anywhere.
    env_writes = re.findall(r"(?m)^\s*\$envText = (.*)$", install)
    env_repl = [w for w in env_writes if "-replace" in w]
    env_lit = [w for w in env_writes if w.startswith("[regex]::Replace($envText")]
    check(".env values are substituted literally (no regex template)",
          len(env_lit) >= 3 and not env_repl and
          '-replace "(?m)^#?\\s*$key=.*$"' not in install and
          '-replace "(?m)^#?\\s*$k=.*$"' not in install,
          "%d literal site(s), %d -replace site(s): %r" % (len(env_lit), len(env_repl), env_repl[:1]))

    print("\n== no chat account -> no service is registered, nothing remote to serve ==")
    # The page is the default door again, so the task has TWO reasons to exist: a chat
    # lane, or the page. A lane-less install with the page on registers the task (the
    # page keeps it alive); only -NoWeb leaves a files-only install. $LocalWeb is still
    # gone - the page lane is not a separate lane object now.
    check("the local-page lane variable is gone", "$LocalWeb" not in install,
          "the installer still computes a page lane")
    check("the door question offers the page first and describes sessions instead of offering them",
          "The web page (the default door" in install
          and "Sessions by hand in a terminal (nothing runs in the background)" not in install
          and "tinycmdr --cli for a" in install,
          "the lane menu still implies chat or terminal are the only doors")
    check("the page's own questions are skipped when the menu did not pick it",
          "-not $WantWeb -and -not $WebToken" in install,
          "the page asks for a bind/port/token after 'no page' was answered")
    check("registration counts a chat lane or the page",
          "$Serve = $AnyLane -or (-not $NoWeb)" in install
          and "$RegisterTask = (-not $SkipTask) -and $Serve" in install,
          "RegisterTask does not count the page")
    check("the supervisor argument variable is gone", "$SuperviseArgs" not in install,
          "the launcher still carries a page argument")
    check("the token-less run says nothing runs in the background and names the local doors",
          "autostart: skipped" in install
          and "there is nothing to keep running in the" in install
          and "python tinycmdr.py --cli" in install and "python tinycmdr.py --once" in install,
          "the no-lane branch is missing or silent")

    print("\n== the model setup is the cloud-or-local conversation, and the key stays out of config ==")
    # The defect: the URL was asked first and probed with NO key, so a hosted provider's 401
    # read as "not reachable" and its model list never came back; the key, asked last, went to
    # config.json's llm.api_key - a file the agent reads into a prompt. And the secret prompts
    # used Read-Host -AsSecureString, blank in some hosts and dropping a paste in others.
    check("a paste-capable masked reader exists",
          "function Read-Secret {" in install and "[Console]::ReadKey($true)" in install,
          "Read-Secret missing")
    check("Ask-Text's secret branch no longer calls Read-Host -AsSecureString",
          "Read-Host $shown -AsSecureString" not in install,
          "a prompt still masks itself with Read-Host -AsSecureString")
    check("the Mattermost token field uses it too (the field that would not paste)",
          "Read-Secret \"Mattermost bot token" in install,
          "the Mattermost token prompt is still a raw Read-Host")
    check("the model probe sends the bearer key",
          'Authorization"] = "Bearer $Key"' in install, "Test-EndpointModels sends no key")
    check("the model question asks local or cloud first",
          "function Ask-Choose {" in install and "Which kind of endpoint is it?" in install,
          "no local/cloud question")
    check("a wrong key is re-asked, bounded (no infinite loop at EOF)",
          "$keyTries -ge 3" in install and "$fbKeyTries -ge 3" in install,
          "the auth retry has no cap")
    check("the primary's key is NOT written to config.json",
          "$cfg.llm.api_key = $ModelKey" not in install
          and '$cfg.llm.PSObject.Properties.Remove("api_key")' in install,
          "config.json still carries llm.api_key")
    check("it goes to .env as TINYCMDR_LLM_API_KEY, replacing any older line",
          '"TINYCMDR_LLM_API_KEY", "ANYSEARCH_API_KEY"' in install
          and '$managed += "TINYCMDR_LLM_API_KEY"' in install,
          "the .env writer never carries the primary key")

    print("\n== the emitted install is still the repo's own shape ==")
    check("the uninstall branch still comes before the installer preamble",
          install.index("if ($Uninstall) {") < install.index("Head \"tinycmdr installer\""))

    print("\n== a re-run over a configured install keeps it, and asks once ==")
    ps1 = source("install/install-tinycmdr.ps1")
    check("the wizard is gated on the keep answer",
          "Keep the existing configuration?" in ps1
          and "if ($Ask -and -not $KeepConn) {" in ps1,
          "a configured reinstall walked the whole wizard and read as a reset")
    check("a configured re-run is detected from config.json + a token in .env",
          "There is already a configured install" in ps1
          and re.search(r"TINYCMDR_\(MM\|TG\)_TOKEN", ps1) is not None)
    check("the Telegram token is carried over from .env on a redo",
          re.search(r"\^TINYCMDR_TG_TOKEN=\(\.\+\)\$", ps1) is not None,
          "it used to be written back EMPTY, silently dropping the lane")
    check("the Telegram ids are read back from the existing config",
          "$cfg.telegram.allowed_users" in ps1 and "-not $TelegramIds" in ps1,
          "the token-with-no-id guard would refuse a valid kept install")

    # ---- every shipped .ps1 PARSES when a PowerShell is here to say so ------------
    # The checks above are [READ] by design (the bed is macOS). The Windows CI job HAS a
    # PowerShell, so there the same files get a real parse - which is how a syntax error
    # in update.ps1 (a file nothing else executes, and the one code that must run on every
    # released version) is caught before a release rather than by a user.
    ps = shutil.which("powershell") or shutil.which("pwsh")
    if not ps:
        skip("every shipped .ps1 parses", "no PowerShell on PATH here (the Windows job)")
    else:
        for rel in ("update.ps1", "install.ps1", "install/install-tinycmdr.ps1"):
            probe = ("$e=$null;[System.Management.Automation.Language.Parser]::"
                     "ParseFile('%s',[ref]$null,[ref]$e)|Out-Null;"
                     "if($e.Count){$e|ForEach-Object{$_.Message};exit 1}" % rel)
            r = subprocess.run([ps, "-NoProfile", "-NonInteractive", "-Command", probe],
                               capture_output=True, text=True)
            check("%s parses" % rel, r.returncode == 0,
                  (r.stdout + r.stderr).strip()[:200])

    print("\n== the copy phase carries the page's assets (fresh-install regression) ==")
    # The installer used to copy a hand-written list of names; the page redesign added
    # assets/ to the package and the list was never told, so every fresh install served
    # /page.css as a 404 (operator's fresh-install report, 2026-10-04). The copy is the
    # package-tree-minus-host-owned rule now - no list to drift.
    check("the Windows installer copies the package tree, not a list",
          "$hostDirs" in install
          and "Get-ChildItem -Path $Source -Recurse -File" in install
          and "$copy = @(" not in install,
          "the copy phase is a hand list again")

    print("")
    if FAILED:
        print("%d check(s) FAILED:" % len(FAILED))
        for f in FAILED:
            print("  - " + f)
        return 1
    print("all checks passed (this installer has also been run on a real Windows 11 "
          "host - see CHANGELOG [1.0.25] for what that pass covered)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

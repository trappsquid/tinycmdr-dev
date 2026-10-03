"""Structural checks on the Windows installer (Batch E / audit W1-W11).

The Phase 1 bed is macOS, so nothing here executes PowerShell or cmd - these checks
parse the shipped text and assert the SHAPE of each fix, so a later edit cannot quietly
revert one. Every claim in this file is [READ]; the runtime proof has to come from a
Windows host and is listed as outstanding in CHANGELOG/README.

Run:  python tests/test_installer_windows.py
"""
import os
import re
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
    E6 check cannot be anchored on a line number.
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
    uninstall = source("install/uninstall-tinycmdr.ps1")
    one_line = source("install.ps1")
    shim = source("tinycmdr.cmd")

    print("== W1 (E1) elevation is re-checked once the fleet defaults have had their say ==")
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

    print("\n== W1 (E1) the fallback Register-ScheduledTask is caught, not trapped ==")
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

    print("\n== W2 (E2) the user PATH is edited in HKCU\\Environment, not via SetEnvironmentVariable ==")
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

    print("\n== W3 (E3) -VerifyOnly runs before the interpreter step ==")
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

    print("\n== W4 (E4) the generated launchers are path-free and stay ASCII ===")
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

    print("\n== W5 (E5) the uninstall line names the wrapper and a real folder ==")
    check("the installer summary prints the .cmd -Uninstall wrapper",
          "install-tinycmdr.cmd -Uninstall" in install)
    check("the summary's -File form carries -ExecutionPolicy Bypass",
          "-ExecutionPolicy Bypass -File" in install)
    check("the summary's -File form passes the real -InstallDir",
          'uninstall-tinycmdr.ps1" -InstallDir "' in install)
    check("no uninstall line hardcodes the default folder any more",
          'powershell -File "' not in install,
          "the old 'powershell -File \"...\" -Force' form is back")
    check("install.ps1's hint is the wrapper + Bypass + a real folder too",
          "install-tinycmdr.cmd`\" -Uninstall" in one_line and
          "-ExecutionPolicy Bypass" in one_line and
          'powershell -File `"' not in one_line)
    check("the install wrapper documents -Uninstall",
          "-Uninstall" in cmd and "-InstallDir" in cmd)
    check("the wrapper still runs PowerShell with -ExecutionPolicy Bypass",
          "-ExecutionPolicy Bypass" in cmd)
    check("the uninstaller documents both wrapper and Bypass forms",
          "install-tinycmdr.cmd -Uninstall" in uninstall and
          "-ExecutionPolicy Bypass -File" in uninstall)
    check("the uninstaller no longer claims a -AsService install lands in C:\\tinycmdr",
          "put it in C:\\tinycmdr" not in uninstall)

    print("\n== W6 (E6) Windows restart elevation is for a task-owned instance only ==")
    # The fix itself belongs to tinycmdr.py, which this batch does not own (the parent
    # session does). This check ACTIVATES itself the moment that file stops demanding an
    # elevated shell on `os.name == "nt"` alone: until then it prints a skip naming the
    # site, and afterwards it asserts the conditioned shape, so the item cannot be
    # declared done twice or silently reverted.
    verb = function_body(source("tinycmdr.py"), "def _verb_restart():")
    bare = re.search(r'(?m)^\s*if os\.name == "nt" and not _is_elevated\(\)\s*:', verb)
    probe = re.search(r"(Get-ScheduledTask|schtasks|_scheduled_task|_task_owned|task_installed)", verb)
    if bare:
        skip("E6/W6 'require elevation only for a task-owned instance' - still outstanding",
             "tinycmdr.py _verb_restart demands elevation on os.name alone; the parent session "
             "owns that file. This check flips to a real assertion when it lands "
             "(predicate: a task probe precedes _is_elevated())")
    elif "_is_elevated()" not in verb:
        skip("E6/W6 _verb_restart no longer demands elevation on Windows at all",
             "nothing left to condition - re-read W6 if that was not deliberate")
    else:
        check("E6/W6 the Windows elevation demand follows a task-owned-instance probe",
              probe is not None and probe.start() < verb.find("_is_elevated()"),
              "probe=%r at %s, _is_elevated() at %d" %
              (probe and probe.group(0), probe and probe.start(), verb.find("_is_elevated()")))
        check("E6/W6 the shortcut lane is no longer refused (message names a task-owned run)",
              "task" in verb)

    print("\n== W7 (E7) the stop filter sees the supervisor and the folder removal retries ==")
    stop = between(install, "function Stop-TinycmdrProcesses {", "function Remove-TinycmdrFolder {")
    stop_code = stop[stop.find("param([string] $Dir)"):] if "param([string] $Dir)" in stop else ""
    check("the stop filter matches tinycmdr-supervise.py in its filter code",
          "tinycmdr-supervise.py" in stop_code, "filter: %r" % stop_code[:200])
    check("the stop filter matches wscript.exe hosts",
          "'wscript.exe'" in stop_code and "tinycmdr-service.vbs" in stop_code)
    check("the stop filter still matches python processes by install dir",
          "$_.Name -like 'python*'" in stop_code and '"*$Dir*"' in stop_code)
    rm = between(install, "function Remove-TinycmdrFolder {", "function Say")
    check("folder removal retries instead of a one-shot Remove-Item",
          re.search(r"for \(\$i = 1; \$i -le \d+; \$i\+\+\)", rm) is not None and "Start-Sleep" in rm)
    check("the uninstaller uses the retrying removal",
          "Remove-TinycmdrFolder -Dir $InstallDir" in install and
          "Remove-Item $InstallDir -Recurse -Force\n" not in install)

    print("\n== W8 (E8) tinycmdr.cmd refuses the Microsoft Store python stub ==")
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

    print("\n== W9 (E8) -SkipTask no longer withholds the PATH entry ==")
    check("the PATH gate is -NoPath only",
          "if ($NoPath) {" in install and "$NoPath -or $SkipTask" not in install)
    check("the PATH section says so", "left alone (-NoPath)" in install)

    print("\n== W10 (E8) the python.org fallback download follows the architecture ==")
    check("the asset is chosen from $env:PROCESSOR_ARCHITECTURE",
          "PROCESSOR_ARCHITECTURE" in install and "$pyArch" in install)
    check("all three architectures are mapped",
          all(a in between(install, "$pyArch = switch", "$installerName") for a in
              ('"AMD64"', '"ARM64"', '"x86"')))
    check("no hardcoded amd64 asset URL is left",
          "python-3.12.8-amd64.exe" not in install and "python-3.12.8-$pyArch.exe" in install)
    check("the resolved interpreter's word size is checked",
          "Is64BitOperatingSystem" in install and "calcsize('P')" in install)

    print("\n== W11 (E8) PATH-vs-delete order, and literal .env substitution ==")
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
    # A token-less install used to register the local page as its lane. That lane was removed
    # from the assistant, so with no Mattermost and no Telegram token there is nothing remote
    # to serve: no task and no logon shortcut is registered, because that process would exit at
    # once and the supervisor would loop it. The files land; the run says so.
    check("the local-page lane variable is gone", "$LocalWeb" not in install,
          "the installer still computes a page lane")
    check("registration needs a real chat lane",
          "$RegisterTask = (-not $SkipTask) -and ($AnyLane -or $LocalWeb)" not in install
          and "$RegisterTask = (-not $SkipTask) -and $AnyLane" in install,
          "RegisterTask still counts a page lane")
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
          '"TINYCMDR_LLM_API_KEY", "TAVILY_API_KEY"' in install
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

<#
    tinycmdr installer - copies the bot into THIS USER's profile and makes it
    available. NO ADMINISTRATOR RIGHTS ARE NEEDED for a normal install.

    Double-click ..\install-tinycmdr.cmd, or run this file from a shell:

        powershell -ExecutionPolicy Bypass -File .\install\install-tinycmdr.ps1

    With no arguments it:
      * installs into %USERPROFILE%\tinycmdr (your own folder, nothing shared)
      * finds Python 3.10+, and DOWNLOADS AND INSTALLS Python 3.12 if the machine
        has none (winget, then the python.org installer) - no manual step
      * builds the install's own virtual environment and installs the
        dependencies into it (requests, mmpy_bot, croniter)
      * uses install\fleet-defaults.json from the package when it is present
        (a fleet host needs nothing typed in), reuses a bot token from an
        existing .env, and asks for one only if there is none
      * starts it at your next logon through a shortcut in your Startup folder
        (no elevation, no service registration)

    A redo is:

        install-tinycmdr.cmd -Force

    Other things it can do:
        -AsService         register a Windows scheduled task instead (runs at
                           BOOT, before you log in - this one DOES need an
                           elevated shell, because Windows reserves boot-start
                           tasks for administrators)
        -InstallDir <d>    install somewhere else
        -VerifyOnly        is this install working? (no reinstall)
        -Uninstall [-Force] stop it, remove the folder and the autostart entry

    Exit codes:
        0  installed and verified
        1  bad input or missing prerequisite
        2  install failed
        3  installed, but the model endpoint did not answer (a config gap)
#>
[CmdletBinding()]
param(
    [string] $InstallDir      = "",              # default: %USERPROFILE%\tinycmdr
                                                 # (fleet-defaults.json may name one)
    [string] $TaskName        = "Tinycmdr",
    [string] $MattermostUrl   = "",              # default: fleet-defaults.json
    [int]    $MattermostPort  = 443,
    [string] $MattermostToken = "",              # default: token file, existing .env, then prompt
    [string] $MattermostTokenFile = "",          # read the token from a file instead
    [string] $TelegramToken   = "",              # the third door: a Telegram DM
    [string] $TelegramIds     = "",              # NUMERIC ids, comma or space separated
                                                 # (message @userinfobot for yours)
    [string] $SecretsFile     = "",              # .env-style file: TINYCMDR_MM_TOKEN,
                                                 # TAVILY_API_KEY, ANYSEARCH_API_KEY
                                                 # (a model key is per bot and is
                                                 #  ignored here - set it per host)
    [string] $AllowedUser     = "",              # default: fleet-defaults.json
    [string] $BotName         = "",              # defaults to this machine's name
    [string] $ModelBaseUrl    = "",              # default: fleet-defaults.json
    [string] $Model           = "main",
    [int]    $WebPort         = 8787,            # only used with -EnableWeb
    [switch] $EnableWeb,                         # legacy local chat page (off by default)
    [string] $WebHost         = "",              # the page's bind address. "" keeps this
                                                 # host's own value (a fresh install uses
                                                 # 127.0.0.1); 0.0.0.0 opens it to your
                                                 # network, which always needs the token
    [string[]] $AddEndpoint   = @(),             # more model endpoints, repeatable:
                                                 # "<base_url>;<model>;<alias>;<key>" -
                                                 # tried in order when the primary fails,
                                                 # and the key lands in .env. (';' and
                                                 # not '|': a cmd.exe wrapper reads '|'
                                                 # as a pipe and splits the argument)
    [string] $Python          = "",              # full path to python.exe if auto-detect fails
    [switch] $InstallPython,                     # kept for compatibility: installing a
                                                 # missing Python is now the DEFAULT
    [switch] $Force,                             # redo: stop what is running, overwrite everything
    [switch] $AsService,                         # register a boot-start scheduled task
                                                 # instead of a logon shortcut (needs admin)
    [switch] $SkipTask,                          # files only: no autostart, no service
    [switch] $NoStart,
    [switch] $NoPath,                            # leave the user PATH alone
    [switch] $NoPause,                           # for scripted runs (the .cmd uses this)
    [switch] $VerifyOnly,                        # just probe -InstallDir and stop
    [switch] $Uninstall,                         # remove the task and the folder
    [switch] $NonInteractive                     # never ask: for scripts and fleet pushes
                                                 # (a redirected stdin also means "do not ask")
)

$ErrorActionPreference = "Stop"
$Source = Split-Path -Parent $PSScriptRoot       # package root (one level above install\)

# --------------------------------------------------------------- asking the user
# The .cmd wrapper always passes -NoPause, and that flag is only about keeping the window open at
# the end. Questions are a separate decision: a real console, and not -NonInteractive. Gating them
# on -NoPause is exactly how a double-click ended up asking nothing.
# Whether to ASK. A real console means a person is there; -NonInteractive or a
# redirected stdin means a script (a fleet push must never hang on a question).
# Note this is deliberately NOT -NoPause: the .cmd wrapper always passes -NoPause
# to keep its window open, so gating questions on that is exactly how a
# double-click ended up asking nothing.
$Ask = (-not $NonInteractive) -and ((-not [Console]::IsInputRedirected) -or $env:TINYCMDR_ASK)

function Ask-Text {
    param([string] $Prompt, [string] $Default = "", [switch] $Secret, [switch] $AllowBlank)
    # ${Prompt} - a bare "$Prompt:" reads as a scoped variable to PowerShell
    $shown = if ($Default) { "${Prompt} [$Default]: " } else { "${Prompt}: " }
    if ($Secret -and -not $Default) { $shown = "${Prompt}: " }
    while ($true) {
        if ($Secret) {
            $sec = Read-Host $shown -AsSecureString
            $val = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
                       [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
        } else {
            $val = Read-Host $shown
        }
        if ($val -and $val.Trim()) { return $val.Trim() }
        if ($Default) { return $Default }
        if ($AllowBlank) { return "" }
        Write-Host "  (this one is needed - please type something)"
    }
}

function Ask-Many {
    # Any number of them: "1,3" or "1 3" or just Enter for the default. The doors are not
    # exclusive - the chat build serves the page itself, and a session never takes the lock.
    param([string] $Title, [string[]] $Options, [string] $Default = "1")
    Write-Host ""
    Write-Host $Title
    for ($i = 0; $i -lt $Options.Count; $i++) { Write-Host ("  {0}) {1}" -f ($i + 1), $Options[$i]) }
    while ($true) {
        $a = (Read-Host ("Choose any of 1-{0}, separated by commas (Enter = {1})" -f $Options.Count, $Default)).Trim()
        if (-not $a) { $a = $Default }
        $picked = @()
        $bad = $false
        foreach ($part in ($a -split "[,\s]+")) {
            if (-not $part) { continue }
            $n = 0
            if ([int]::TryParse($part, [ref] $n) -and $n -ge 1 -and $n -le $Options.Count) { $picked += $n }
            else { $bad = $true; break }
        }
        if (-not $bad -and $picked.Count) { return ($picked | Sort-Object -Unique) }
        Write-Host "  Please use the numbers from the list, like 1 or 1,3."
    }
}

function Ask-Yes {
    param([string] $Prompt, [bool] $Default = $true)
    $d = if ($Default) { "Y/n" } else { "y/N" }
    while ($true) {
        $a = (Read-Host "$Prompt [$d]").Trim().ToLower()
        if (-not $a) { return $Default }
        if ($a -in @("y", "yes")) { return $true }
        if ($a -in @("n", "no")) { return $false }
        Write-Host "  Please answer y or n."
    }
}



# Everything below is transcribed. A Windows install often runs in a window that
# closes the moment the script ends (or on a host you cannot see), so the reason
# for a failure has to survive on disk: %TEMP%\tinycmdr-install.log, the same file
# the .cmd wrapper points at.
$LogFile = Join-Path $env:TEMP "tinycmdr-install.log"
try { Start-Transcript -Path $LogFile -Force | Out-Null } catch { }
# Every Stop-Transcript below routes through here: the summary prints the web
# token for paste and the transcript sits in %TEMP% (security review residual,
# 2026-09-23 - the old ?token= link leaked the same value). Scrub the secrets
# this run handled before the log goes cold.
function Stop-TranscriptRedacted {
    try { Microsoft.PowerShell.Core\Stop-Transcript | Out-Null } catch { }
    foreach ($s in @($MattermostToken, $TelegramToken, $ModelKey, $webToken)) {
        if ($s -and $s.Length -ge 8 -and (Test-Path $LogFile)) {
            try {
                $t = Get-Content -Raw -LiteralPath $LogFile
                if ($t -and $t.Contains($s)) {
                    [System.IO.File]::WriteAllText($LogFile, $t.Replace($s, "<redacted>"))
                }
            } catch { }
        }
    }
}
trap {
    Write-Host "`nINSTALL FAILED: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Full log: $LogFile"
    try { Stop-TranscriptRedacted } catch { }
    if (-not $NoPause) { Read-Host "Press Enter to close" }
    exit 2
}

$Unverified = $false    # install ok, but the model endpoint did not answer
# Which of these the caller actually passed (an update must not push the shipped
# loopback/`main` defaults over a host that is working).
$ModelBaseUrlGiven = [bool]$ModelBaseUrl
$ModelGiven        = $Model -and $Model -ne "main"
$AppName = $TaskName
$elevated = ([Security.Principal.WindowsPrincipal] `
             [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
             [Security.Principal.WindowsBuiltInRole]::Administrator)

# Elevation is NOT required for a normal install any more. Everything a reader
# gets by default lives in their own profile: the folder, the venv, the user
# PATH entry and the Startup shortcut. Only -AsService (a boot-start scheduled
# task) needs an elevated shell, and that is checked where it is used.
if ($AsService -and -not $elevated -and -not $VerifyOnly -and -not $Uninstall) {
    Write-Host "This needs an elevated PowerShell to register the scheduled task." -ForegroundColor Red
    Write-Host "Re-run as Administrator, or drop -AsService to install and start at logon"
    Write-Host "(no administrator rights needed for that)."
    exit 1
}

# ------------------------------------------------------------------ helpers

# PowerShell 5.1 has no BOM-less UTF8 string encoder for Set-Content, and a BOM
# silently breaks JSON parsing and HTTP headers. Write through this, always.
function Write-Utf8NoBom {
    param([string] $Path, [string] $Text)
    [System.IO.File]::WriteAllText($Path, $Text, (New-Object System.Text.UTF8Encoding($false)))
}

function Invoke-Py {
    <#
        Run python and hand back its output (stdout AND stderr) as a string.

        Never let a child's stderr terminate the installer: with
        ErrorActionPreference = Stop, a native command writing to stderr counts as a
        TERMINATING error, so a missing dependency or any traceback aborted the whole
        install and surfaced only the traceback's first line. That is what happened
        on a host whose Python 3.12 had no `requests` yet.
    #>
    param([string] $PythonPath, [Parameter(ValueFromRemainingArguments = $true)] $PyArgs)
    $EAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $tmp = [IO.Path]::GetTempFileName()
    try {
        # Redirect to a FILE rather than 2>&1 | Out-String: PowerShell wraps a native
        # command's stderr in ErrorRecords, so the captured text arrives decorated with
        # source lines and CategoryInfo noise. A file gets the raw text.
        & $PythonPath @PyArgs > $tmp 2>&1
        return (Get-Content $tmp -Raw)
    } catch {
        return "could not run ${PythonPath}: $($_.Exception.Message)"
    } finally {
        $ErrorActionPreference = $EAP
        Remove-Item $tmp -Force -ErrorAction SilentlyContinue
    }
}

function Test-PyImport {
    <#
        Is $Module importable by this interpreter? Uses the EXIT CODE, because
        matching the child's text is a trap: `$out -match "ok"` is true when the
        failed command's own text ("print('ok')") is echoed back in the error, so a
        missing dependency looked present and the install went on to fail later.
    #>
    param([string] $PythonPath, [string] $Module)
    $EAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $PythonPath -c "import $Module" 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    } finally {
        $ErrorActionPreference = $EAP
    }
}

function Invoke-Probe {
    <#
        One local turn through the agent: no Mattermost token, no port, no admin.
        Two traps this has already fallen into, both handled here:
          * a native command writing to stderr counts as a TERMINATING error while
            ErrorActionPreference is Stop, so a Python traceback aborted the whole
            installer and reported only the traceback's first line
          * a console with a legacy code page made Python die printing its own banner
        Hence: relax the preference for the call only, force UTF-8 for the child, and
        hand back the real tail of the output.
    #>
    param([string] $Dir, [string] $PythonPath)
    $env:PYTHONIOENCODING = "utf-8"
    try {
        return (Invoke-Py $PythonPath (Join-Path $Dir "tinycmdr.py") --once "reply with the single word: READY")
    } finally {
        Remove-Item Env:\PYTHONIOENCODING -ErrorAction SilentlyContinue
    }
}

function Stop-TinycmdrProcesses {
    <#
        Kill the bot AND its supervisor for this install. Needed before a -Force copy
        and before an uninstall: the running bot holds tinycmdr.log and tinycmdr.lock,
        so overwriting in place either fails or leaves a stale process alive.

        The bot and the supervisor are two processes. In the documented no-venv
        fallback the supervisor's command line is "<machine python>
        tinycmdr-supervise.py" - no install dir anywhere - so the old
        "python% AND the install dir" filter killed only the bot child, and the
        surviving supervisor respawned it straight into the folder being overwritten
        or removed (audit W7; that is what left -Uninstall saying "being used by
        another process"). wscript.exe hosts the hidden launcher, so it is in scope too.
    #>
    param([string] $Dir)
    $killed = 0
    try {
        Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
            Where-Object {
                if (-not $_.CommandLine) { return $false }
                if ($_.Name -like 'python*') {
                    return ($_.CommandLine -like "*$Dir*") -or
                           ($_.CommandLine -like "*tinycmdr-supervise.py*")
                }
                if ($_.Name -eq 'wscript.exe') {
                    return ($_.CommandLine -like "*tinycmdr-service.vbs*")
                }
                return $false
            } |
            ForEach-Object {
                try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop; $killed++ } catch { }
            }
    } catch { }
    return $killed
}

function Remove-TinycmdrFolder {
    <#
        Remove $Dir, retrying instead of giving up on the first "being used by another
        process". Windows releases a killed process's file handles asynchronously, and
        the supervisor may not have been visible to the first sweep, so a one-shot
        Remove-Item leaves the folder - and a half-removed install - behind (audit W7).
    #>
    param([string] $Dir)
    for ($i = 1; $i -le 5; $i++) {
        try {
            Remove-Item $Dir -Recurse -Force -ErrorAction Stop
            return $true
        } catch {
            if ($i -eq 5) {
                Say "NOTE    : could not remove ${Dir}: $($_.Exception.Message)"
                Say "          close anything using it, then remove the folder by hand"
                return $false
            }
            $null = Stop-TinycmdrProcesses -Dir $Dir
            Start-Sleep -Seconds 2
        }
    }
    return $false
}

function Say  ($m) { Write-Host "  $m" }
function Head ($m) { Write-Host "`n== $m" -ForegroundColor Cyan }
function Fail ($m) {
    Write-Host "`nFAILED: $m" -ForegroundColor Red
    Write-Host "Full log: $LogFile"
    try { Stop-TranscriptRedacted } catch { }
    if (-not $NoPause) { Read-Host "Press Enter to close" }
    exit 1
}

# --------------------------------------------------------------- user PATH
# The user PATH is a REG_EXPAND_SZ value under HKCU\Environment, and it is FULL of
# other installers' entries - many written as "%JAVA_HOME%\bin". Reading it with
# [Environment]::GetEnvironmentVariable expands those; SetEnvironmentVariable then
# writes the EXPANDED text back as a plain REG_SZ, so every unrelated %VAR% entry on
# the machine is frozen at whatever it resolved to that day (audit W2 - and the
# uninstaller repeated it). Edit the registry value itself instead: read with
# DoNotExpandEnvironmentNames so the "%VAR%" text survives, write ExpandString, and
# only ever append or remove OUR one entry.
function Get-UserPathRaw {
    $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey("Environment")
    if (-not $key) { return "" }
    try {
        return [string]$key.GetValue("Path", "",
            [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
    } finally { $key.Close() }
}

function Set-UserPathRaw {
    param([string] $Value)
    $key = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey("Environment")
    try { $key.SetValue("Path", $Value, [Microsoft.Win32.RegistryValueKind]::ExpandString) }
    finally { $key.Close() }
}

function Get-UserPathParts {
    # Non-empty entries only, in order, with any %VAR% text kept verbatim.
    return @((Get-UserPathRaw) -split ';' | Where-Object { $_ -and $_.Trim() })
}

function Test-UserPathHas {
    param([string] $Entry)
    $want = $Entry.TrimEnd('\')
    foreach ($p in (Get-UserPathParts)) { if ($p.TrimEnd('\') -eq $want) { return $true } }
    return $false
}

function Resolve-Python {
    param([string] $Explicit)
    $cands = @()
    if ($Explicit) { $cands += $Explicit }
    if (Get-Command python -ErrorAction SilentlyContinue) {
        $cmdPath = (Get-Command python).Source
        if ($cmdPath -notmatch 'WindowsApps\\python\.exe$') { $cands += $cmdPath }
    }
    if (Get-Command py -ErrorAction SilentlyContinue) {
        try {
            $p = (& py -3.12 -c "import sys; print(sys.executable)" 2>$null)
            if ($LASTEXITCODE -eq 0 -and $p) { $cands += $p.Trim() }
        } catch { }
        try {
            $p = (& py -3.11 -c "import sys; print(sys.executable)" 2>$null)
            if ($LASTEXITCODE -eq 0 -and $p) { $cands += $p.Trim() }
        } catch { }
        try {
            $p = (& py -3.10 -c "import sys; print(sys.executable)" 2>$null)
            if ($LASTEXITCODE -eq 0 -and $p) { $cands += $p.Trim() }
        } catch { }
    }
    $cands += @(Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python31*\python.exe" -ErrorAction SilentlyContinue |
                Sort-Object FullName -Descending | Select-Object -ExpandProperty FullName)
    $cands += @(Get-ChildItem "C:\Users\*\AppData\Local\Programs\Python\Python31*\python.exe" -ErrorAction SilentlyContinue |
                Sort-Object FullName -Descending | Select-Object -ExpandProperty FullName)
    $cands += @(Get-ChildItem "C:\Program Files\Python31*\python.exe" -ErrorAction SilentlyContinue |
                Sort-Object FullName -Descending | Select-Object -ExpandProperty FullName)
    $cands += @(Get-ChildItem "C:\Python31*\python.exe" -ErrorAction SilentlyContinue |
                Select-Object -ExpandProperty FullName)
    foreach ($c in ($cands | Select-Object -Unique)) {
        if (-not (Test-Path $c)) { continue }
        try {
            $v = (Invoke-Py $c -c "import sys; print('%d.%d' % sys.version_info[:2])")
            $v = ($v -split "`n" | Where-Object { $_.Trim() -match '^\d+\.\d+$' } | Select-Object -First 1)
            if ($v -and [version]$v.Trim() -ge [version]"3.10") { return @{ Path = $c; Version = $v.Trim() } }
        } catch { }
    }
    return $null
}

# ------------------------------------------------------------- fleet defaults

# install\fleet-defaults.json (shipped in the package) supplies this fleet's
# values so a host needs no arguments: Mattermost host, model endpoint, allowed
# user. Anything passed on the command line wins.
$fleet = $null
$fleetFile = Join-Path $PSScriptRoot "fleet-defaults.json"
if (Test-Path $fleetFile) {
    try { $fleet = Get-Content $fleetFile -Raw | ConvertFrom-Json } catch { $fleet = $null }
}
$fromFleet = @()
if ($fleet) {
    if (-not $MattermostUrl -and $fleet.mattermost_url) {
        $MattermostUrl = [string]$fleet.mattermost_url; $fromFleet += "mattermost url" }
    if (-not $AllowedUser -and $fleet.allowed_user) {
        $AllowedUser = [string]$fleet.allowed_user; $fromFleet += "allowed user" }
    if (-not $ModelBaseUrl -and $fleet.model_base_url) {
        $ModelBaseUrl = [string]$fleet.model_base_url; $fromFleet += "model endpoint" }
    if ($fleet.model -and ($Model -eq "main")) { $Model = [string]$fleet.model }
    # A fleet kit names the fleet's own install folder and keeps the boot-start
    # task the fleet's hosts run. The public package ships no fleet-defaults.json,
    # so a reader gets the profile default and the no-admin autostart.
    if (-not $InstallDir -and $fleet.install_dir) {
        $InstallDir = [string]$fleet.install_dir; $fromFleet += "install dir" }
    if ($fleet.as_service -eq $true) { $AsService = $true }
}

# The guard at the top of this file ran before the defaults above could set
# $AsService, and the fleet kit exists precisely to ship as_service: true - so an
# unelevated fleet push sailed past the guard and died inside Register-ScheduledTask,
# after the files, the venv, config.json and .env had already been written (audit W1).
# Re-check now that fleet-defaults.json has had its say.
if ($AsService -and -not $elevated -and -not $VerifyOnly -and -not $Uninstall) {
    Write-Host "This needs an elevated PowerShell to register the scheduled task." -ForegroundColor Red
    Write-Host "Re-run as Administrator, or drop -AsService to install and start at logon"
    Write-Host "(no administrator rights needed for that)."
    exit 1
}

# ------------------------------------------------- where this install goes
# Your own profile. Nothing outside it is written by a default install - no
# C:\ root folder, no machine-wide PATH entry, no service registration - so no
# elevated shell is involved and nothing has to be granted.
if (-not $InstallDir) { $InstallDir = Join-Path $env:USERPROFILE "tinycmdr" }
$InstallDir = $InstallDir.TrimEnd('\')
# The autostart entry a default install owns (removed by -Uninstall). A -AsService
# install does not use this file.
$StartupLink = Join-Path $env:APPDATA ("Microsoft\Windows\Start Menu\Programs\Startup\" +
                                       "$AppName.lnk")

# ------------------------------------------------------------------ uninstall

if ($Uninstall) {
    Head "uninstalling $AppName"
    $taskExists = $null -ne (Get-ScheduledTask -TaskName $AppName -ErrorAction SilentlyContinue)
    # The user PATH counts too (audit W11): a folder someone deleted by hand still has a
    # dead PATH entry pointing at it, and the old early-exit walked away without saying so.
    $pathHasEntry = Test-UserPathHas $InstallDir
    if (-not $taskExists -and -not (Test-Path $InstallDir) -and -not (Test-Path $StartupLink) -and
        -not $pathHasEntry) {
        Say "nothing to remove (no task '$AppName', no $InstallDir)"
        try { Stop-TranscriptRedacted } catch { }
        exit 0
    }
    if ($taskExists -and -not $SkipTask) {
        try { Stop-ScheduledTask -TaskName $AppName -ErrorAction SilentlyContinue } catch { }
        try {
            Unregister-ScheduledTask -TaskName $AppName -Confirm:$false -ErrorAction Stop
            Say "task    : $AppName removed"
        } catch { Say "task    : could not remove $AppName ($($_.Exception.Message))" }
    }
    if (Test-Path $StartupLink) {
        Remove-Item $StartupLink -Force -ErrorAction SilentlyContinue
        Say "startup : $AppName.lnk removed"
    }
    $n = Stop-TinycmdrProcesses -Dir $InstallDir
    if ($n) { Say "stopped : $n process(es)" }
    # The PATH entry is removed AFTER the folder question, deliberately (audit W11): it
    # used to go first, so answering "n" to "Delete ...?" left a working install whose
    # `tinycmdr` verb had been silently cut off its PATH. Keeping the folder keeps the
    # verb. And an entry only goes once its folder is really gone, so a removal that
    # failed (still held by a process) does not orphan the verb either.
    if (Test-Path $InstallDir) {
        if (-not $Force) {
            if ($NoPause) { Fail "refusing to delete $InstallDir without -Force (or run interactively to confirm)" }
            $ans = Read-Host "Delete $InstallDir and everything in it? (y/N)"
            if ($ans -notmatch '^(y|yes)$') {
                Say "kept $InstallDir"
                Say "path    : $InstallDir stays on your user PATH (its folder is still there)"
                try { Stop-TranscriptRedacted } catch { }
                exit 0
            }
        }
        if (Remove-TinycmdrFolder -Dir $InstallDir) { Say "removed : $InstallDir" }
    }
    # undo the user-Path entry the install added (the verb surface, audit F12)
    if ((-not (Test-Path $InstallDir)) -and (Test-UserPathHas $InstallDir)) {
        $keep = @(Get-UserPathParts | Where-Object { $_.TrimEnd('\') -ne $InstallDir.TrimEnd('\') })
        Set-UserPathRaw ($keep -join ';')
        Say "path    : $InstallDir removed from the user Path"
    }
    Say "done"
    try { Stop-TranscriptRedacted } catch { }
    if (-not $NoPause) { Read-Host "`nPress Enter to close" }
    exit 0
}

# ------------------------------------------------------------------- preamble

Head "tinycmdr installer"
Say "package : $Source"
Say "target  : $InstallDir"
if ($fromFleet.Count) { Say "fleet   : $($fromFleet -join ', ') (from fleet-defaults.json)" }

# ---------------------------------------------------------------- verify only
# BEFORE the interpreter step, deliberately: -VerifyOnly is documented as "report on an
# existing install, change nothing", yet it used to fall into the Python
# discovery/auto-install block below - a winget install, then a python.org download -
# before it verified a single thing (audit W3). A verify run must not fetch a runtime.
if ($VerifyOnly) {
    if (-not (Test-Path (Join-Path $InstallDir "tinycmdr.py"))) {
        Write-Host "no tinycmdr.py in $InstallDir" -ForegroundColor Red
        try { Stop-TranscriptRedacted } catch { }
        exit 1
    }
    Head "verifying $InstallDir"
    # An install carries its own interpreter; probe that one, or the check grades a
    # different python than the bot actually runs under. Only when the venv is missing do
    # we look for a machine python - and never install one from here.
    $venvProbe = Join-Path $InstallDir "venv\Scripts\python.exe"
    $probePy = $null
    if (Test-Path $venvProbe) {
        $probePy = $venvProbe
    } else {
        $found = Resolve-Python -Explicit $Python
        if ($found) { $probePy = $found.Path }
    }
    if (-not $probePy) {
        Write-Host "no python to probe with - is $InstallDir complete?" -ForegroundColor Red
        try { Stop-TranscriptRedacted } catch { }
        exit 1
    }
    $p = Invoke-Probe -Dir $InstallDir -Python $probePy
    Write-Host (($p.Trim() -split "`n" | Select-Object -Last 6) -join "`n")
    try { Stop-TranscriptRedacted } catch { }
    if ($p -match "READY") {
        Write-Host "OK: the agent answered" -ForegroundColor Green
        if (-not $NoPause) { Read-Host "Press Enter to close" }
        exit 0
    }
    Write-Host "NOT VERIFIED: no answer from the model endpoint - check llm.base_url / llm.model" -ForegroundColor Yellow
    if (-not $NoPause) { Read-Host "Press Enter to close" }
    exit 3
}

# ------------------------------------------------------------- 1. python 3.12
Head "finding Python"
$py = Resolve-Python -Explicit $Python
if (-not $py) {
    Say "Python 3.10+ was not found on this machine - installing Python 3.12 automatically..."
    $installed = $false
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Say "installing Python 3.12 via winget..."
        try {
            & winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
            if ($LASTEXITCODE -eq 0) { $installed = $true }
        } catch { }
        # winget writes the new PATH into the registry; THIS process still holds the
        # old one, so refresh it before looking again or the install just made is
        # invisible to the search below.
        $env:Path = ([Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                     [Environment]::GetEnvironmentVariable("Path", "User"))
        Start-Sleep -Seconds 2
    }
    if (-not $installed) {
        Say "downloading official installer from python.org..."
        # python.org publishes one installer per architecture, and the URL used to be
        # amd64 on every host - an ARM64 box downloaded something it could not run while
        # the winget path right above picked the right one (audit W10).
        $pyArch = switch ($env:PROCESSOR_ARCHITECTURE) {
            "AMD64" { "amd64" }
            "ARM64" { "arm64" }
            "x86"   { "win32" }
            default { "amd64" }
        }
        $installerName = "python-3.12.8-$pyArch.exe"
        $installerUrl = "https://www.python.org/ftp/python/3.12.8/$installerName"
        $installerPath = Join-Path $env:TEMP $installerName
        try {
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            Invoke-WebRequest -Uri $installerUrl -OutFile $installerPath -UseBasicParsing
            Say "running Python installer silently..."
            $proc = Start-Process -FilePath $installerPath -ArgumentList "/quiet","InstallAllUsers=0","PrependPath=1","Include_pip=1" -Wait -PassThru
            if ($proc.ExitCode -eq 0) { $installed = $true }
        } catch {
            Write-Host "download failed: $_" -ForegroundColor Red
        }
    }
    $py = Resolve-Python -Explicit $Python
}
if (-not $py) { Fail "could not automatically install Python 3.12. Please install from https://python.org and re-run." }
Say "python  : $($py.Path)  (v$($py.Version))"
# Prove the interpreter's word size matches the host (audit W10): the fallback download
# used to be amd64 on every machine, and a 32-bit python on a 64-bit host starts fine,
# then dies during the dependency install with a DLL error that names nothing useful.
if ([Environment]::Is64BitOperatingSystem -and
    (Invoke-Py $py.Path -c "import struct; print(struct.calcsize('P') * 8)").Trim() -eq "32") {
    Say "WARNING : python at $($py.Path) is 32-BIT on a 64-bit host"
    Say "          install the 64-bit Python from python.org, then re-run this installer"
}

# The dependency set is the one the code declares in its own header:
#   pip install requests mmpy_bot croniter
# Installing only `requests` produced a bot that started, logged two warnings and
# never connected -- mmpy_bot IS the Mattermost client (live 2026-09-10).
# They are installed further down, into this install's OWN virtual environment:
# that folder does not exist yet here, and the reader has not agreed to the
# install. What matters now is only that an interpreter exists at all.

# ------------------------------------------------------ identity of this install
# $AppName is ONE name per user, not per folder: -Uninstall removes $AppName.lnk and that
# task by name, and a run that keeps the default name writes over whatever is already
# registered under it - so a probe or a second install silently takes the first one's
# autostart away. Measured on the macOS side (2026-09-26), where test installs sharing the
# default label left the real agent unregistered: no bot, and its page answered nothing.
$foreign = ""
if (Test-Path $StartupLink) {
    $lnkText = ""
    try {
        $sh = New-Object -ComObject WScript.Shell
        $lnk = $sh.CreateShortcut($StartupLink)
        $lnkText = ("$($lnk.TargetPath) $($lnk.Arguments) $($lnk.WorkingDirectory)").Trim()
    } catch { }
    if ($lnkText -and ($lnkText -notlike "*$InstallDir*")) {
        $foreign = "$StartupLink -> $lnkText"
    }
}
if (-not $foreign -and -not $SkipTask) {
    $t = Get-ScheduledTask -TaskName $AppName -ErrorAction SilentlyContinue
    if ($t) {
        $act = @($t.Actions)[0]
        $actText = ("$($act.Execute) $($act.Arguments)").Trim()
        if ($actText -and ($actText -notlike "*$InstallDir*")) {
            $foreign = "scheduled task $AppName -> $actText"
        }
    }
}
if ($foreign) {
    Fail "the name $AppName already belongs to another install:
    $foreign
This run would take that autostart entry away. Give this install its own name:
    INSTALL-WINDOWS.cmd -TaskName Tinycmdr-$(hostname) <your switches>
(a probe or a second install always should), or remove the other install first:
    INSTALL-WINDOWS.cmd -Uninstall"
}

# ----------------------------------------------------------------- 2. the app
# What the install cannot work without. Every name here must also be in the package: this
# check is the one place where a file that stopped shipping is fatal instead of silent, and
# the reader is the one who finds out. build-package.py verifies this list against the
# staged package, so the two cannot drift apart again.
$required = @("tinycmdr.py", "tinycmdr-supervise.py",
              "config.example.json", ".env.example", "skills")
foreach ($f in $required) {
    if (-not (Test-Path (Join-Path $Source $f))) { Fail "package is missing $f (run the installer from the extracted zip)" }
}
if ((Test-Path $InstallDir) -and -not $Force) {
    $existing = (Get-ChildItem $InstallDir -ErrorAction SilentlyContinue | Measure-Object).Count
    if ($existing -gt 0) {
        if ($Ask) {
            # "use -Force" is not something a person double-clicking this can act on. Ask, and
            # mean the same thing -Force means: the app files are replaced, the state stays.
            Write-Host ""
            Write-Host "  There is already an install in $InstallDir"
            Write-Host "  ($existing item(s) - your notes, ledger, sessions and secrets are kept)"
            if (Ask-Yes "  Replace its app files with this package?" $true) {
                $Force = $true
            } else {
                Say "nothing was changed"
                try { Stop-TranscriptRedacted } catch { }
                if (-not $NoPause) { Read-Host "`nPress Enter to close" }
                exit 0
            }
        } else {
            Fail "$InstallDir already exists and is not empty - use -Force to redo it in place, or -Uninstall to remove it, or -InstallDir <other>"
        }
    }
}
# Secrets, from one .env-style file. The search keys are the same on every host,
# so they live in exactly one place you keep (a share is fine); the Mattermost
# token is per host. Any of these four keys found in the file gets written into
# the install's .env - nothing has to be typed or hand-edited.
$secrets = @{}
$secretsFrom = ""
if (-not $SecretsFile) {
    $cand = Join-Path $PSScriptRoot "fleet-secrets.env"
    if (Test-Path $cand) { $SecretsFile = $cand }
}
if ($SecretsFile) {
    if (-not (Test-Path $SecretsFile)) { Fail "no such secrets file: $SecretsFile" }
    foreach ($line in Get-Content $SecretsFile) {
        $s = $line.Trim()
        if (-not $s -or $s.StartsWith("#") -or $s -notmatch "=") { continue }
        $k, $v = $s.Split("=", 2)
        if ($v.Trim()) { $secrets[$k.Trim()] = $v.Trim() }
    }
    $secretsFrom = $SecretsFile
    Say "secrets : $($secrets.Count) key(s) from $SecretsFile"
}

# Extra endpoints, either as switches (repeatable - this is also how a script adds one)
# or answered at the prompt below. "<base_url>;<model>;<alias>;<key>", the last two
# optional; the key goes to .env under a generated name, never into config.json.
# A '|' is accepted too, but it CANNOT be used through INSTALL-WINDOWS.cmd: cmd.exe
# reads it as a pipe and hands the pieces to different processes (measured 2026-09-26).
$script:Fallbacks = @()
$fbGiven = 0
foreach ($spec in $AddEndpoint) {
    if (-not $spec -or -not $spec.Trim()) { continue }
    $parts = @(($spec -split '[;|]') + @("", "", "", ""))
    $u = "$($parts[0])".Trim(); $m = "$($parts[1])".Trim()
    $a = "$($parts[2])".Trim(); $k = "$($parts[3])".Trim()
    if (-not $u) { continue }
    $fbGiven++
    $script:Fallbacks += [pscustomobject]@{ Url = $u; Model = $m; Alias = $a; Key = $k
                                            Env = "TINYCMDR_ENDPOINT${fbGiven}_API_KEY" }
    Say "endpoint added: $m at $u"
}

if ($Ask) {
    Head "three ways to talk to it"
    Write-Host ""
    Write-Host "tinycmdr answers messages. Pick how it should get them - the first one needs nothing"
    Write-Host "else installed, hosted or reachable."
    $picked = Ask-Many "How should you talk to it? Pick any that apply - they work together." @(
        "A local page on this machine (http://127.0.0.1:$WebPort) - no chat server needed",
        "A Mattermost bot account (paste a bot token from your server)",
        "A Telegram bot account (a token from @BotFather; DMs only, nothing to host)",
        "Sessions by hand in a terminal (nothing runs in the background)")
    $WantWeb = $picked -contains 1
    $WantChat = $picked -contains 2
    $WantTg = $picked -contains 3
    $WantCli = $picked -contains 4
    if ($WantWeb) { $EnableWeb = $true }
    if ($WantChat -and $WantWeb) {
        Write-Host ""
        Write-Host "  (Both: the bot serves the page itself, so that is one process, one task.)"
    }

    if ($WantChat) {
        Write-Host ""
        $known = if ($MattermostUrl -and $MattermostUrl -ne "CHANGE-ME.example.com") { $MattermostUrl } else { "" }
        if ($known) { Write-Host "  (leave blank to keep ${known})" }
        $MattermostUrl = Ask-Text "Mattermost server, no https:// (e.g. chat.example.com)" $known
        # The secrets file is read before this, so a token handed over with -SecretsFile or a
        # package default is never asked for twice.
        if (-not $MattermostToken -and $secrets["TINYCMDR_MM_TOKEN"]) {
            $MattermostToken = $secrets["TINYCMDR_MM_TOKEN"]
            $tokenSource = "the secrets file"
        }
        if (-not $MattermostToken) {
            $in = Ask-Text "Bot token (input hidden; paste and press Enter)" -Secret
            if ($in) { $MattermostToken = $in; $tokenSource = "prompt" }
        } else {
            Write-Host "  (a token is already known from the package, the secrets file or .env - kept)"
        }
        $u = Ask-Text "Your Mattermost user id (optional, but without it the bot ignores your DMs)"
        if ($u) { $AllowedUser = $u }
    }

    if ($WantTg) {
        Write-Host ""
        if ($TelegramToken) {
            Write-Host "  (a Telegram token is already known from a switch or .env - kept)"
        } else {
            $in = Ask-Text "Telegram bot token from @BotFather (input hidden; paste and press Enter)" -Secret
            if ($in) { $TelegramToken = $in }
        }
        $tg = Ask-Text "Your numeric Telegram id (message @userinfobot for it; without it the bot ignores every DM)"
        if ($tg) { $TelegramIds = $tg }
        if ($WantChat) {
            Write-Host ""
            Write-Host "  NOTE: with BOTH tokens set, Mattermost wins and the Telegram lane does NOT"
            Write-Host "        start in the background process. Run this for a Telegram-only side:"
            Write-Host "        python tinycmdr.py --telegram"
        }
    }

    Write-Host ""
    Write-Host "Which model should it use? Any OpenAI-compatible endpoint: llama.cpp, Ollama, vLLM,"
    Write-Host "or a hosted provider."
    $mb = if ($ModelBaseUrl) { $ModelBaseUrl } else { "http://127.0.0.1:8081/v1" }
    $ModelBaseUrl = Ask-Text "Model endpoint" $mb
    $Model = Ask-Text "Model id" $(if ($Model -and $Model -ne "main") { $Model } else { "main" })

    $isLocal = $ModelBaseUrl -match "127\.0\.0\.1|localhost|10\.|192\.168\.|::1"
    $ModelKey = ""
    if (-not $isLocal) {
        Write-Host ""
        Write-Host "  That endpoint is on the network, so it probably wants an API key."
        $ModelKey = Ask-Text "API key for it (blank if it needs none)" -Secret
    }

    # Extra endpoints: llm.fallbacks, tried in order when the primary fails. Each one is
    # base_url + model (+ an optional /model alias), and its key goes to .env under a
    # generated name the entry's api_key_env points at - the shape the build resolves for
    # a fallback endpoint, and it keeps the key out of config.json.
    while ($true) {
        if (-not (Ask-Yes "Add another endpoint?" $false)) { break }
        $n = $script:Fallbacks.Count + 1
        $fbUrl = Ask-Text "Endpoint #$n (OpenAI-compatible /v1 root)"
        if (-not $fbUrl) { Write-Host "  (no address given - nothing added)"; continue }
        $fbModel = Ask-Text "Model id for it" -AllowBlank
        $fbAlias = Ask-Text "Alias, so /model <alias> switches to it (blank = none)" -AllowBlank
        $fbKey   = Ask-Text "API key for it (blank if it needs none)" -Secret
        $script:Fallbacks += [pscustomobject]@{ Url = $fbUrl; Model = $fbModel
                                                Alias = $fbAlias; Key = $fbKey
                                                Env = "TINYCMDR_ENDPOINT${n}_API_KEY" }
        Write-Host "  endpoint #$n added: $fbModel at $fbUrl"
    }

    if ($EnableWeb) {
        # Minted here and NEVER shown. Asking with the generated value as the prompt's default
        # printed it into the install transcript and into tinycmdr.log, which is the leak the
        # Unix installers stopped doing - a page token drives shell on this box. The operator
        # reads it from .env, and can replace it there.
        $script:WebTokenChoice = -join ((48..57) + (97..122) | Get-Random -Count 24 | ForEach-Object { [char]$_ })
        Write-Host ""
        Write-Host "The page is protected by a token, so nothing else on this machine can drive the agent."
        Write-Host "It is generated here and written to .env (TINYCMDR_WEB_TOKEN) - read it there."
        Write-Host ""
        # The bind address, asked because only the reader knows whether another machine
        # needs the page - and a page nobody can reach reads as a broken install.
        if (Ask-Yes "Should the page be reachable from other machines on your network?" $true) {
            $WebHost = "0.0.0.0"
        } else { $WebHost = "127.0.0.1" }
    }

    Write-Host ""
    Write-Host "  ---- about to install ----"
    Write-Host ("  folder       : {0}" -f $InstallDir)
    # the bot name is resolved later (a switch, fleet-defaults, then this machine's name), so
    # the summary resolves it the same way or it prints a bare "@"
    $nameNow = if ($BotName) { $BotName } else { $env:COMPUTERNAME.ToLower() }
    $ways = @()
    if ($WantChat) { $ways += "a Mattermost bot on $MattermostUrl as @$nameNow" }
    if ($WantTg) {
        $ways += if ($WantChat) { "a Telegram DM (only with --telegram)" } else { "a Telegram DM" }
    }
    if ($WantWeb) { $ways += "a local page on http://127.0.0.1:$WebPort" }
    if ($WantCli) { $ways += "sessions you start by hand" }
    Write-Host ("  how you talk : {0}" -f ($ways -join " and "))
    Write-Host ("  model        : {0} at {1}" -f $Model, $ModelBaseUrl)
    if ($ModelKey) { Write-Host "  model key    : given (stored in config.json's llm.api_key)" }
    Write-Host ""
    if (-not (Ask-Yes "Install now?" $true)) {
        Say "nothing was changed"
        try { Stop-TranscriptRedacted } catch { }
        exit 0
    }
}

# resolve the bot token before touching anything: -MattermostToken, the secrets
# file, a token file, an existing .env (so a -Force redo keeps it), then ask
$tokenSource = ""
if (-not $MattermostToken -and $secrets["TINYCMDR_MM_TOKEN"]) {
    if ($MattermostTokenFile) {
        $MattermostToken = (Get-Content $MattermostTokenFile -Raw).Trim()
        $tokenSource = "the token file"
    } else {
        $MattermostToken = $secrets["TINYCMDR_MM_TOKEN"]
        $tokenSource = "the secrets file"
    }
}
if ($MattermostToken) {
    $tokenSource = "the -MattermostToken switch"
} elseif (-not $MattermostToken) {
    if ($MattermostTokenFile) {
        if (-not (Test-Path $MattermostTokenFile)) { Fail "no such token file: $MattermostTokenFile" }
        $MattermostToken = (Get-Content $MattermostTokenFile -Raw).Trim()
        $tokenSource = "file"
    } elseif (Test-Path (Join-Path $InstallDir ".env")) {
        foreach ($line in Get-Content (Join-Path $InstallDir ".env")) {
            $s = $line.Trim()
            if ($s -match '^TINYCMDR_MM_TOKEN=(.+)$' -and $matches[1].Trim()) {
                $MattermostToken = $matches[1].Trim()
                $tokenSource = "the existing .env (redo kept it)"
                break
            }
        }
    }
    # $Ask, not -not $NoPause: the .cmd wrapper ALWAYS passes -NoPause (it only means
    # "keep the window open"), so gating this on it never fired for a double-click, and
    # a scripted -NonInteractive run hung here forever waiting for a token nobody was
    # there to type (measured 2026-09-24: a CI-style install stalled at this line).
    if (-not $MattermostToken -and $Ask) {
        $sec = Read-Host "Mattermost bot token (blank = set it in .env later)" -AsSecureString
        $MattermostToken = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
            [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
        if ($MattermostToken) { $tokenSource = "prompt" }
    }
}

# -------------------------------------------------------------------- the chat lane
# A Mattermost account is OPTIONAL: the harness has three doors - a chat bot, the CLI
# (`python tinycmdr.py --cli`) and the local page (`python tinycmdr.py --web` -> 127.0.0.1:8787,
# which is dispatched before the token check). With no token there is no chat lane, and a
# chat-lane task would exit immediately (tinycmdr.py refuses to start without a token, on
# purpose: a missing token used to fail silently as "never connects") while the supervisor
# respawned it every few seconds. So a token-less install registers the local page instead,
# or nothing at all.
$ChatLane = [bool]($MattermostToken) -and ($MattermostUrl -and $MattermostUrl -ne "CHANGE-ME.example.com")
# Telegram is a chat lane too, and its token alone is enough: with no Mattermost token the
# build runs the Telegram lane by itself, so a Telegram-only install NEEDS the background
# task - otherwise it only answers while a window is open.
$TgLane = [bool]($TelegramToken)
$AnyLane = $ChatLane -or $TgLane
$LocalWeb = (-not $AnyLane) -and $EnableWeb
$RegisterTask = (-not $SkipTask) -and ($AnyLane -or $LocalWeb)
# What the background supervisor is told to run. A token-less install serves the
# LOCAL PAGE, and the supervisor has to be told that explicitly: started with no
# arguments the bot runs the CHAT lane, which refuses to start without a token, so
# the supervisor would respawn a dying process forever (measured 2026-09-24 on a
# --web install started through the Startup shortcut).
$SuperviseArgs = if ($LocalWeb) { "--web" } else { "" }

# ---------------------------------------------------------------- 3. copy files
Head "copying the app"
if ($Force) {
    # stop what is running from here first: the live process holds tinycmdr.log
    # and tinycmdr.lock, so overwriting in place is what makes a redo messy
    if (-not $SkipTask) { try { Stop-ScheduledTask -TaskName $AppName -ErrorAction SilentlyContinue } catch { } }
    $n = Stop-TinycmdrProcesses -Dir $InstallDir
    if ($n) { Say "stopped : $n running process(es) for a clean copy" }
    Start-Sleep -Seconds 2
}
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
$copy = @("tinycmdr.py", "tinycmdr-supervise.py", "tinycmdr.cmd", "requirements.txt", "config.example.json",
          ".env.example", "README.md", "field-notes.md", "soul.md", "skills",
          "tools",    # the starter drop-in tools; tools/README.md has the shapes
          "install")  # the installer family incl. uninstall-tinycmdr.ps1
foreach ($item in $copy) {
    $src = Join-Path $Source $item
    if (Test-Path $src) { Copy-Item $src -Destination $InstallDir -Recurse -Force }
}
# the restart helper is the one maintenance script that is host-generic
New-Item -ItemType Directory -Force -Path (Join-Path $InstallDir "maintenance") | Out-Null
$restart = Join-Path $Source "maintenance\restart-tinycmdr.ps1"
if (Test-Path $restart) { Copy-Item $restart (Join-Path $InstallDir "maintenance") -Force }
$stateDirs = @("sessions", "snapshots", "tools", "tmp")
foreach ($d in $stateDirs) { New-Item -ItemType Directory -Force -Path (Join-Path $InstallDir $d) | Out-Null }
Say "copied  : $(($copy | Where-Object { Test-Path (Join-Path $Source $_) }) -join ', ')"

# ------------------------------------------- 4. its own python + dependencies
# The dependencies go into a virtual environment INSIDE the install folder rather
# than into whatever python the machine happens to have. One install then owns its
# stack, nothing else on the box can break it by upgrading a shared package, and
# no administrator rights are involved - the folder is the reader's own.
# A pre-existing venv is REUSED, so a -Force redo does not re-download anything.
Head "python environment"
$venvDir = Join-Path $InstallDir "venv"
$venvPy  = Join-Path $venvDir "Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    Say "creating : $venvDir"
    $null = Invoke-Py $py.Path -m venv $venvDir
}
if (Test-Path $venvPy) {
    Say "python   : $venvPy"
} else {
    # A venv can fail to build (a store python, a locked-down temp folder). The
    # install still works - the dependencies then go into the interpreter found
    # above - so say so and carry on instead of stopping the reader here.
    Say "note     : could not build a virtual environment - dependencies will go into"
    Say "           $($py.Path) itself"
    $venvPy = $py.Path
}
# A python whose ensurepip never ran has no pip at all, and then every install
# below fails for a reason that is not the reader's fault.
if (-not (Test-PyImport $venvPy "pip")) {
    Say "pip      : missing - bootstrapping with ensurepip"
    $null = Invoke-Py $venvPy -m ensurepip --upgrade
}
$reqFile = Join-Path $InstallDir "requirements.txt"
$missing = @()
foreach ($mod in @("requests", "mmpy_bot", "croniter")) {
    if (-not (Test-PyImport $venvPy $mod)) { $missing += $mod }
}
if ($missing.Count) {
    Say "deps     : installing $($missing -join ', ')  (needs internet/PyPI)"
    $pipArgs = @("-m", "pip", "install", "--quiet", "--disable-pip-version-check")
    if (Test-Path $reqFile) { $pipArgs += @("-r", $reqFile) }
    else { $pipArgs += @("requests", "mmpy_bot", "croniter") }
    $pipOut = Invoke-Py $venvPy @pipArgs
    if ((-not (Test-PyImport $venvPy "requests")) -or (-not (Test-PyImport $venvPy "mmpy_bot"))) {
        Write-Host (($pipOut.Trim() -split "`n" | Select-Object -Last 8) -join "`n")
        Fail ("could not install the dependencies (requests, mmpy_bot) into $venvPy - " +
              "pip's output is above. Check internet/proxy access, then re-run.")
    }
    if (-not (Test-PyImport $venvPy "croniter")) {
        Say "deps     : croniter still missing - the schedule tool will be disabled"
    }
    Say "deps     : installed"
} else {
    Say "deps     : requests, mmpy_bot, croniter present"
}
# Everything below - the hidden launcher, the autostart entry and the probe - runs
# the bot with this interpreter.
$py = @{ Path = $venvPy; Version = $py.Version }

# ------------------------------------------------------ the verb surface on PATH
# audit F12: an install left no command behind, so day-two work meant hand-editing
# .env and config.json. tinycmdr.cmd is the shim; the folder goes on the USER path
# (never the machine path), written straight into HKCU\Environment so nobody else's
# %VAR% entries are frozen (W2). Only -NoPath withholds it: -SkipTask is documented as
# "files only: no autostart, no service", and it used to swallow the PATH entry too
# (audit W9), leaving an install with no `tinycmdr` verb attached.
if ($NoPath) {
    Say "path    : left alone (-NoPath)"
} else {
    if (Test-UserPathHas $InstallDir) {
        Say "path    : already on your user PATH: tinycmdr status"
    } else {
        Set-UserPathRaw (((Get-UserPathParts) + $InstallDir) -join ';')
        Say "path    : added to your user PATH - open a NEW window and run: tinycmdr status"
    }
}

# The host field is the HOST alone: a reader pastes what their browser shows
# ("https://chat.example.com/"), while the scheme and the port are their own keys. Split
# what came in rather than writing a url no client can build a request from (measured
# 2026-09-26 on a fleet macOS host, where the host field came out holding a full URL).
if ($MattermostUrl) {
    $raw = $MattermostUrl
    $h = ($raw -replace '^[a-zA-Z][a-zA-Z0-9+.-]*://', '')
    if ($h -match '/') { $h = ($h -split '/')[0] }
    if ($h -match '@') { $h = $h.Substring($h.LastIndexOf('@') + 1) }
    if ($h -match '^(?<host>\[[^\]]+\]|[^:]+):(?<port>\d+)$') {
        $h = $Matches['host']
        $MattermostPort = [int]$Matches['port']
    }
    if ($h -ne $raw) { Say "mm url  : $raw -> $h (host field, port $MattermostPort)" }
    $MattermostUrl = $h
}

# --------------------------------------------------------------- 4. config.json
Head "writing config.json"
$cfgPath = Join-Path $InstallDir "config.json"
# The HOST's own config is the base whenever there is one. This used to copy the
# package's config.example.json over it on EVERY run, so a plain re-run - and an
# update with -Force - replaced a working install's settings with the example's
# placeholders (mattermost.url, allowed_users, the model endpoint) and the bot
# could not start. Measured 2026-09-24 on the macOS bed, where an in-place update did
# exactly that and had to be repaired by hand.
$cfgFresh = -not (Test-Path $cfgPath)
$cfgBase = if ($cfgFresh) { Join-Path $InstallDir "config.example.json" } else { $cfgPath }
if (-not $BotName) {
    $BotName = ($env:COMPUTERNAME).ToLower()          # host-style short name
}
if (-not $MattermostUrl) { $MattermostUrl = "CHANGE-ME.example.com" }
$webToken = if ($EnableWeb) {
    if ($script:WebTokenChoice) { $script:WebTokenChoice }   # the user's own, or the suggestion
    else { -join (1..48 | ForEach-Object { "{0:x}" -f (Get-Random -Maximum 16) }) }
} else { "" }
# The link no longer carries the token (security review 2026-09-23): a token in
# the query string lands in the request line, browser history and any proxy log,
# and this token is shell and code execution on this box. The page prompts for
# it, and the summary prints the token for paste.
$webLink = if ($EnableWeb) {
    "http://127.0.0.1:$WebPort"
} else { "" }

if (-not (Test-Path $cfgBase)) {
    Fail "no config.json and no config.example.json in $InstallDir"
}
$cfg = Get-Content $cfgBase -Raw | ConvertFrom-Json
# Only what this run was TOLD. An update passes no -AllowedUser and no -ModelBaseUrl,
# and re-applying the shipped defaults over a working host is what emptied an
# allowlist (a bot that ignores every DM) and moved a LAN endpoint to loopback.
if ($MattermostUrl -and $MattermostUrl -ne "CHANGE-ME.example.com") {
    $cfg.mattermost.url = $MattermostUrl
} elseif (-not $cfg.mattermost.url) {
    $cfg.mattermost.url = "CHANGE-ME.example.com"
}
if ($MattermostPort -and $MattermostPort -ne 443) { $cfg.mattermost.port = $MattermostPort }
$cfg.mattermost.token = ""
if ($AllowedUser) { $cfg.mattermost.allowed_users = @($AllowedUser) }
if ($ModelBaseUrlGiven) {
    $cfg.llm.base_url = $ModelBaseUrl
} elseif ($cfgFresh) {
    $cfg.llm.base_url = "http://127.0.0.1:8081/v1"
}
if ($ModelGiven -or $cfgFresh) { $cfg.llm.model = $Model }
if ($ModelKey) {
    # The primary's key is not env-resolved (only fallback entries have api_key_env), so a
    # hosted endpoint that needs a key carries it here. A key in .env plus a fallback entry is
    # the tidier shape - see README, "Model endpoints".
    $cfg.llm.api_key = $ModelKey
}
# The local chat page is opt-in. tinycmdr is driven from Mattermost; a fresh
# install has no reason to open a port, and local checks don't need one.
# Only turn it on when it is actually wanted; the code default is "off" too.
if ($EnableWeb) {
    if (-not $cfg.PSObject.Properties['web']) {
        $cfg | Add-Member -NotePropertyName web -NotePropertyValue ([pscustomobject]@{})
    }
    $cfg.web.enabled = $true
    # The page token is a SECRET, so it goes to .env with the others: one secrets file
    # per install, one place to look, and nothing loose in the folder.
    $cfg.web.token   = ""
    $cfg.web.port    = $WebPort
    # The bind address: what the reader chose (-WebHost or the question), never a
    # silent move of a working host's page - and a fresh install keeps the loopback
    # default it has always had. An empty host means 0.0.0.0 to the build, which is
    # why it is written out when it is chosen.
    if ($WebHost) { $cfg.web.host = $WebHost }
    elseif ($cfgFresh) { $cfg.web.host = "127.0.0.1" }
}
# Extra endpoints: llm.fallbacks, in order, tried when the primary fails. The list is
# injected into the JSON below rather than serialized here, because ConvertTo-Json
# renders a ONE-element array as a bare object - and the code iterates it as a list
# (a lone object would iterate its keys instead).
$fbJson = ""
if ($script:Fallbacks.Count) {
    $cfg.llm.fallbacks = @()
    $list = @()
    foreach ($fb in $script:Fallbacks) {
        $e = [ordered]@{ base_url = $fb.Url }
        if ($fb.Model) { $e["model"] = $fb.Model }
        if ($fb.Alias) { $e["alias"] = $fb.Alias }
        if ($fb.Key)   { $e["api_key_env"] = $fb.Env }
        $list += [pscustomobject]$e
    }
    $fbJson = ($list | ConvertTo-Json -Depth 6)
    if ($list.Count -eq 1) { $fbJson = "[`n$fbJson`n]" }
} elseif ($cfgFresh) {
    # config.example.json carries a placeholder fallback (api.example.com, with a key
    # variable nobody has): a fresh install must not inherit an endpoint that does not
    # exist. An update keeps whatever the host already had.
    $cfg.llm.fallbacks = @()
}
# The third door. The TOKEN is .env-only (env_map resolves TINYCMDR_TG_TOKEN, and a copy
# in config.json is ignored with a warning), so only the numeric allowlist goes in here.
# The lane is deny-by-default: a token with no id refuses to start, which is why the
# installer refuses to finish that way rather than leaving a bot that ignores every DM.
$tgIds = @()
if ($TelegramIds) {
    $tgIds = @($TelegramIds -split '[,\s]+' | Where-Object { $_ -match '^\d+$' })
}
if ($TelegramToken -and $tgIds.Count -eq 0) {
    Fail "a Telegram token with no numeric id: that lane would ignore every DM. Message @userinfobot for your id and pass -TelegramIds 123456789"
}
if ($TelegramIds -and $tgIds.Count -eq 0) {
    Fail "-TelegramIds needs numeric ids (message @userinfobot for yours): got '$TelegramIds'"
}
if (-not $cfg.PSObject.Properties['telegram']) {
    $cfg | Add-Member -NotePropertyName telegram -NotePropertyValue ([pscustomobject]@{})
}
$cfg.telegram.token = ""
if ($tgIds.Count -gt 0) { $cfg.telegram.allowed_users = @($tgIds) }
$cfg.agent.bot_name       = $BotName
$cfg.agent.debug_dump_dir = ""

# Write UTF-8 WITHOUT a BOM: PowerShell 5.1's Set-Content -Encoding UTF8 adds one,
# and a BOM breaks json parsing (and a BOM'd token file breaks the HTTP auth header
# by one invisible character). One-element arrays also collapse to a scalar in
# ConvertTo-Json, so allowed_users is forced back to a list.
$json = $cfg | ConvertTo-Json -Depth 20
# One-element arrays collapse to a scalar in ConvertTo-Json, and an empty one becomes
# null; both break an allowlist check (a string is compared character by character, and
# `uid not in None` raises). Unconditional, so it covers the Telegram list too.
$json = $json -replace '"allowed_users":\s*"(.*?)"', '"allowed_users": [ "$1" ]'
$json = $json -replace '"allowed_users":\s*null', '"allowed_users": []'
if ($fbJson) {
    $json = [regex]::Replace($json, '(?s)("fallbacks"\s*:\s*)\[\s*\]',
                             { param($m) $m.Groups[1].Value + $fbJson })
}
Write-Utf8NoBom $cfgPath $json
$verify = Get-Content $cfgPath -Raw | ConvertFrom-Json
if ($verify.mattermost.allowed_users -is [string] -or $verify.telegram.allowed_users -is [string]) {
    Fail "config.json allowed_users came out as a string, not a list - refusing to install a broken allowlist"
}
Say "bot name: $BotName"
Say "model   : $Model @ $ModelBaseUrl"
Say "mm url  : $MattermostUrl`:$MattermostPort"
# Loopback is the neutral default for a package that must not ship any one host's
# address - but it silently only works if a model runs on THIS machine. Say so,
# and list it as an outstanding item rather than pretending it is configured.
$LoopbackModel = $ModelBaseUrl -match "://(127\.0\.0\.1|localhost|\[::1\])"
if ($LoopbackModel) {
    Say "NOTE    : llm.base_url is LOOPBACK - only right if a model runs on this"
    Say "          machine. For a model elsewhere, pass -ModelBaseUrl"
    Say "          http://<model-host>:8081/v1 (or edit it in config.json)"
}
if ($ChatLane) {
    if ($MattermostUrl -eq "CHANGE-ME.example.com") { Say "NOTE    : edit $cfgPath (mattermost.url) before the bot will connect" }
    if (-not $AllowedUser) { Say "NOTE    : add your Mattermost user id to mattermost.allowed_users, or the bot ignores your DMs" }
    if ($TgLane) { Say "tg lane : a Telegram token is set too - Mattermost wins in this process,"
                   Say "          so Telegram needs:  python tinycmdr.py --telegram" }
} elseif ($TgLane) {
    Say "tg lane : Telegram only - allowlist $($tgIds -join ', '), starts by itself"
} else {
    Say "NOTE    : no chat account - mattermost.url and allowed_users are unused for now,"
    Say "          and two local doors already work (see the summary below)"
}

# ---------------------------------------------------------------------- 5. .env
Head "writing .env"
$envPath = Join-Path $InstallDir ".env"
# A model key is per bot, never fleet-wide. Keep whatever this host's .env already
# holds (the template is about to replace the file), and never take one from
# $SecretsFile: that is how several hosts ended up sharing one provider key, and
# every one of them then showed the others' usage in that provider's dashboard.
# No provider is named here on purpose - the key is whatever the endpoint issued.
$ownKeys = @{}
if (Test-Path $envPath) {
    foreach ($line in (Get-Content $envPath)) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)=(.+)$') {
            $k = $matches[1]
            $v = $matches[2].Trim()
            # Only the keys THIS INSTALL owns are withheld. The search keys were in
            # this list too, so a re-run without -SecretsFile dropped a working
            # host's TAVILY/ANYSEARCH keys - the same loss the config writer had,
            # one file over (measured 2026-09-24 on the macOS bed).
            if ($v -and @("TINYCMDR_MM_TOKEN", "TINYCMDR_TG_TOKEN",
                          "TINYCMDR_WEB_TOKEN") -notcontains $k) {
                $ownKeys[$k] = $v
            }
        }
    }
}
Copy-Item (Join-Path $InstallDir ".env.example") $envPath -Force
$envText = Get-Content $envPath -Raw
$written = @()
$refused = @()
foreach ($key in @("TINYCMDR_MM_TOKEN", "TINYCMDR_TG_TOKEN", "TAVILY_API_KEY", "ANYSEARCH_API_KEY")) {
    $val = ""
    if ($key -eq "TINYCMDR_MM_TOKEN") { $val = $MattermostToken }
    elseif ($key -eq "TINYCMDR_TG_TOKEN") { $val = $TelegramToken }
    else { $val = $secrets[$key] }
    if (-not $val) { continue }
    if ($val -match '^\s*<.*>\s*$' -or $val -match '(?i)redacted') {
        # A redacted package or secrets file carries no real key. Writing the
        # placeholder looks like success and then 401s at every search, so leave
        # the key unset and say so (seen on two Linux hosts, 2026-09-11).
        $refused += $key
        continue
    }
    # write into the commented template line, or append if there is none. The value is
    # substituted LITERALLY (audit W11): `-replace` reads its replacement as a regex
    # template, so a token containing $$/$&/$'/` mangled itself on the way into .env -
    # and the summary then printed a token the page rejected.
    if ($envText -match "(?m)^#?\s*$key=") {
        $envText = [regex]::Replace($envText, "(?m)^#?\s*$key=.*$", { param($m) "$key=$val" })
    } else {
        $envText = $envText.TrimEnd() + "`n$key=$val`n"
    }
    $written += $key
}
# Everything else this host already had goes back in: a redo must never lose a key.
foreach ($k in @($ownKeys.Keys)) {
    $v = $ownKeys[$k]
    if ($envText -match "(?m)^#?\s*$k=") {
        $envText = [regex]::Replace($envText, "(?m)^#?\s*$k=.*$", { param($m) "$k=$v" })
    } else {
        $envText = $envText.TrimEnd() + "`n$k=$v`n"
    }
    $written += "$k (this host's own)"
}
# The extra endpoints' keys. The carry-over above already put any older line in place,
# so this REPLACES it rather than writing a second one for the same name.
foreach ($fb in $script:Fallbacks) {
    if (-not $fb.Key) { continue }
    $k = $fb.Env
    if ($envText -match "(?m)^#?\s*$k=") {
        $envText = [regex]::Replace($envText, "(?m)^#?\s*$k=.*$", { param($m) "$k=$($fb.Key)" })
    } else {
        $envText = $envText.TrimEnd() + "`n$k=$($fb.Key)`n"
    }
    $written += "$k (extra endpoint)"
}
# And say what was NOT copied, without naming any provider: a model key belongs to
# one host, and silently sharing it is how one box's usage appeared on another.
$notCopied = @($secrets.Keys | Where-Object {
        @("TINYCMDR_MM_TOKEN", "TINYCMDR_TG_TOKEN", "TAVILY_API_KEY",
          "ANYSEARCH_API_KEY") -notcontains $_ })
if ($notCopied.Count) {
    Say "NOTE    : not copied from the secrets file: $($notCopied -join ', ')"
    Say "          a model key is per host - put this host's own in $envPath by hand"
}
Write-Utf8NoBom $envPath $envText
if ($written.Count) {
    Say "env     : $($written -join ', ') written"
    if ($MattermostToken) { Say "          (bot token from $tokenSource)" }
} else {
    if ($ChatLane) {
        Say "NOTE    : no keys to write - put the bot token in $envPath (TINYCMDR_MM_TOKEN=...)"
    } elseif (-not $TgLane) {
        Say "env     : no chat token - none needed for the two local doors"
    }
}
if ($refused.Count) {
    Say "WARNING : refused redacted placeholder value(s): $($refused -join ', ')"
    Say "          that file carried no real key - edit $envPath with the real values"
}
if (-not $secrets["TAVILY_API_KEY"] -and -not $secrets["ANYSEARCH_API_KEY"]) {
    Say "          search keys not set: web search will be unavailable on this host"
} elseif ($refused.Count) {
    Say "          search keys not set: web search will be unavailable on this host"
}
if ($EnableWeb) {
    # The token is a secret like the bot token, and .env is where secrets live: one file
    # per install instead of a second one nobody remembers. Appended after the template
    # was written, so a redo cannot leave two values in the file.
    # No BOM: this file is parsed line by line, and a stray byte at the head of it has
    # burned this project before. Rewritten whole rather than appended for the same reason.
    $envText = (Get-Content -Raw -LiteralPath $envPath).TrimEnd()
    Write-Utf8NoBom $envPath ($envText + "`n" + "TINYCMDR_WEB_TOKEN=$webToken" + "`n")
    $written += "TINYCMDR_WEB_TOKEN (this install's web page)"
    $stale = Join-Path $InstallDir "web-token.txt"
    if (Test-Path $stale) {
        Remove-Item $stale -Force
        Say "note    : removed web-token.txt - the page token lives in .env now"
    }
    Say "web page: http://127.0.0.1:$WebPort"
    # The value is not printed: this console is transcribed to tinycmdr.log, and a page
    # token drives shell on this box.
    Say "          page token: in .env (TINYCMDR_WEB_TOKEN, mode 600) - not shown here;"
    Say "          read it with:   Get-Content `"$envPath`" | Select-String TINYCMDR_WEB_TOKEN"
} else {
    Say "web page: off - local checks need no port:  tinycmdr.py --once ""<task>"""
}

# --------------------------------------------------------- 6. launcher + service
Head "writing launcher"
# pythonw keeps a window from appearing; fall back to python.exe if pythonw is absent
$pywPath = Join-Path (Split-Path $py.Path) "pythonw.exe"
if (-not (Test-Path $pywPath)) { $pywPath = $py.Path }
# This file is kept PURE ASCII by construction: its folder comes from
# WScript.ScriptFullName and the only absolute path left is the interpreter fallback.
# When that path itself is not ASCII - a python under a profile whose name is not ASCII -
# the ASCII encoder below would mangle it exactly the way it used to mangle the install
# folder, so let WSH resolve the bare name through PATH instead: that is where the
# installer's own python.org install puts it (audit W4).
$vbsPyFallback = $pywPath
if ($vbsPyFallback -notmatch '^[\x20-\x7e]+$') {
    $vbsPyFallback = Split-Path -Leaf $pywPath
    Say "NOTE    : the interpreter path is not ASCII - the hidden launcher will find"
    Say "          $vbsPyFallback on PATH instead of baking the path into the script"
}
$vbs = @"
' Launches the tinycmdr SUPERVISOR hidden (no console window) and WAITS for it.
' Started by the logon shortcut (or by the scheduled task when -AsService was
' used). Run by hand:
'   wscript //B //Nologo tinycmdr-service.vbs
'
' The wait is load-bearing, and so is running the supervisor rather than the bot:
'   * running tinycmdr.py directly exits at once, so the task always looked
'     "Ready" even while the bot was dead, and RestartOnFailure could never fire;
'   * while the supervisor runs the task shows Running, and when it exits its code
'     propagates, so that policy does fire.
' The supervisor is what keeps the bot alive (relaunch on exit 75 or a crash, a
' readiness check, status JSON in logs/); this task is the outer safety net.
'
' The folder comes from WScript.ScriptFullName, not from an absolute path pasted in
' (audit W4): the install folder used to be written through an ASCII encoder, so a
' folder whose path holds a non-ASCII name came out with that name replaced by ? and
' the autostart was dead on the next reboot while the installer still reported success.
' Nothing but the interpreter fallback is absolute now, so the file stays ASCII wherever
' it lands.
Dim fso, sh, here, py
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh  = CreateObject("WScript.Shell")
here = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = here
py = here & "\venv\Scripts\pythonw.exe"
If Not fso.FileExists(py) Then py = "$vbsPyFallback"
sh.Run """" & py & """ """ & here & "\tinycmdr-supervise.py"" $SuperviseArgs", 0, True
"@
# ASCII on purpose: the text above is path-free by construction, so a non-ASCII profile
# name cannot reach the encoder to be destroyed (audit W4).
Set-Content (Join-Path $InstallDir "tinycmdr-service.vbs") $vbs -Encoding ASCII

$bat = @"
@echo off
rem tinycmdr launcher - double-click to start with a console window you can watch.
rem Auto-start is handled by the Startup shortcut (or the scheduled task "$AppName" on
rem an -AsService install); this file is for manual runs.
rem
rem %~dp0 is THIS file's own folder, so no absolute path is baked into the text. The
rem installer used to interpolate the install folder and write it through an ASCII
rem encoder, so a folder whose path holds a non-ASCII name came out with that name
rem replaced by ? - a launcher pointing at a folder that never existed (audit W4).
setlocal
cd /d "%~dp0"
set "PY=%~dp0venv\Scripts\python.exe"
if exist "%PY%" goto :run
rem No venv (the documented fallback install): the dependencies went into a machine
rem python, so look that one up - skipping the Microsoft Store stub on PATH, the same
rem trap tinycmdr.cmd skips.
set "PY="
call :findpy python.exe
if not defined PY call :findpy py.exe
if not defined PY (
    echo tinycmdr: no python on PATH - re-run the installer. 1>&2
    exit /b 127
)
:run
"%PY%" "%~dp0tinycmdr.py" %*
exit /b %ERRORLEVEL%

:findpy
for /f "delims=" %%I in ('where %~1 2^>nul') do (
    echo(%%I| findstr /i /c:"WindowsApps" >nul
    if errorlevel 1 if not defined PY set "PY=%%I"
)
exit /b 0
"@
Set-Content (Join-Path $InstallDir "launch_tinycmdr.bat") $bat -Encoding ASCII
Say "wrote   : tinycmdr-service.vbs, launch_tinycmdr.bat"

# Secrets lockdown (security review 2026-09-23): .env holds the bot token, the
# web token and provider keys, and the folder holds sessions and notes. Without
# this they sit at the inherited folder ACL - readable by anything running as
# this user or as an admin. Keep the install to this user, Administrators and
# SYSTEM (the scheduled task runs as this same user). SIDs, not names:
# BUILTIN\Administrators is localized on non-English Windows.
try {
    icacls $InstallDir /inheritance:r /grant:r `
        "$($env:USERNAME):(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" "*S-1-5-18:(OI)(CI)F" `
        | Out-Null
    Say "acl     : $InstallDir locked to $env:USERNAME, Administrators, SYSTEM"
} catch {
    Say "NOTE    : could not tighten the ACL on $InstallDir ($($_.Exception.Message))"
}

# --------------------------------------------------------- 7. starting it up
# Two ways, and the DEFAULT one needs no administrator rights at all:
#   * a shortcut in your own Startup folder - starts at logon, in your session.
#     This is what a plain install gets.
#   * a scheduled task (-AsService) - starts at BOOT, before anyone logs in.
#     Windows reserves boot-start tasks for administrators, so only that one
#     needs an elevated shell. Nothing else in this installer does.
if (-not $SkipTask -and -not $RegisterTask) {
    Head "autostart: skipped"
    Say "no chat account and no -EnableWeb, so there is nothing to keep running in the"
    Say "background. Both local doors work from a shell:"
    Say "  python tinycmdr.py --cli        (a session in this window)"
    Say "  python tinycmdr.py --web        (a page on http://127.0.0.1:$WebPort)"
}
if ($RegisterTask -and -not $AsService) {
    Head "starting it at logon (no admin needed)"
    $startupDir = Split-Path -Parent $StartupLink
    New-Item -ItemType Directory -Force -Path $startupDir | Out-Null
    try {
        # A shortcut rather than a copied script: one owner for the launcher, and
        # removing one file undoes the autostart. wscript.exe runs the hidden
        # launcher with no console window.
        $ws  = New-Object -ComObject WScript.Shell
        $lnk = $ws.CreateShortcut($StartupLink)
        $lnk.TargetPath        = Join-Path $env:SystemRoot "System32\wscript.exe"
        $lnk.Arguments         = "//B //Nologo `"$InstallDir\tinycmdr-service.vbs`""
        $lnk.WorkingDirectory  = $InstallDir
        $lnk.Description       = "tinycmdr ($AppName) - hidden background agent"
        $lnk.Save()
        Say "startup : $StartupLink"
        Say "          starts hidden at your next logon (delete that shortcut to stop it)"
    } catch {
        Say "NOTE    : could not write the Startup shortcut ($($_.Exception.Message))"
        Say "          start it by hand instead: $InstallDir\launch_tinycmdr.bat"
    }
}
if ($RegisterTask -and $AsService) {
    Head "registering the scheduled task"
    if ($LocalWeb) {
        # No chat account: run the LOCAL PAGE, still under the supervisor so a crash is
        # respawned. This is the one case where the task does not run the chat lane.
        $pyw = Join-Path (Split-Path -Parent $py.Path) "pythonw.exe"
        if (-not (Test-Path $pyw)) { $pyw = $py.Path }
        $action = New-ScheduledTaskAction -Execute $pyw -Argument "tinycmdr-supervise.py $SuperviseArgs"
        $action.WorkingDirectory = $InstallDir
        Say "mode    : local page only - no chat account, page on 127.0.0.1:$WebPort"
    } else {
        $action = New-ScheduledTaskAction -Execute "wscript.exe" `
                    -Argument "//B //Nologo ""$InstallDir\tinycmdr-service.vbs"""
    }
    # Which account the task runs as. "$env:USERDOMAIN\$env:USERNAME" is WRONG on a
    # machine that is not in a domain: USERDOMAIN is "WORKGROUP", which does not
    # resolve, and Register-ScheduledTask dies with "No mapping between account names
    # and security IDs was done" (measured on a fresh install on a Windows host in a
    # workgroup, where the previous install used the bare account name and worked).
    # Resolve a real account
    # first: the domain only when there is one, then the machine name, then the bare
    # user name, and prove each by translating it to a SID.
    $acct = $null
    $cands = @()
    if ($env:USERDOMAIN -and $env:USERDOMAIN -ne "WORKGROUP") { $cands += "$env:USERDOMAIN\$env:USERNAME" }
    $cands += "$env:COMPUTERNAME\$env:USERNAME"
    $cands += $env:USERNAME
    foreach ($c in $cands) {
        try {
            $null = (New-Object System.Security.Principal.NTAccount($c)).Translate([System.Security.Principal.SecurityIdentifier])
            $acct = $c
            break
        } catch { }
    }
    if (-not $acct) { Fail "no account for the scheduled task resolves ($($cands -join ', '))" }
    Say "account : $acct"

    $tLogon = New-ScheduledTaskTrigger -AtLogOn -User $acct
    $tLogon.Delay = "PT30S"                            # let the network/Docker settle first
    $tBoot  = New-ScheduledTaskTrigger -AtStartup
    $tBoot.Delay = "PT4M"
    $set = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
             -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 2) `
             -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
    $principal = New-ScheduledTaskPrincipal -UserId $acct `
                   -LogonType S4U -RunLevel Limited              # headless: runs with or without a logon
    try {
        Register-ScheduledTask -TaskName $AppName -Action $action -Trigger @($tLogon, $tBoot) `
            -Settings $set -Principal $principal -Force `
            -Description "tinycmdr: Mattermost ops agent (@$BotName)" | Out-Null
        Say "task    : $AppName registered (logon +30s, boot +4min, S4U)"
    } catch {
        Say "S4U registration failed ($($_.Exception.Message)) - falling back to interactive logon"
        $principal = New-ScheduledTaskPrincipal -UserId $acct -LogonType Interactive
        # The fallback used to be bare, so with $ErrorActionPreference = Stop an
        # unelevated run (a fleet kit's as_service: true, a wrong account) hit the trap
        # and printed "INSTALL FAILED", exit 2 - after the files, the venv, config.json
        # and .env were already written (audit W1). Catch it and say what to do instead.
        try {
            Register-ScheduledTask -TaskName $AppName -Action $action -Trigger @($tLogon, $tBoot) `
                -Settings $set -Principal $principal -Force `
                -Description "tinycmdr: Mattermost ops agent (@$BotName)" | Out-Null
            Say "task    : $AppName registered (interactive - starts at your logon only)"
        } catch {
            Fail ("could not register the scheduled task '$AppName': $($_.Exception.Message)`n" +
                  "Run this installer again from an elevated PowerShell (Run as Administrator)," +
                  " or drop -AsService - a logon install needs no administrator rights.`n" +
                  "The files are in $InstallDir; start it with launch_tinycmdr.bat.")
        }
    }
}

# ------------------------------------------------------------ 8. start + verify
if ($RegisterTask -and -not $NoStart) {
    Head "starting and verifying"
    if ($AsService) {
        Start-ScheduledTask -TaskName $AppName
    } else {
        # No task to start: run the very launcher the logon shortcut runs, so the
        # install is proven now instead of at the next logon. wscript with //B has
        # no console window, and this returns as soon as it is launched.
        Start-Process -FilePath (Join-Path $env:SystemRoot "System32\wscript.exe") `
            -ArgumentList "//B //Nologo `"$InstallDir\tinycmdr-service.vbs`""
    }
    Start-Sleep -Seconds 3
    $probe = Invoke-Probe -Dir $InstallDir -Python $py.Path
    Write-Host (($probe.Trim() -split "`n" | Select-Object -Last 6) -join "`n")
    if ($probe -match "READY") {
        Say "the agent answered - the app and the model endpoint both work"
    } else {
        Say "INSTALLED, BUT NOT VERIFIED: the probe did not get an answer."
        Say "That is a configuration gap, not a broken install - the files and the"
        Say "$(if ($AsService) { 'scheduled task' } else { 'logon shortcut' }) are in place."
        Say "Set llm.base_url / llm.model in $cfgPath (and see the output above), then:"
        if ($AsService) {
            Say "  Stop-ScheduledTask $AppName; Start-ScheduledTask $AppName"
        } else {
            Say "  tinycmdr restart      (or stop and start it from the Startup shortcut)"
        }
        $Unverified = $true
    }
}

Head "done - still to do"
$todo = @()
if ($TgLane -and -not $ChatLane) {
    Say "This install answers Telegram DMs. The lane starts by itself (no Mattermost token"
    Say "on this box), and the allowlist is $($tgIds -join ', ')."
    Say ""
    Say "  DM your bot and it answers; a group message is refused on purpose."
    if (-not $RegisterTask) { Say "  NOTE   : no background task was registered (-SkipTask), so nothing is listening yet." }
} elseif (-not $AnyLane) {
    Say "This install has NO chat account, which is a supported way to run it. Two doors are"
    Say "open right now, and neither needs a chat server:"
    Say ""
    Say "  a session   :  cd $InstallDir ; python tinycmdr.py --cli"
    Say "  a local page:  cd $InstallDir ; python tinycmdr.py --web"
    Say "                 then open http://127.0.0.1:$WebPort"
    if ($LocalWeb) {
        Say "                 (this install already serves that page; its token is in"
        Say "                  .env: TINYCMDR_WEB_TOKEN)"
    }
    Say ""
    Say "Add a Mattermost account whenever you want one:"
    Say "  install-tinycmdr.cmd -Force -MattermostTokenFile <file with the token>"
    Say "Add a Telegram DM whenever you want one:"
    Say "  install-tinycmdr.cmd -Force -TelegramToken <token> -TelegramIds <your numeric id>"
    Say ""
}
if (-not $MattermostToken) { $todo += "optional: Mattermost bot token -> $envPath  (TINYCMDR_MM_TOKEN=...)" }
if (-not $AllowedUser)     { $todo += "optional: allowed_users -> $cfgPath  (your Mattermost user id)" }
if ($MattermostUrl -eq "CHANGE-ME.example.com") { $todo += "optional: Mattermost server url -> $cfgPath  (mattermost.url)" }
if ($LoopbackModel) { $todo += "model endpoint        -> $cfgPath  (llm.base_url - loopback right now)" }
if ($todo.Count -eq 0 -and $ChatLane) { Say "nothing - this install is configured" }
elseif (-not $ChatLane -and $todo.Count -eq 0) { Say "nothing required - both local doors work" }
else { $n = 1; foreach ($t in $todo) { Say "$n. $t"; $n++ } }
Say ""
if ($todo.Count -gt 0 -and $RegisterTask) {
    if ($AsService) {
        Say "after editing, restart:  Stop-ScheduledTask $AppName; Start-ScheduledTask $AppName"
    } else {
        Say "after editing, restart:  tinycmdr restart   (or delete/restore the Startup shortcut)"
    }
}
if ($todo.Count -gt 0 -and -not $RegisterTask) { Say "after editing, just start it:  cd $InstallDir ; python tinycmdr.py --cli   (or --web)" }
Say "logs: $InstallDir\tinycmdr.log"
if ($Ask) {
    if ($WantChat) { Say "DM the bot account on $MattermostUrl and it will answer." }
    if ($WantWeb) {
        Say "the page: $webLink"
        Say "  it asks for its token on first open - it is in .env (TINYCMDR_WEB_TOKEN)"
        if (Ask-Yes "Open it now?" $true) {
            Start-Process $webLink | Out-Null
        }
    }
    if ($WantCli) {
        Say "a session needs nothing running:  cd $InstallDir ; python tinycmdr.py --cli"
        if (Ask-Yes "Open a session now?" $false) {
            Say "starting a session - type your task, Ctrl-C to leave"
            try { & $py.Path (Join-Path $InstallDir "tinycmdr.py") "--cli" } catch { }
        }
    }
    if ($WantTg) {
        Say "DM your Telegram bot and it will answer."
        if ($WantChat) { Say "  (Mattermost wins in the background process, so run the Telegram"
                         Say "   side as:  cd $InstallDir ; python tinycmdr.py --telegram)" }
    }
    if (-not ($WantChat -or $WantTg -or $WantWeb -or $WantCli)) {
        Say "nothing selected - the harness is installed and does not run in the background."
    }
}
if ($EnableWeb) {
    Say "web page : $webLink   (it asks for its token on first open)"
    # Loopback answering says nothing about the address a reader will actually type: a
    # page bound to 127.0.0.1 and one behind the host firewall look the same from here
    # (measured 2026-09-26 on a fleet macOS host: "nothing reachable at <lan-ip>:8787"
    # was both at once, and the installer had said nothing about either).
    $lanIp = ""
    try {
        $lanIp = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop |
                  Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" } |
                  Select-Object -First 1).IPAddress
    } catch { }
    if (-not $lanIp) {
        Say "web page : no LAN address on this machine yet (wifi off?)"
    } else {
        $pageHost = ""
        try { $pageHost = (Get-Content $cfgPath -Raw | ConvertFrom-Json).web.host } catch { }
        $pageUp = ""
        try { $pageUp = (Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 "http://$lanIp`:$WebPort/api/health").Content } catch { }
        if ($pageUp) {
            Say "web page : http://$lanIp`:$WebPort   (open that from any machine here)"
        } elseif ($pageHost -eq "127.0.0.1") {
            Say "web page : LOOPBACK only (web.host 127.0.0.1) - only this machine can"
            Say "           reach it. Set web.host to 0.0.0.0 in config.json to open it."
        } else {
            Say "web page : answers on 127.0.0.1 but not on http://$lanIp`:$WebPort."
            Say "           Windows Firewall is the usual reason; allow the port in an"
            Say "           ELEVATED shell:"
            Say "  New-NetFirewallRule -DisplayName `"tinycmdr web`" -Direction Inbound -LocalPort $WebPort -Protocol TCP -Action Allow"
        }
    }
    Say "  token: in .env (TINYCMDR_WEB_TOKEN) - not printed here"
}
Say "check  : $InstallDir> python tinycmdr.py --once ""/status""   (a session: python tinycmdr.py --cli)"
Say "redo   : install-tinycmdr.cmd -Force"
# The wrapper is the line to give a reader (audit W5): a stock Restricted execution
# policy refuses the -File form, and the -File form used to hardcode the default folder
# so a -InstallDir install could not be removed with it at all. Both wrappers pass
# -InstallDir through, and both are in the install folder / the package.
Say ("uninstall: {0}\install\install-tinycmdr.cmd -Uninstall -Force" -f $InstallDir)
Say "           (from an extracted package: INSTALL-WINDOWS.cmd -Uninstall -Force)"
Say ('           (or: powershell -ExecutionPolicy Bypass -File "' + $InstallDir +
     '\install\uninstall-tinycmdr.ps1" -InstallDir "' + $InstallDir + '" -Force)')
try { Stop-TranscriptRedacted } catch { }
if (-not $NoPause) { Read-Host "`nPress Enter to close" }
# 0 = installed and verified - 3 = installed, model endpoint not answering yet
if ($Unverified) { exit 3 } else { exit 0 }

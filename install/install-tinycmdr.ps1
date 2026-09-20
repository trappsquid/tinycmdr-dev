<#
    tinycmdr installer - copies the bot to this host and registers it to start at
    logon and at boot, the way the fleet's Windows hosts run it.

    Double-click ..\install-tinycmdr.cmd, or run this file from a shell:

        powershell -ExecutionPolicy Bypass -File .\install\install-tinycmdr.ps1

    With no arguments it uses install\fleet-defaults.json from the package (that
    file carries this fleet's Mattermost host, model endpoint and allowed user, so
    a fleet host needs nothing typed in), reuses the bot token from an existing
    .env, and asks for one only if there is none. A redo is:

        install-tinycmdr.cmd -Force

    Other things it can do:
        -VerifyOnly        is this install working? (no admin, no reinstall)
        -Uninstall [-Force] stop the task, remove the folder

    Exit codes:
        0  installed and verified
        1  bad input or missing prerequisite
        2  install failed
        3  installed, but the model endpoint did not answer (a config gap)
#>
[CmdletBinding()]
param(
    [string] $InstallDir      = "C:\tinycmdr",
    [string] $TaskName        = "tinycmdr",
    [string] $MattermostUrl   = "",              # default: fleet-defaults.json
    [int]    $MattermostPort  = 443,
    [string] $MattermostToken = "",              # default: token file, existing .env, then prompt
    [string] $MattermostTokenFile = "",          # read the token from a file instead
    [string] $SecretsFile     = "",              # .env-style file: tinycmdr_MM_TOKEN,
                                                 # TAVILY_API_KEY, ANYSEARCH_API_KEY
                                                 # (DEEPSEEK_API_KEY is per bot and is
                                                 #  ignored here - set it per host)
    [string] $AllowedUser     = "",              # default: fleet-defaults.json
    [string] $BotName         = "",              # defaults to this machine's name
    [string] $ModelBaseUrl    = "",              # default: fleet-defaults.json
    [string] $Model           = "main",
    [int]    $WebPort         = 8787,            # only used with -EnableWeb
    [switch] $EnableWeb,                         # legacy local chat page (off by default)
    [string] $Python          = "",              # full path to python.exe if auto-detect fails
    [switch] $InstallPython,                     # winget-install Python 3.12 if missing
    [switch] $Force,                             # redo: stop what is running, overwrite everything
    [switch] $SkipTask,                          # files only: no admin, no task, no start
    [switch] $NoStart,
    [switch] $NoPause,                           # for scripted runs (the .cmd uses this)
    [switch] $VerifyOnly,                        # just probe -InstallDir and stop
    [switch] $Uninstall                          # remove the task and the folder
)

$ErrorActionPreference = "Stop"
$Source = Split-Path -Parent $PSScriptRoot       # package root (one level above install\)

# Everything below is transcribed. A Windows install often runs in a window that
# closes the moment the script ends (or on a host you cannot see), so the reason
# for a failure has to survive on disk: %TEMP%\tinycmdr-install.log, the same file
# the .cmd wrapper points at.
$LogFile = Join-Path $env:TEMP "tinycmdr-install.log"
try { Start-Transcript -Path $LogFile -Force | Out-Null } catch { }
trap {
    Write-Host "`nINSTALL FAILED: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Full log: $LogFile"
    try { Stop-Transcript | Out-Null } catch { }
    if (-not $NoPause) { Read-Host "Press Enter to close" }
    exit 2
}

$Unverified = $false    # install ok, but the model endpoint did not answer
$AppName = $TaskName
$elevated = ([Security.Principal.WindowsPrincipal] `
             [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
             [Security.Principal.WindowsBuiltInRole]::Administrator)

# Elevation is needed only to touch the scheduled task, so -SkipTask and
# -VerifyOnly work from a normal shell (and are testable).
if (-not $elevated -and -not $SkipTask -and -not $VerifyOnly) {
    Write-Host "This needs an elevated PowerShell to register the scheduled task." -ForegroundColor Red
    Write-Host "Re-run as Administrator, or use -SkipTask to install files only."
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

function Stop-tinycmdrProcesses {
    <#
        Kill python processes started from $Dir. Needed before a -Force copy and
        before an uninstall: the running bot holds tinycmdr.log and tinycmdr.lock,
        so overwriting in place either fails or leaves a stale process alive.
    #>
    param([string] $Dir)
    $killed = 0
    try {
        Get-CimInstance Win32_Process -Filter "Name like 'python%'" -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandLine -and $_.CommandLine -like "*$Dir*" } |
            ForEach-Object {
                try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop; $killed++ } catch { }
            }
    } catch { }
    return $killed
}

function Say  ($m) { Write-Host "  $m" }
function Head ($m) { Write-Host "`n== $m" -ForegroundColor Cyan }
function Fail ($m) {
    Write-Host "`nFAILED: $m" -ForegroundColor Red
    Write-Host "Full log: $LogFile"
    try { Stop-Transcript | Out-Null } catch { }
    if (-not $NoPause) { Read-Host "Press Enter to close" }
    exit 1
}

function Resolve-Python {
    param([string] $Explicit)
    $cands = @()
    if ($Explicit) { $cands += $Explicit }
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $p = (& py -3.12 -c "import sys; print(sys.executable)" 2>$null)
        if ($LASTEXITCODE -eq 0 -and $p) { $cands += $p.Trim() }
    }
    $cands += @(Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python31*\python.exe" -ErrorAction SilentlyContinue |
                Sort-Object FullName -Descending | Select-Object -ExpandProperty FullName)
    $cands += @(Get-ChildItem "C:\Python31*\python.exe" -ErrorAction SilentlyContinue |
                Select-Object -ExpandProperty FullName)
    foreach ($c in $cands) {
        if (-not (Test-Path $c)) { continue }
        $v = (Invoke-Py $c -c "import sys; print('%d.%d' % sys.version_info[:2])")
        $v = ($v -split "`n" | Where-Object { $_.Trim() -match '^\d+\.\d+$' } | Select-Object -First 1)
        if ($v -and [version]$v.Trim() -ge [version]"3.10") { return @{ Path = $c; Version = $v.Trim() } }
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
}

# ------------------------------------------------------------------ uninstall

if ($Uninstall) {
    Head "uninstalling $AppName"
    $taskExists = $null -ne (Get-ScheduledTask -TaskName $AppName -ErrorAction SilentlyContinue)
    if (-not $taskExists -and -not (Test-Path $InstallDir)) {
        Say "nothing to remove (no task '$AppName', no $InstallDir)"
        try { Stop-Transcript | Out-Null } catch { }
        exit 0
    }
    if ($taskExists -and -not $SkipTask) {
        try { Stop-ScheduledTask -TaskName $AppName -ErrorAction SilentlyContinue } catch { }
        try {
            Unregister-ScheduledTask -TaskName $AppName -Confirm:$false -ErrorAction Stop
            Say "task    : $AppName removed"
        } catch { Say "task    : could not remove $AppName ($($_.Exception.Message))" }
    }
    $n = Stop-tinycmdrProcesses -Dir $InstallDir
    if ($n) { Say "stopped : $n process(es)" }
    if (Test-Path $InstallDir) {
        if (-not $Force) {
            if ($NoPause) { Fail "refusing to delete $InstallDir without -Force (or run interactively to confirm)" }
            $ans = Read-Host "Delete $InstallDir and everything in it? (y/N)"
            if ($ans -notmatch '^(y|yes)$') {
                Say "kept $InstallDir"
                try { Stop-Transcript | Out-Null } catch { }
                exit 0
            }
        }
        Remove-Item $InstallDir -Recurse -Force
        Say "removed : $InstallDir"
    }
    Say "done"
    try { Stop-Transcript | Out-Null } catch { }
    if (-not $NoPause) { Read-Host "`nPress Enter to close" }
    exit 0
}

# ------------------------------------------------------------------- preamble

Head "tinycmdr installer"
Say "package : $Source"
Say "target  : $InstallDir"
if ($fromFleet.Count) { Say "fleet   : $($fromFleet -join ', ') (from fleet-defaults.json)" }

# ------------------------------------------------------------- 1. python 3.12
Head "finding Python"
$py = Resolve-Python -Explicit $Python
if (-not $py -and $InstallPython) {
    Say "no Python 3.10+ found - installing Python 3.12 via winget"
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) { Fail "winget is unavailable; install Python 3.12 manually and re-run" }
    & winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements | Out-Null
    $py = Resolve-Python -Explicit $Python
}
if (-not $py) { Fail "no Python 3.10+ found. Install Python 3.12 (https://python.org) or pass -InstallPython" }
Say "python  : $($py.Path)  (v$($py.Version))"

# The dependency set is the one the code declares in its own header:
#   pip install requests mmpy_bot croniter
# Installing only `requests` produced a bot that started, logged two warnings and
# never connected -- mmpy_bot IS the Mattermost client (live 2026-09-10).
$reqFile = Join-Path $Source "requirements.txt"
$missReq  = -not (Test-PyImport $py.Path "requests")
$missMm   = -not (Test-PyImport $py.Path "mmpy_bot")
$missCron = -not (Test-PyImport $py.Path "croniter")
if ($missReq -or $missMm -or $missCron) {
    $what = @()
    if ($missReq)  { $what += "requests" }
    if ($missMm)   { $what += "mmpy_bot" }
    if ($missCron) { $what += "croniter" }
    Say "deps    : missing $($what -join ', ') - installing (this needs internet/PyPI)"
    if (Test-Path $reqFile) {
        $pip = Invoke-Py $py.Path -m pip install --quiet --disable-pip-version-check -r $reqFile
    } else {
        $pip = Invoke-Py $py.Path -m pip install --quiet --disable-pip-version-check requests mmpy_bot croniter
    }
    $stillReq = -not (Test-PyImport $py.Path "requests")
    $stillMm  = -not (Test-PyImport $py.Path "mmpy_bot")
    if ($stillReq -or $stillMm) {
        Write-Host (($pip.Trim() -split "`n" | Select-Object -Last 8) -join "`n")
        Fail "could not install the required dependencies (requests, mmpy_bot) for $($py.Path) - pip's output is above. Check internet/proxy, then re-run."
    }
    if (-not (Test-PyImport $py.Path "croniter")) {
        Say "deps    : croniter still missing - the schedule tool will be disabled"
    }
    Say "deps    : installed"
} else {
    Say "deps    : requests, mmpy_bot, croniter present"
}

# ---------------------------------------------------------------- verify only

if ($VerifyOnly) {
    if (-not (Test-Path (Join-Path $InstallDir "tinycmdr.py"))) {
        Write-Host "no tinycmdr.py in $InstallDir" -ForegroundColor Red
        try { Stop-Transcript | Out-Null } catch { }
        exit 1
    }
    Head "verifying $InstallDir"
    $p = Invoke-Probe -Dir $InstallDir -Python $py.Path
    Write-Host (($p.Trim() -split "`n" | Select-Object -Last 6) -join "`n")
    try { Stop-Transcript | Out-Null } catch { }
    if ($p -match "READY") {
        Write-Host "OK: the agent answered" -ForegroundColor Green
        if (-not $NoPause) { Read-Host "Press Enter to close" }
        exit 0
    }
    Write-Host "NOT VERIFIED: no answer from the model endpoint - check llm.base_url / llm.model" -ForegroundColor Yellow
    if (-not $NoPause) { Read-Host "Press Enter to close" }
    exit 3
}

# ----------------------------------------------------------------- 2. the app
$required = @("tinycmdr.py", "config.example.json", ".env.example", "skills", "tests")
foreach ($f in $required) {
    if (-not (Test-Path (Join-Path $Source $f))) { Fail "package is missing $f (run the installer from the extracted zip)" }
}
if ((Test-Path $InstallDir) -and -not $Force) {
    $existing = (Get-ChildItem $InstallDir -ErrorAction SilentlyContinue | Measure-Object).Count
    if ($existing -gt 0) {
        Fail "$InstallDir already exists and is not empty - use -Force to redo it in place, or -Uninstall to remove it, or -InstallDir <other>"
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

# resolve the bot token before touching anything: -MattermostToken, the secrets
# file, a token file, an existing .env (so a -Force redo keeps it), then ask
$tokenSource = ""
if (-not $MattermostToken -and $secrets["tinycmdr_MM_TOKEN"]) {
    if ($MattermostTokenFile) {
        $MattermostToken = (Get-Content $MattermostTokenFile -Raw).Trim()
        $tokenSource = "the token file"
    } else {
        $MattermostToken = $secrets["tinycmdr_MM_TOKEN"]
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
            if ($s -match '^tinycmdr_MM_TOKEN=(.+)$' -and $matches[1].Trim()) {
                $MattermostToken = $matches[1].Trim()
                $tokenSource = "the existing .env (redo kept it)"
                break
            }
        }
    }
    if (-not $MattermostToken -and -not $NoPause) {
        $sec = Read-Host "Mattermost bot token (blank = set it in .env later)" -AsSecureString
        $MattermostToken = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
            [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
        if ($MattermostToken) { $tokenSource = "prompt" }
    }
}

# ---------------------------------------------------------------- 3. copy files
Head "copying the app"
if ($Force) {
    # stop what is running from here first: the live process holds tinycmdr.log
    # and tinycmdr.lock, so overwriting in place is what makes a redo messy
    if (-not $SkipTask) { try { Stop-ScheduledTask -TaskName $AppName -ErrorAction SilentlyContinue } catch { } }
    $n = Stop-tinycmdrProcesses -Dir $InstallDir
    if ($n) { Say "stopped : $n running process(es) for a clean copy" }
    Start-Sleep -Seconds 2
}
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
$copy = @("tinycmdr.py", "requirements.txt", "config.example.json", ".env.example", "CHANGELOG.md", "README.md", "skills", "tests")
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

# --------------------------------------------------------------- 4. config.json
Head "writing config.json"
$cfgPath = Join-Path $InstallDir "config.json"
Copy-Item (Join-Path $InstallDir "config.example.json") $cfgPath -Force
if (-not $BotName) {
    $BotName = ($env:COMPUTERNAME).ToLower()          # host-style short name
}
if (-not $MattermostUrl) { $MattermostUrl = "CHANGE-ME.example.com" }
$webToken = if ($EnableWeb) {
    -join (1..48 | ForEach-Object { "{0:x}" -f (Get-Random -Maximum 16) })
} else { "" }

$cfg = Get-Content $cfgPath -Raw | ConvertFrom-Json
$cfg.mattermost.url           = $MattermostUrl
$cfg.mattermost.port          = $MattermostPort
$cfg.mattermost.token         = ""
$cfg.mattermost.allowed_users = if ($AllowedUser) { @($AllowedUser) } else { @() }
if (-not $ModelBaseUrl) { $ModelBaseUrl = "http://127.0.0.1:8081/v1" }
$cfg.llm.base_url             = $ModelBaseUrl
$cfg.llm.model                = $Model
# The local chat page is opt-in. tinycmdr is driven from Mattermost; a fresh
# install has no reason to open a port, and local checks don't need one.
# The template's web block is off, and the local chat page is opt-in per host, so
# only turn it on when it is actually wanted; the code default is "off" too.
if ($EnableWeb) {
    if (-not $cfg.PSObject.Properties['web']) {
        $cfg | Add-Member -NotePropertyName web -NotePropertyValue ([pscustomobject]@{})
    }
    $cfg.web.enabled = $true
    $cfg.web.token   = $webToken
    $cfg.web.port    = $WebPort
    $cfg.web.host    = "127.0.0.1"
}
$cfg.agent.bot_name           = $BotName
$cfg.agent.debug_dump_dir     = ""

# Write UTF-8 WITHOUT a BOM: PowerShell 5.1's Set-Content -Encoding UTF8 adds one,
# and a BOM breaks json parsing (and a BOM'd token file breaks the HTTP auth header
# by one invisible character). One-element arrays also collapse to a scalar in
# ConvertTo-Json, so allowed_users is forced back to a list.
$json = $cfg | ConvertTo-Json -Depth 20
if ($AllowedUser) {
    $json = $json -replace '"allowed_users":\s*"(.*?)"', '"allowed_users": [ "$1" ]'
} else {
    # ConvertTo-Json renders an empty array as null, and the bot's allowlist check
    # then raises on `uid not in None`. Write a real empty list.
    $json = $json -replace '"allowed_users":\s*null', '"allowed_users": []'
}
Write-Utf8NoBom $cfgPath $json
$verify = Get-Content $cfgPath -Raw | ConvertFrom-Json
if ($verify.mattermost.allowed_users -is [string]) {
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
if ($MattermostUrl -eq "CHANGE-ME.example.com") { Say "NOTE    : edit $cfgPath (mattermost.url) before the bot will connect" }
if (-not $AllowedUser) { Say "NOTE    : add your Mattermost user id to mattermost.allowed_users, or the bot ignores your DMs" }

# ---------------------------------------------------------------------- 5. .env
Head "writing .env"
$envPath = Join-Path $InstallDir ".env"
# A model key is per bot, never fleet-wide. Keep this host's own before the
# template replaces the file, and never take one from $SecretsFile: that is how
# several hosts ended up sharing one DeepSeek key (every one of them then showed
# the others' key as unused in the provider's dashboard).
$ownKey = ""
if (Test-Path $envPath) {
    foreach ($line in (Get-Content $envPath)) {
        if ($line -match '^\s*DEEPSEEK_API_KEY=(.+)$' -and $matches[1].Trim()) { $ownKey = $matches[1].Trim() }
    }
}
Copy-Item (Join-Path $InstallDir ".env.example") $envPath -Force
$envText = Get-Content $envPath -Raw
$written = @()
$refused = @()
foreach ($key in @("tinycmdr_MM_TOKEN", "TAVILY_API_KEY", "ANYSEARCH_API_KEY")) {
    $val = ""
    if ($key -eq "tinycmdr_MM_TOKEN") { $val = $MattermostToken } else { $val = $secrets[$key] }
    if (-not $val) { continue }
    if ($val -match '^\s*<.*>\s*$' -or $val -match '(?i)redacted') {
        # A redacted package or secrets file carries no real key. Writing the
        # placeholder looks like success and then 401s at every search, so leave
        # the key unset and say so (seen on two Linux hosts, 2026-09-11).
        $refused += $key
        continue
    }
    # write into the commented template line, or append if there is none
    if ($envText -match "(?m)^#?\s*$key=") {
        $envText = $envText -replace "(?m)^#?\s*$key=.*$", "$key=$val"
    } else {
        $envText = $envText.TrimEnd() + "`n$key=$val`n"
    }
    $written += $key
}
# The host's own model key goes back in (a redo must not lose it), and it never
# comes from the shared secrets file.
if ($ownKey) {
    if ($envText -match "(?m)^#?\s*DEEPSEEK_API_KEY=") {
        $envText = $envText -replace "(?m)^#?\s*DEEPSEEK_API_KEY=.*$", "DEEPSEEK_API_KEY=$ownKey"
    } else {
        $envText = $envText.TrimEnd() + "`nDEEPSEEK_API_KEY=$ownKey`n"
    }
    $written += "DEEPSEEK_API_KEY (this host's own)"
} elseif ($secrets.ContainsKey("DEEPSEEK_API_KEY")) {
    Say "NOTE    : DEEPSEEK_API_KEY in the secrets file IGNORED - the model key is per bot."
    Say "          Put this host's own key in $envPath by hand."
}
Write-Utf8NoBom $envPath $envText
if ($written.Count) {
    Say "env     : $($written -join ', ') written"
    if ($MattermostToken) { Say "          (bot token from $tokenSource)" }
} else {
    Say "NOTE    : no keys to write - put the bot token in $envPath (tinycmdr_MM_TOKEN=...)"
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
    # No BOM: this is pasted into an HTTP header, where one stray byte is a 401.
    Write-Utf8NoBom (Join-Path $InstallDir "web-token.txt") $webToken
    Say "web page: http://127.0.0.1:$WebPort  (token in web-token.txt)"
} else {
    Say "web page: off - local checks need no port:  tinycmdr.py --once ""<task>"""
}

# --------------------------------------------------------- 6. launcher + service
Head "writing launcher"
$vbs = @"
' Launches tinycmdr hidden (no console window). Registered as the scheduled
' task "$AppName". Run by hand:  wscript //B //Nologo "$InstallDir\tinycmdr-service.vbs"
Dim sh
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "$InstallDir"
sh.Run """$($py.Path)"" tinycmdr.py", 0, False
"@
# pythonw keeps a window from appearing; fall back to python.exe if pythonw is absent
$pyw = Join-Path (Split-Path $py.Path) "pythonw.exe"
if (Test-Path $pyw) { $vbs = $vbs -replace [regex]::Escape($py.Path), $pyw }
Set-Content (Join-Path $InstallDir "tinycmdr-service.vbs") $vbs -Encoding ASCII

$bat = @"
@echo off
rem tinycmdr launcher - double-click to start with a console window you can watch.
rem Auto-start is handled by scheduled task "$AppName"; this is for manual runs.
cd /d "$InstallDir"
"$($py.Path)" tinycmdr.py
"@
Set-Content (Join-Path $InstallDir "launch_tinycmdr.bat") $bat -Encoding ASCII
Say "wrote   : tinycmdr-service.vbs, launch_tinycmdr.bat"

# ------------------------------------------------------------ 7. scheduled task
if (-not $SkipTask) {
    Head "registering the scheduled task"
    $action = New-ScheduledTaskAction -Execute "wscript.exe" `
                -Argument "//B //Nologo ""$InstallDir\tinycmdr-service.vbs"""
    $tLogon = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
    $tLogon.Delay = "PT30S"                            # let the network/Docker settle first
    $tBoot  = New-ScheduledTaskTrigger -AtStartup
    $tBoot.Delay = "PT4M"
    $set = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
             -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 2) `
             -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
    $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
                   -LogonType S4U -RunLevel Limited              # headless: runs with or without a logon
    try {
        Register-ScheduledTask -TaskName $AppName -Action $action -Trigger @($tLogon, $tBoot) `
            -Settings $set -Principal $principal -Force `
            -Description "tinycmdr: Mattermost ops agent (@$BotName)" | Out-Null
        Say "task    : $AppName registered (logon +30s, boot +4min, S4U)"
    } catch {
        Say "S4U registration failed ($($_.Exception.Message)) - falling back to interactive logon"
        $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive
        Register-ScheduledTask -TaskName $AppName -Action $action -Trigger @($tLogon, $tBoot) `
            -Settings $set -Principal $principal -Force `
            -Description "tinycmdr: Mattermost ops agent (@$BotName)" | Out-Null
        Say "task    : $AppName registered (interactive - starts at your logon only)"
    }
}

# ------------------------------------------------------------ 8. start + verify
if (-not $SkipTask -and -not $NoStart) {
    Head "starting and verifying"
    Start-ScheduledTask -TaskName $AppName
    Start-Sleep -Seconds 3
    $probe = Invoke-Probe -Dir $InstallDir -Python $py.Path
    Write-Host (($probe.Trim() -split "`n" | Select-Object -Last 6) -join "`n")
    if ($probe -match "READY") {
        Say "the agent answered - the app and the model endpoint both work"
    } else {
        Say "INSTALLED, BUT NOT VERIFIED: the probe did not get an answer."
        Say "That is a configuration gap, not a broken install - the files and the"
        Say "scheduled task are in place. Set llm.base_url / llm.model in $cfgPath"
        Say "(and see the output above), then:"
        Say "  Stop-ScheduledTask $AppName; Start-ScheduledTask $AppName"
        $Unverified = $true
    }
}

Head "done - still to do"
$todo = @()
if (-not $MattermostToken) { $todo += "Mattermost bot token  -> $envPath  (tinycmdr_MM_TOKEN=...)" }
if (-not $AllowedUser)     { $todo += "allowed_users         -> $cfgPath  (your Mattermost user id)" }
if ($MattermostUrl -eq "CHANGE-ME.example.com") { $todo += "Mattermost server url -> $cfgPath  (mattermost.url)" }
if ($LoopbackModel) { $todo += "model endpoint        -> $cfgPath  (llm.base_url - loopback right now)" }
if ($todo.Count -eq 0) { Say "nothing - this install is configured" }
else { $n = 1; foreach ($t in $todo) { Say "$n. $t"; $n++ } }
Say ""
if ($todo.Count -gt 0) { Say "after editing, restart:  Stop-ScheduledTask $AppName; Start-ScheduledTask $AppName" }
Say "logs: $InstallDir\tinycmdr.log"
if ($EnableWeb) { Say "web page: http://127.0.0.1:$WebPort  (token in web-token.txt)" }
Say "check  : $InstallDir> python tinycmdr.py --once ""/status""   (or --cli)"
Say "redo   : install-tinycmdr.cmd -Force"
try { Stop-Transcript | Out-Null } catch { }
if (-not $NoPause) { Read-Host "`nPress Enter to close" }
# 0 = installed and verified - 3 = installed, model endpoint not answering yet
if ($Unverified) { exit 3 } else { exit 0 }

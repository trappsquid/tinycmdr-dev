# smoke-service.ps1 - install from the built package WITH the scheduled task, prove the
# agent is up, restart it through the shipped verb, prove it is still up, then remove the
# task and the install.
#
# WHY. The install job's smoke passes -SkipTask, so nothing registers or starts the task,
# and the restart helper's session path was never executed. A Windows box left deaf after
# `tinycmdr restart` is an open defect; this is the loop that reproduces it.
#
#   pwsh -NoProfile -ExecutionPolicy Bypass -File maintenance\smoke-service.ps1
#
# Needs an elevated runner: -AsService registers an S4U task, and the restart verb asks
# for the same elevation.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = (Get-Command python).Source
$TaskName = "Tinycmdr"
$Base = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { $env:TEMP }
$Work = Join-Path $Base ("tc-service-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
$Inst = Join-Path $Work "inst"
$Rules = Join-Path $Work "rules"
$Vpy = Join-Path $Inst "venv\Scripts\python.exe"
$App = Join-Path $Inst "tinycmdr.py"
$oldPyPath = $env:PYTHONPATH

function Say($m) { Write-Host "`n=== $m" }
function Fail($m) { Write-Host "*** $m" -ForegroundColor Red; throw $m }

function Invoke-Native([string] $exe, [string[]] $argv, [string] $cwd) {
    # Native stderr is a LOG here (the app logs its INFO lines there), and under
    # $ErrorActionPreference = "Stop" PowerShell 5.1 turns a redirected native stderr
    # line into a terminating NativeCommandError - the first health call died on its own
    # "theme.toml was missing" line (measured 2026-11-01 on windows-latest). Continue
    # for the call, capture both streams, then restore the caller's preference.
    $saved = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    Push-Location $cwd
    try {
        $out = (& $exe @argv 2>&1 | Out-String)
        $code = $LASTEXITCODE
    } finally {
        Pop-Location
        $ErrorActionPreference = $saved
    }
    return [pscustomobject]@{ Out = $out; Code = $code }
}

function Wait-Up([int] $seconds) {
    $deadline = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $deadline) {
        $r = Invoke-Native $Vpy @($App, "health") $Inst
        if ($r.Code -eq 0 -and $r.Out -match " up " -and $r.Out -notmatch " is DOWN") { return $r.Out }
        Start-Sleep -Seconds 2
    }
    return $null
}

New-Item -ItemType Directory -Force -Path $Work, $Rules | Out-Null
try {
    Say "build the public package"
    Copy-Item (Join-Path $Root "maintenance\private_rules.example.py") (Join-Path $Rules "private_rules.py")
    $env:PYTHONPATH = if ($oldPyPath) { "$Rules;$oldPyPath" } else { $Rules }
    $r = Invoke-Native $Python @((Join-Path $Root "maintenance\build-package.py"), "--public") $Root
    if ($r.Code -ne 0) { Write-Host $r.Out; Fail "build-package.py exited $($r.Code)" }
    $zip = Get-ChildItem (Join-Path $Root "dist\tinycmdr-*-win.zip") |
        Sort-Object LastWriteTime | Select-Object -Last 1
    if (-not $zip) { Fail "the build produced no win zip" }

    Say "unpack and install with -AsService -NoStart"
    $pkgRoot = Join-Path $Work "pkg"
    Expand-Archive -Path $zip.FullName -DestinationPath $pkgRoot -Force
    $pkg = Get-ChildItem $pkgRoot -Directory | Select-Object -First 1
    $installer = Join-Path $pkg.FullName "install\install-tinycmdr.ps1"
    # -NoStart: the installer's own verification drives a MODEL turn, and a runner has
    # no endpoint; the files and the task are what this smoke is about. It starts the
    # task below. Switches are passed BY NAME: an array splat binds positionally and put
    # -NoPath into MattermostPort (measured 2026-11-01).
    $savedEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $installOut = (& $installer -NonInteractive -AsService -NoStart -NoPath -NoPause `
            -InstallDir $Inst -Python $Python 2>&1 | Out-String)
        $installRc = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $savedEap
    }
    if ($installRc -ne 0) { Write-Host $installOut; Fail "the installer exited $installRc" }

    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $task) { Write-Host $installOut; Fail "the install registered no task '$TaskName'" }
    Say "start the task"
    Start-ScheduledTask -TaskName $TaskName

    Say "health after install"
    $out = Wait-Up 90
    if (-not $out) {
        $r = Invoke-Native $Vpy @($App, "health") $Inst
        Write-Host $r.Out
        Fail "health did not report up after install"
    }
    Write-Host $out.Trim()

    Say "restart through the shipped verb"
    $r = Invoke-Native $Vpy @($App, "restart") $Inst
    Write-Host $r.Out.Trim()
    if ($r.Code -ne 0) {
        $rlog = Join-Path $Inst "maintenance\restart-tinycmdr.log"
        if (Test-Path $rlog) { Get-Content $rlog -Tail 30 | Write-Host }
        Fail "tinycmdr restart exited $($r.Code)"
    }

    Say "health after restart"
    $out2 = Wait-Up 90
    if (-not $out2) {
        $rlog = Join-Path $Inst "maintenance\restart-tinycmdr.log"
        if (Test-Path $rlog) { Get-Content $rlog -Tail 40 | Write-Host }
        $t = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if ($t) { Write-Host ("task state: " + $t.State) }
        Fail "health did not report up after restart"
    }
    Write-Host $out2.Trim()
    Say "smoke-service: OK"
}
finally {
    try { Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue } catch { }
    try { Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue } catch { }
    try {
        Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
            Where-Object {
                $_.CommandLine -and
                $_.CommandLine.IndexOf($Inst, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
            } |
            ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    } catch { }
    if ($null -ne $oldPyPath) { $env:PYTHONPATH = $oldPyPath }
    else { Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue }
    Remove-Item $Work -Recurse -Force -ErrorAction SilentlyContinue
}

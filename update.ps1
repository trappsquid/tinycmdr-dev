<#
 tinycmdr update.ps1 - the ONE update path, version-independent.

   powershell -ExecutionPolicy Bypass -File update.ps1 [-Dir C:\path\to\install]

 The rule this file exists for: every user, on every released version, types
 `tinycmdr update` (or `/tinycmdr update` in chat) and it works. An installed copy is
 whatever version it is - possibly one whose own updater is old, broken, or predates the
 release package entirely - so the repair must not depend on it. This script is fetched
 from the LATEST release and does the whole job itself:

   1. fetch the release package for Windows and its SHA256SUMS
   2. verify the checksum before touching anything
   3. extract to a temp dir, then copy over the install - never overwriting a host-owned
      file (config.json, .env, soul.md, notes.md, tools\, skills\, sessions\, state, jobs,
      tasks, logs, spill, venv, theme.toml) and never deleting anything else
   4. say the version it moved from and to, and how to restart

 No git, ever. Exit code 0 when updated or already current, 1 when refused or failed (the
 install is left untouched).
#>
param(
    [string]$Dir = (Split-Path -Parent $MyInvocation.MyCommand.Path)
)
$ErrorActionPreference = "Stop"
# Windows PowerShell 5.1's default protocol set excludes TLS 1.2 where .NET's
# strong-crypto registry keys are unset, and GitHub requires it - the installer sets this
# and the updater runs in its own fresh 5.1 process (A-2026-10-08-172).
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$Repo = "trappsquid/tinycmdr"
$Dir = (Resolve-Path $Dir).Path
if (-not (Test-Path (Join-Path $Dir "tinycmdr.py"))) {
    Write-Error "update: $Dir is not a tinycmdr install (no tinycmdr.py)"; exit 1
}
function Get-Version([string]$file) {
    $t = Get-Content $file -Raw
    if ($t -match '(?m)^VERSION = "(.*?)"') { return $Matches[1] }
    return "?"
}
$Cur = Get-Version (Join-Path $Dir "tinycmdr.py")
$Work = Join-Path $env:TEMP ("tinycmdr-update-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
New-Item -ItemType Directory -Force -Path $Work | Out-Null
$Asset = "tinycmdr-win.zip"
$Base = "https://github.com/$Repo/releases/latest/download"
# TINYCMDR_UPDATE_URL names a mirror or a staged release the way update.sh honours it -
# the .ps1 twin ignored it, so a staged update could not be driven on Windows
# (A-2026-10-08-173).
if ($env:TINYCMDR_UPDATE_URL) { $Base = $env:TINYCMDR_UPDATE_URL.TrimEnd('/') }
Write-Host "update: $Dir is $Cur; fetching the latest release ($Asset)"
try {
    Invoke-WebRequest -UseBasicParsing "$Base/$Asset" -OutFile (Join-Path $Work $Asset)
    Invoke-WebRequest -UseBasicParsing "$Base/SHA256SUMS" -OutFile (Join-Path $Work "SHA256SUMS")
    $Want = (Select-String -Path (Join-Path $Work "SHA256SUMS") -Pattern ([regex]::Escape(" " + $Asset) + '$') |
             Select-Object -First 1).Line.Split(" ")[0]
    $Got = (Get-FileHash (Join-Path $Work $Asset) -Algorithm SHA256).Hash.ToLower()
    if (-not $Want -or $Want.ToLower() -ne $Got) {
        Write-Error "update: checksum mismatch - nothing was changed"; exit 1
    }
    Expand-Archive (Join-Path $Work $Asset) -DestinationPath (Join-Path $Work "pkg") -Force
    $Src = (Get-ChildItem (Join-Path $Work "pkg") -Recurse -Filter tinycmdr.py |
            Select-Object -First 1).DirectoryName
    if (-not $Src) { Write-Error "update: the package has no tinycmdr.py"; exit 1 }
    $New = Get-Version (Join-Path $Src "tinycmdr.py")

    # Host-owned paths: never overwrite, never delete anything not in the package.
    $HostFiles = @("config.json", ".env", "soul.md", "notes.md", "notes-authored.json",
                   "field-notes.md", "atlas.md", "experiments.jsonl", "web-sessions.json",
                   "state.json", "jobs.json", "tasks.json", "tasks.journal.jsonl",
                   "tasks.md", "confirm-allow.json", "tools-provenance.json", "theme.toml",
                   "tinycmdr.log", "tinycmdr.lock")
    $HostDirs = @("tools", "skills", "sessions", "snapshots", "logs", "spill", "venv",
                  "dist", ".git", "tmp")
    $written = 0
    Push-Location $Src
    try {
        Get-ChildItem -Recurse -File -Force | ForEach-Object {
            $rel = $_.FullName.Substring($Src.Length).TrimStart("\")
            $top = $rel.Split("\")[0]
            if ($HostFiles -contains $rel -or $HostDirs -contains $top) { return }
            $dest = Join-Path $Dir $rel
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dest) | Out-Null
            Copy-Item $_.FullName $dest -Force
            $written++
        }
    } finally { Pop-Location }
    Write-Host "update: $Cur -> $New ($written file(s); host-owned files left alone)"
    if ($Cur -eq $New) { Write-Host "update: already current" }
    # Single quotes: PowerShell eats a backtick in a double-quoted string (measured: the
    # message printed "inycmdr restart" - the backtick before "t" became a tab).
    else { Write-Host 'update: restart to run it - "tinycmdr restart" for the bot (a terminal session just relaunches)' }

    # ---- the page: an install that predates it has no token, and without one the server
    # stays off (never an open port - and also no door). This updater runs on any released
    # version, so the ask belongs here: in the operator's own console, with the mint as the
    # default and their own token always an option.
    $EnvFile = Join-Path $Dir ".env"
    if ((Test-Path $EnvFile) -and -not (Select-String -Path $EnvFile -Pattern '^TINYCMDR_WEB_TOKEN=' -Quiet)) {
        Write-Host ""
        Write-Host "This install has no page token yet (the browser page arrived in 1.0.67)."
        Write-Host "Without one the page does not start, and nothing opens on its own."
        $Tok = ""
        if ([Environment]::UserInteractive -and -not $env:TINYCMDR_NONINTERACTIVE) {
            $Tok = Read-Host "Page token (empty mints one, or paste your own)"
        }
        $Minted = $false
        if ([string]::IsNullOrWhiteSpace($Tok)) {
            $Bytes = New-Object byte[] 32
            [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($Bytes)
            $Tok = [Convert]::ToBase64String($Bytes).Replace('+', '-').Replace('/', '_').TrimEnd('=')
            $Minted = $true
        }
        # The file must END in a newline before the key is appended: Add-Content adds none
        # ahead of the value, so an editor-saved .env merged the key into the last line
        # (`TINYCMDR_MODEL_KEY=abcTINYCMDR_WEB_TOKEN=...`) - the model key corrupted, the
        # token invisible to the guard above and the page never starting. Read-modify-write,
        # the way tinycmdr.py's `_env_set` does it (run 23, A-2026-10-07-63). NOT run on this
        # box (no pwsh) - verified by reading, and by the twin in update.sh, which a suite
        # now executes.
        $EnvText = if (Test-Path $EnvFile) { [System.IO.File]::ReadAllText($EnvFile) } else { "" }
        if ($EnvText.Length -gt 0 -and -not $EnvText.EndsWith("`n")) { $EnvText += "`n" }
        [System.IO.File]::WriteAllText($EnvFile, $EnvText + "TINYCMDR_WEB_TOKEN=$Tok`n")
        $Port = 8790
        try {
            $cfg = Get-Content (Join-Path $Dir "config.json") -Raw | ConvertFrom-Json
            if ($cfg.web.port) { $Port = [int]$cfg.web.port }
        } catch { }
        $Suffix = ""
        if ($Minted) { $Suffix = " (minted for you)" }
        Write-Host "page token written to $EnvFile$Suffix"
        Write-Host "page link: http://127.0.0.1:$Port/#token=$Tok"
        Write-Host "  link again later:  tinycmdr web      (LAN/port wizard: tinycmdr setup)"
    }
} finally {
    Remove-Item $Work -Recurse -Force -ErrorAction SilentlyContinue
}

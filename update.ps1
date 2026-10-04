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
    $HostDirs = @("tools", "skills", "sessions", "logs", "spill", "venv", "dist", ".git")
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
} finally {
    Remove-Item $Work -Recurse -Force -ErrorAction SilentlyContinue
}

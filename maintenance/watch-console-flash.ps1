# Watch for a short-lived console process (powershell/conhost/cmd/wscript/cscript).
# Polls fast; logs every sighting with its command line and lifetime, plus any
# Task Scheduler event that lands in the same second. Silent when nothing appears.
param([int]$Minutes = 12, [string]$Out = 'C:/Users/<user>\logs\console-flash.log')
$log = $Out
$logDir = Split-Path $log
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
Add-Content -Path $log -Value "=== monitor start $(Get-Date -Format s) for $Minutes min ===" -Encoding utf8

$names = 'powershell','conhost','cmd','wscript','cscript','pwsh'
$seen = @{}
$end = (Get-Date).AddMinutes($Minutes)
while ((Get-Date) -lt $end) {
    $procs = Get-CimInstance -ClassName Win32_Process -ErrorAction SilentlyContinue |
             Where-Object { $names -contains ($_.Name -replace '\.exe$','').ToLower() }
    foreach ($p in $procs) {
        $key = "$($p.ProcessId)"
        if (-not $seen.ContainsKey($key)) {
            $seen[$key] = [pscustomobject]@{ pid = $p.ProcessId; name = $p.Name; cmd = $p.CommandLine; parent = $p.ParentProcessId; start = Get-Date }
            # parent name gives the culprit that spawned it
            $pn = (Get-CimInstance -ClassName Win32_Process -Filter "ProcessId=$($p.ParentProcessId)" -ErrorAction SilentlyContinue).Name
            Add-Content -Path $log -Value ("[{0}] APPEAR pid={1} parent={2}({3}) name={4} cmd={5}" -f `
                (Get-Date -Format 'HH:mm:ss'), $p.ProcessId, $p.ParentProcessId, $pn, $p.Name, [string]$p.CommandLine) -Encoding utf8
        }
    }
    # retire entries that vanished, reporting their lifetime
    foreach ($k in @($seen.Keys)) {
        if (-not (Get-Process -Id ([int]$k) -ErrorAction SilentlyContinue)) {
            $e = $seen[$k]
            $life = ((Get-Date) - $e.start).TotalSeconds
            Add-Content -Path $log -Value ("[{0}] GONE  pid={1} name={2} lifetime={3:N2}s cmd={4}" -f `
                (Get-Date -Format 'HH:mm:ss'), $e.pid, $e.name, $life, [string]$e.cmd) -Encoding utf8
            $seen.Remove($k)
        }
    }
    Start-Sleep -Milliseconds 300
}
Add-Content -Path $log -Value "=== monitor end $(Get-Date -Format s) ===" -Encoding utf8

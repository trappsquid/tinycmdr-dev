# Elevated audit: which tinycmdr/container tasks actually exist, with triggers.
$log = 'C:/Users/<user>\tinycmdr\maintenance\task-audit.log'
function Log([string]$m) { "$m" | Add-Content -Path $log }
Set-Content -Path $log -Value "=== task audit (elevated) $(Get-Date -Format s) ==="

Log "--- Get-ScheduledTask the manager box*/Tinycmdr ---"
try {
    Get-ScheduledTask | Where-Object { $_.TaskName -match 'the manager box|Tinycmdr|Hermes' } |
        ForEach-Object {
            $t = $_
            Log ("{0,-24} state={1} user={2} runlevel={3}" -f $t.TaskName, $t.State, $t.Principal.UserId, $t.Principal.RunLevel)
            $t.Triggers | ForEach-Object { Log ("    trigger={0} start={1} delay={2}" -f $_.CimClass.CimClassName, $_.StartBoundary, $_.Delay) }
            Log ("    action={0} {1}" -f $t.Actions[0].Execute, $t.Actions[0].Arguments)
        }
} catch { Log "Get-ScheduledTask FAILED: $($_.Exception.Message)" }

Log "--- schtasks /query the manager box-StartContainers ---"
$q = & schtasks /query /tn 'the manager box-StartContainers' /v /fo list 2>&1 | Out-String
Log ($q.Trim() -replace "`r`n", "`n")

Log "--- task files on disk (System32\Tasks) ---"
foreach ($f in (Get-ChildItem 'C:\Windows\System32\Tasks' | Where-Object { $_.Name -match 'the manager box|Tinycmdr|Hermes' })) {
    Log ("{0}  mtime={1}" -f $f.Name, $f.LastWriteTime)
}
Log "--- last 5 TaskScheduler operational events for the manager box-StartContainers ---"
try {
    Get-WinEvent -LogName 'Microsoft-Windows-TaskScheduler/Operational' -MaxEvents 200 -ErrorAction Stop |
        Where-Object { $_.Message -match 'the manager box-StartContainers' } |
        Select-Object -First 5 |
        ForEach-Object { Log ("{0}  id={1}  {2}" -f $_.TimeCreated, $_.Id, ($_.Message -replace "`r`n", " ")) }
} catch { Log "event log unavailable: $($_.Exception.Message)" }

Log "=== audit end ==="

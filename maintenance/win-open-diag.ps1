$out = "C:/Users/<user>\tinycmdr\maintenance\win-open-diag.txt"
$sb = [System.Text.StringBuilder]::new()
function W($s = "") { [void]$sb.AppendLine($s) }

W ("captured at: " + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'))
W ("user: " + ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name))
W ("elevated: " + ([System.Security.Principal.WindowsPrincipal]::New([System.Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)))
W ""

# 1) Scheduled tasks whose action launches a shell
W "=== SCHEDULED TASKS (shell/exec actions) ==="
try {
  Get-ScheduledTask -ErrorAction Stop | ForEach-Object {
    $t = $_
    $acts = @($t.Actions)
    $matches = $acts | Where-Object { $_.Execute -match 'powershell|cmd|python|wscript|cscript|tinycmdr|watchdog|schtasks|\.ps1|\.cmd|\.exe' }
    if ($matches) {
      W ("Task: " + (Join-Path $t.TaskPath $t.TaskName))
      $acts | ForEach-Object { W ("   exec: " + $_.Execute + "  args: " + $_.Arguments) }
      $t.Triggers | ForEach-Object { W ("   trigger: " + $_.GetType().Name) }
      W ("   states: started=" + $t.State + " hidden=" + $t.Settings.Hidden + " restartOnNet=" + $t.Settings.RestartOnNet)
      W ""
    }
  }
} catch { W ("tasks err: " + $_) }
W ""

# 2) Task Scheduler event log (recent)
W "=== TASK SCHEDULER EVENTS (last 40) ==="
try {
  $tss = Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-TaskScheduler'} -ErrorAction Stop -MaxEvents 40
  foreach ($e in $tss) {
    $msg = $e.Message
    $msg = $msg.Replace([char]13, ' ')
    $msg = $msg.Replace([char]10, ' ')
    $msg = [text.regularexpressions.regex]::Replace($msg, '\s+', ' ')
    W (($e.TimeCreated.ToString('HH:mm:ss')) + " id=" + $e.Id + " " + $msg.Trim())
  }
} catch { W ("tssched err: " + $_) }
W ""

# 3) Security 4688 process creation for shells
W "=== 4688 PROCESS CREATION (shells, last 60) ==="
try {
  $ev = Get-WinEvent -FilterHashtable @{LogName='Security'; Id=4688} -ErrorAction SilentlyContinue -MaxEvents 120
  $n = 0
  foreach ($e in $ev) {
    $cl = $e.Properties.CommandLine
    if ($cl -match 'powershell|cmd\.exe|conhost|wscript|cscript') {
      W (($e.TimeCreated.ToString('HH:mm:ss')) + "  cmd: " + $cl)
      $n++
      if ($n -ge 60) { break }
    }
  }
  if ($n -eq 0) { W "(no 4688 entries matched -- process creation may not be audited)" }
} catch { W ("4688 err: " + $_) }
W ""

# 4) Current shell processes
W "=== CURRENT SHELL PROCESSES ==="
$shells = Get-Process -ErrorAction SilentlyContinue | Where-Object { 'cmd','powershell','pwsh','conhost','wscript','cscript','WindowsTerminal' -contains $_.ProcessName } | Sort-Object StartTime
foreach ($p in $shells) {
  $dur = ((Get-Date) - $p.StartTime).TotalSeconds
  W ("id=" + $p.Id + " " + $p.ProcessName + " start=" + $p.StartTime.ToString('HH:mm:ss') + " dur=" + [int]$dur + "s  path=" + $p.Path)
}
W ""

# 5) Visible top-level windows
W "=== VISIBLE TOP-LEVEL WINDOWS ==="
$shellUI = New-Object -ComObject Shell.Application
$wins = $shellUI.Windows()
$seen = New-Object System.Collections.Generic.List[string]
for ($i = 0; $i -lt $wins.Count; $i++) {
  $w = $wins.Item($i)
  try {
    $path = $w.Document.FolderSelf.Path
    $loc = $w.LocationUrl
    $seen.Add($path + ' :: ' + [System.IO.Path]::GetFileNameWithoutExtension($loc))
  } catch {}
}
if ($seen.Count -gt 0) { ($seen | Sort-Object -Unique) | ForEach-Object { W $_ } } else { W "(none / COM unavailable)" }

[System.IO.File]::WriteAllText($out, $sb.ToString())
W ("WROTE " + $out)

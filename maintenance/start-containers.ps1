# Boot-time container watchdog: waits for the Docker engine, then starts any
# container of the example stack that is not running. Never stops or removes anything.
# Registered as SYSTEM task 'the manager box-StartContainers' (AtStartup +1 min).
# Rationale: `docker stop` clears a container from restart=unless-stopped, so an
# explicit stop before a reboot leaves the whole stack Exited (that stranded
# mattermost/tinycmdr on 2026-09-10). This is the safety net.

$log  = 'C:/Users/<user>\tinycmdr\maintenance\container-boot.log'
$docker = 'C:\Program Files\Docker\Docker\resources\bin\docker.exe'
$Containers = 'mattermost-db','mattermost','npm','landing','crawl4ai','flaresolverr','tor-proxy',
              'open-webui','docling','stirling-pdf','search-agg'

function L($m) { "$((Get-Date).ToString('s')) $m" | Add-Content -Path $log }
L "=== boot container check start ==="

# 1. wait for the engine (max 5 min)
$up = $false
for ($i = 1; $i -le 30; $i++) {
    $null = & $docker ps 2>$null
    if ($LASTEXITCODE -eq 0) { $up = $true; L "engine reachable after $($i*10)s"; break }
    Start-Sleep -Seconds 10
}
if (-not $up) { L "ABORT: docker engine not reachable after 300s"; exit 1 }

# 2. start anything down, in dependency order
$running = & $docker ps --format '{{.Names}}'
foreach ($c in $Containers) {
    if ($running -contains $c) { L "ok    $c" ; continue }
    L "start $c"
    $null = & $docker start $c 2>&1
    Start-Sleep -Seconds 2
}

# 3. report final state
Start-Sleep -Seconds 5
$final = & $docker ps --format '{{.Names}}'
foreach ($c in $Containers) {
    if ($final -contains $c) { L "up    $c" } else { L "DOWN  $c" }
}

# 4. mattermost readiness
for ($i = 1; $i -le 24; $i++) {
    try {
        $r = Invoke-RestMethod -Uri 'http://127.0.0.1:8065/api/v4/system/ping' -TimeoutSec 5
        L "mattermost ping: $($r.status)"; break
    } catch { Start-Sleep -Seconds 5 }
}
L "=== boot container check end ==="

$ErrorActionPreference = 'Continue'
$log = 'C:/Users/<user>\tinycmdr\maintenance\ht-enable.log'
function L($m) { "$((Get-Date).ToString('s')) $m" | Add-Content -Path $log }

L "=== HT enable run start (pid $PID) ==="

# 1. password state - does a supervisor password gate the write?
try {
    $pw = Get-CimInstance -Namespace root/wmi -ClassName Lenovo_BiosPasswordSettings
    L ("password settings: State={0} Encoding={1}" -f $pw.PasswordState, $pw.PasswordEncoding)
} catch { L "password settings unavailable: $($_.Exception.Message)" }

# 2. current value
$before = $null
try {
    $before = (Get-CimInstance -Namespace root/wmi -ClassName Lenovo_BiosSetting |
        Where-Object { $_.CurrentSetting -match 'HyperThreadingTechnology' } |
        Select-Object -First 1).CurrentSetting
    L "before: $before"
} catch { L "read failed: $($_.Exception.Message)" }

# 3. set
try {
    $set = Get-CimInstance -Namespace root/wmi -ClassName Lenovo_SetBiosSetting
    $r = Invoke-CimMethod -InputObject $set -MethodName 'SetBiosSetting' -Arguments @{ parameter = 'HyperThreadingTechnology,Enabled' }
    L "SetBiosSetting('HyperThreadingTechnology,Enabled') -> ReturnValue=$($r.ReturnValue) return=$($r.return)"
} catch { L "SetBiosSetting threw: $($_.Exception.Message)" }

# 4. commit
try {
    $sv = Get-CimInstance -Namespace root/wmi -ClassName Lenovo_SaveBiosSettings
    $r2 = Invoke-CimMethod -InputObject $sv -MethodName 'SaveBiosSettings' -Arguments @{ parameter = '' }
    L "SaveBiosSettings('') -> ReturnValue=$($r2.ReturnValue) return=$($r2.return)"
} catch { L "SaveBiosSettings threw: $($_.Exception.Message)" }

# 5. read back
try {
    $after = (Get-CimInstance -Namespace root/wmi -ClassName Lenovo_BiosSetting |
        Where-Object { $_.CurrentSetting -match 'HyperThreadingTechnology' } |
        Select-Object -First 1).CurrentSetting
    L "after(staged): $after"
} catch { L "read-back failed: $($_.Exception.Message)" }

L "=== run end ==="

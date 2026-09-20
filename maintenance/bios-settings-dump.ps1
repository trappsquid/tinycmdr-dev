# Dump every Lenovo BIOS setting exposed over WMI, plus password gate state.
$log = 'C:/Users/<user>\tinycmdr\maintenance\bios-settings-dump.log'
function L($m) { $m | Add-Content -Path $log }
"=== BIOS SETTINGS DUMP $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') (elevated=$((New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) pid=$PID) ===" | Set-Content -Path $log

try {
    $pw = Get-CimInstance -Namespace root/wmi -ClassName Lenovo_BiosPasswordSettings
    L ("PASSWORD: State={0} Encoding={1} MinLen={2} MaxLen={3}" -f $pw.PasswordState, $pw.PasswordEncoding, $pw.MinimumPasswordLength, $pw.MaximumPasswordLength)
} catch { L "PASSWORD: unavailable - $($_.Exception.Message)" }

L "--- SETTINGS ---"
try {
    Get-CimInstance -Namespace root/wmi -ClassName Lenovo_BiosSetting |
        Sort-Object SettingName |
        ForEach-Object { L ("{0} = {1}" -f $_.SettingName, $_.CurrentSetting) }
} catch { L "SETTINGS: read failed - $($_.Exception.Message)" }

L "--- BIOS ELEMENT ---"
try {
    Get-CimInstance -Namespace root/wmi -ClassName Lenovo_BIOSElement |
        Select-Object Version, ReleaseDate, Manufacturer, SerialNumber, BIOSMode |
        Format-List | Out-String | ForEach-Object { L $_ }
} catch { L "BIOSElement failed - $($_.Exception.Message)" }
L "=== end dump ==="

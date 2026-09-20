# the manager box post-flash evidence log: runs at every boot, appends one line.
# Gives proof of BIOS version / HT state even if nobody logs in to read chat.
$log = 'C:/Users/<user>\tinycmdr\maintenance\bios-postflash.log'
try {
    $bios = Get-CimInstance Win32_BIOS
    $cpu  = Get-CimInstance Win32_Processor | Select-Object -First 1
    $os   = Get-CimInstance Win32_OperatingSystem
    $ht   = (Get-CimInstance -Namespace root/wmi -ClassName Lenovo_BiosSetting -ErrorAction SilentlyContinue |
             Where-Object { $_.CurrentSetting -match 'HyperThreadingTechnology' } | Select-Object -First 1).CurrentSetting
    "$((Get-Date).ToString('s')) BOOT: SMBIOS=$($bios.SMBIOSBIOSVersion) biosDate=$($bios.ReleaseDate) cores=$($cpu.NumberOfCores) logical=$($cpu.NumberOfLogicalProcessors) wmiHT='$ht' osBuild=$($os.BuildNumber) lastBoot=$($os.LastBootUpTime)" |
        Add-Content -Path $log
} catch {
    "$((Get-Date).ToString('s')) BOOT: postflash-check failed: $($_.Exception.Message)" | Add-Content -Path $log
}

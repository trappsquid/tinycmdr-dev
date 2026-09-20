
function Try-Map($unc, $user, $pw) {
  try {
    if ($user) { New-SmbMapping -RemotePath $unc -Credential (New-Object PSCredential($user,(ConvertTo-SecureString $pw -AsPlainText -Force))) -ErrorAction Stop | Out-Null }
    else { New-SmbMapping -RemotePath $unc -ErrorAction Stop | Out-Null }
  } catch { "MAP FAIL $unc : " + $_.Exception.Message; return }
  try {
    $items = Get-ChildItem -LiteralPath $unc -ErrorAction Stop | Select-Object -First 6
    "MAP OK $unc  entries=" + (Get-ChildItem -LiteralPath $unc -ErrorAction Stop).Count
    foreach ($i in $items) { "   " + $i.Name }
  } catch { "LIST FAIL $unc : " + $_.Exception.Message }
  finally { try { Remove-SmbMapping -RemotePath $unc -Force -ErrorAction SilentlyContinue } catch {} }
}
$pw = Get-Content -LiteralPath "$env:USERPROFILE\.nas-cred" -Raw -ErrorAction SilentlyContinue
if (-not $pw) { $pw = "" }
$pw = $pw.Trim()
"--- NAS a LAN address\the file share as Trapp ---"
Try-Map "\\a LAN address\the file share" "Trapp" $pw
"--- NAS implicit (no explicit creds) ---"
Try-Map "\\a LAN address\the file share" $null $null
"--- C$ the Windows test box explicit ---"
Try-Map "\\a LAN address\C$" "David Trapp" $pw
"--- C$ the other Windows box explicit ---"
Try-Map "\\a LAN address\C$" "David Trapp" $pw
"--- C$ implicit ---"
Try-Map "\\a LAN address\C$" $null $null

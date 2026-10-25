# Lets phones and other devices on your home Wi-Fi open oTube.
# Adds one Windows Firewall rule: inbound TCP 8700, Private (home) networks only,
# and only from devices on your local network. Run it once (it asks for admin).
$name = 'oTube (port 8700, home network)'

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
         ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
    # Re-launch elevated (Windows shows its permission prompt)
    Start-Process powershell.exe -Verb RunAs -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"")
    exit
}

if (Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue) {
    Write-Host "The rule already exists - nothing to do." -ForegroundColor Green
} else {
    New-NetFirewallRule -DisplayName $name -Direction Inbound -Protocol TCP -LocalPort 8700 `
        -Action Allow -Profile Private -RemoteAddress LocalSubnet | Out-Null
    Write-Host "Done. Devices on your home Wi-Fi can now open oTube." -ForegroundColor Green
}
$ip = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.InterfaceAlias -match 'Wi-Fi|Ethernet' -and $_.IPAddress -notmatch '^169\.' } | Select-Object -First 1).IPAddress
if ($ip) { Write-Host "On your phone open:  http://${ip}:8700" -ForegroundColor Cyan }
Write-Host ""
Read-Host "Press Enter to close"

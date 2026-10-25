# Starts Docker Desktop (if needed), brings oTube up, and opens it in the browser.
$ErrorActionPreference = 'SilentlyContinue'
Set-Location $PSScriptRoot
$url = 'http://localhost:8700'

# Helper for "Show in folder" / "Open in player" (exits at once if already running).
Start-Process powershell.exe -WindowStyle Hidden -ArgumentList @(
    '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSScriptRoot\otube-helper.ps1`"")

function DockerReady { docker info *> $null; return $LASTEXITCODE -eq 0 }

if (-not (DockerReady)) {
    $exe = "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe"
    if (Test-Path $exe) { Start-Process $exe }
    for ($i = 0; $i -lt 90 -and -not (DockerReady); $i++) { Start-Sleep 2 }
    if (-not (DockerReady)) {
        Add-Type -AssemblyName PresentationFramework
        [System.Windows.MessageBox]::Show('Docker did not start. Open Docker Desktop and try again.', 'oTube') | Out-Null
        exit 1
    }
}

# This PC's address on the home network (the adapter that has the default route),
# shown in Settings -> Devices with a QR code for phones.
$route = Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue | Sort-Object RouteMetric, InterfaceMetric | Select-Object -First 1
if ($route) {
    $ip = (Get-NetIPAddress -InterfaceIndex $route.InterfaceIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue | Select-Object -First 1).IPAddress
    if ($ip) {
        $env:OTUBE_LAN_URL = "http://${ip}:8700"
        # .env is read by docker compose on every start, so the address survives rebuilds
        Set-Content -Path (Join-Path $PSScriptRoot '.env') -Value "OTUBE_LAN_URL=$env:OTUBE_LAN_URL" -Encoding ascii
    }
}

docker compose up -d *> $null

for ($i = 0; $i -lt 60; $i++) {
    try { Invoke-WebRequest $url -UseBasicParsing -TimeoutSec 2 | Out-Null; break } catch { Start-Sleep 1 }
}
Start-Process $url

# Starts Docker Desktop (if needed), brings oTube up, and opens it in the browser.
$ErrorActionPreference = 'SilentlyContinue'
Set-Location $PSScriptRoot
$url = 'http://localhost:8700'

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

docker compose up -d *> $null

for ($i = 0; $i -lt 60; $i++) {
    try { Invoke-WebRequest $url -UseBasicParsing -TimeoutSec 2 | Out-Null; break } catch { Start-Sleep 1 }
}
Start-Process $url

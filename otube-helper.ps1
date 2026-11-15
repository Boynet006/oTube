# oTube helper: lets the app (running in Docker) do "Show in Explorer" and
# "Open in player" / GOM mode / bulk delete to the Recycle Bin on this PC. Listens on 127.0.0.1 only, and only ever opens
# Explorer at an existing file/folder or opens an existing *video* file.
$ErrorActionPreference = 'Stop'
$port = 8701
$videoExts = '.mp4','.m4v','.webm','.mov','.ogv','.ogg','.mkv','.avi','.ts','.mts','.m2ts','.m2t',
             '.flv','.f4v','.wmv','.asf','.mpg','.mpeg','.mpe','.m2v','.mp2','.vob','.dat','.3gp',
             '.3g2','.ogm','.divx','.rm','.rmvb','.mxf','.qt','.dv','.y4m'

$listener = New-Object System.Net.HttpListener
$listener.Prefixes.Add("http://127.0.0.1:$port/")
try { $listener.Start() } catch { exit 0 }  # already running

# Drive watchdog: when a drive is unplugged/replugged (or the PC sleeps), Docker Desktop keeps a
# dead mount for it ("No such device") and oTube sees an empty folder. Every 30 s, re-attach any
# drive Windows has but Docker can't read. Docker containers pick it up live (rslave mount).
$null = Start-Job -ScriptBlock {
    while ($true) {
        try {
            $running = (wsl.exe -l --running -q) -join '' -replace "`0", ''
            if ($running -match 'docker-desktop') {
                $letters = ([IO.DriveInfo]::GetDrives() | Where-Object { $_.IsReady -and $_.DriveType -in 'Fixed', 'Removable' } |
                            ForEach-Object { $_.Name.Substring(0, 1).ToLower() }) -join ' '
                $sh = 'R=/tmp/docker-desktop-root; for d in ' + $letters + '; do ' +
                      'mountpoint -q $R/run/desktop/mnt/host/$d && ls $R/run/desktop/mnt/host/$d >/dev/null 2>&1 && continue; ' +
                      'for m in $R/run/desktop/mnt/host/$d $R/mnt/host/$d /mnt/host/$d; do umount -l $m 2>/dev/null; done; ' +
                      'mkdir -p /mnt/host/$d && mount -t drvfs ${d}: /mnt/host/$d -o metadata || continue; ' +
                      'for t in $R/mnt/host/$d $R/run/desktop/mnt/host/$d; do mkdir -p $t && mount --bind /mnt/host/$d $t; done; echo fixed; done'
                if ((wsl.exe -d docker-desktop -u root -e sh -c $sh) -match 'fixed') {   # show it in oTube now
                    try { $null = Invoke-WebRequest -Uri 'http://localhost:8700/api/rescan' -Method POST -UseBasicParsing -TimeoutSec 600 } catch {}
                }
            }
        } catch {}
        Start-Sleep -Seconds 30
    }
}

# Windows won't let a background app (this helper) put a window in front, so Explorer used to open
# behind the browser / minimized. Find the Explorer window showing the folder and bring it forward.
Add-Type -Namespace OTube -Name Win -MemberDefinition @'
[DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
[DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int cmd);
[DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
[DllImport("user32.dll")] public static extern void keybd_event(byte vk, byte scan, uint flags, UIntPtr extra);
'@
function Show-Explorer([string]$folder) {
    $shell = New-Object -ComObject Shell.Application
    for ($i = 0; $i -lt 30; $i++) {   # up to 3 s for the window to appear
        $w = @($shell.Windows()) | Where-Object {
            try { $_.Document.Folder.Self.Path -eq $folder } catch { $false } } | Select-Object -Last 1
        if ($w) {
            $h = [IntPtr]$w.HWND
            if ([OTube.Win]::IsIconic($h)) { [void][OTube.Win]::ShowWindow($h, 9) }   # restore
            [OTube.Win]::keybd_event(0x12, 0, 0, [UIntPtr]::Zero)                      # a key press lifts
            [OTube.Win]::keybd_event(0x12, 0, 2, [UIntPtr]::Zero)                      # the foreground lock
            [void][OTube.Win]::SetForegroundWindow($h)
            return
        }
        Start-Sleep -Milliseconds 100
    }
}

while ($listener.IsListening) {
    $ctx = $listener.GetContext()
    $req = $ctx.Request; $res = $ctx.Response
    $res.AddHeader('Access-Control-Allow-Origin', '*')
    $status = 200
    try {
        if ($req.HttpMethod -ne 'POST') { $status = 204 }
        else {
            $reader = New-Object System.IO.StreamReader($req.InputStream, [System.Text.Encoding]::UTF8)
            $path = $reader.ReadToEnd().Trim() -replace '/', '\'
            if (-not (Test-Path -LiteralPath $path)) { $status = 404 }
            elseif ($req.Url.AbsolutePath -eq '/reveal' -and (Test-Path -LiteralPath $path -PathType Container)) {
                Start-Process explorer.exe -ArgumentList "`"$path`""          # open the folder
                Show-Explorer $path
            }
            elseif (-not (Test-Path -LiteralPath $path -PathType Leaf)) { $status = 404 }
            elseif ($req.Url.AbsolutePath -eq '/reveal') {
                Start-Process explorer.exe -ArgumentList "/select,`"$path`""  # select the file
                Show-Explorer (Split-Path -LiteralPath $path)
            }
            elseif ($req.Url.AbsolutePath -eq '/gom' -and $videoExts -contains [IO.Path]::GetExtension($path).ToLower()) {
                $gom = @('GOM64.EXE', 'GOM.exe') | ForEach-Object {
                    (Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\$_" -ErrorAction SilentlyContinue).'(default)'
                } | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
                if ($gom) { Start-Process -FilePath $gom -ArgumentList "`"$path`"" } else { $status = 404 }
            }
            elseif ($req.Url.AbsolutePath -eq '/recycle' -and $videoExts -contains [IO.Path]::GetExtension($path).ToLower()) {
                # only the oTube page may delete (browsers always send Origin on cross-site POSTs)
                if ($req.Headers['Origin'] -notmatch '^http://(localhost|127\.0\.0\.1):8700$') { $status = 403 }
                else {
                    Add-Type -AssemblyName Microsoft.VisualBasic
                    [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile($path, 'OnlyErrorDialogs', 'SendToRecycleBin')
                }
            }
            elseif ($req.Url.AbsolutePath -eq '/open' -and $videoExts -contains [IO.Path]::GetExtension($path).ToLower()) {
                Invoke-Item -LiteralPath $path
            }
            else { $status = 400 }
        }
    } catch { $status = 500 }
    $res.StatusCode = $status
    $res.Close()
}

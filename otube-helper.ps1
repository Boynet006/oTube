# oTube helper: lets the app (running in Docker) do "Show in folder" and
# "Open in player" on this PC. Listens on 127.0.0.1 only, and only ever opens
# Explorer at an existing file/folder or opens an existing *video* file.
$ErrorActionPreference = 'Stop'
$port = 8701
$videoExts = '.mp4','.m4v','.webm','.mov','.ogv','.ogg','.mkv','.avi','.ts','.mts','.m2ts','.m2t',
             '.flv','.f4v','.wmv','.asf','.mpg','.mpeg','.mpe','.m2v','.mp2','.vob','.dat','.3gp',
             '.3g2','.ogm','.divx','.rm','.rmvb','.mxf','.qt','.dv','.y4m'

$listener = New-Object System.Net.HttpListener
$listener.Prefixes.Add("http://127.0.0.1:$port/")
try { $listener.Start() } catch { exit 0 }  # already running

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
            }
            elseif (-not (Test-Path -LiteralPath $path -PathType Leaf)) { $status = 404 }
            elseif ($req.Url.AbsolutePath -eq '/reveal') {
                Start-Process explorer.exe -ArgumentList "/select,`"$path`""  # select the file
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

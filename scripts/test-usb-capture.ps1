param(
    [string]$DeviceName = 'Logitech Webcam C930e',
    [ValidateRange(4, 60)]
    [int]$DurationSeconds = 10,
    [ValidateSet('libx264', 'h264_nvenc')]
    [string]$Encoder = 'libx264',
    [string]$OutputRoot = (Join-Path $PSScriptRoot '../backend/data/phase0-pilots')
)

$ErrorActionPreference = 'Stop'

function Resolve-MediaTool([string]$Name) {
    $tool = Get-Command $Name -ErrorAction SilentlyContinue
    if ($tool) { return $tool.Source }
    $candidate = Join-Path $env:LOCALAPPDATA "Microsoft/WinGet/Links/$Name.exe"
    if (Test-Path $candidate) { return $candidate }
    throw "$Name not found. Install FFmpeg and reopen the terminal."
}

$ffmpeg = Resolve-MediaTool 'ffmpeg'
$ffprobe = Resolve-MediaTool 'ffprobe'
$outputDirectory = Join-Path $OutputRoot ([guid]::NewGuid().ToString('N'))
$directory = New-Item -ItemType Directory -Path $outputDirectory -Force
if ($directory.PSDrive.Free -lt 512MB) {
    throw 'At least 512 MiB of free space is required for the bounded pilot.'
}
$playlist = (Join-Path $directory.FullName 'index.m3u8').Replace('\', '/')
$segmentPattern = (Join-Path $directory.FullName 'segment-%03d.m4s').Replace('\', '/')
$preset = if ($Encoder -eq 'libx264') { 'veryfast' } else { 'p4' }
$tune = if ($Encoder -eq 'libx264') { 'zerolatency' } else { 'll' }

$captureArguments = @(
    '-hide_banner', '-nostdin', '-n', '-loglevel', 'warning',
    '-f', 'dshow', '-rtbufsize', '64M', '-vcodec', 'mjpeg',
    '-framerate', '15', '-video_size', '1280x720', '-i', "video=$DeviceName",
    '-t', "$DurationSeconds", '-an', '-c:v', $Encoder, '-preset', $preset,
    '-tune', $tune,
    '-pix_fmt', 'yuv420p', '-b:v', '2M', '-maxrate', '2M', '-bufsize', '4M',
    '-g', '30', '-force_key_frames', 'expr:gte(t,n_forced*2)',
    '-f', 'hls', '-hls_time', '2', '-hls_playlist_type', 'event',
    '-hls_segment_type', 'fmp4', '-hls_flags', 'independent_segments+temp_file',
    '-hls_fmp4_init_filename', 'init.mp4', '-hls_segment_filename', $segmentPattern,
    $playlist
)

& $ffmpeg @captureArguments
if ($LASTEXITCODE -ne 0) { throw "USB capture failed (exit $LASTEXITCODE)." }
if (-not ((Get-Content -LiteralPath $playlist) -contains '#EXT-X-ENDLIST')) {
    throw 'The generated HLS playlist was not finalized.'
}

$probeOutput = & $ffprobe -v error -show_streams -show_format -of json $playlist
if ($LASTEXITCODE -ne 0) { throw 'The generated HLS playlist could not be probed.' }
$probe = ($probeOutput -join "`n") | ConvertFrom-Json
$video = @($probe.streams | Where-Object { $_.codec_type -eq 'video' })
$audio = @($probe.streams | Where-Object { $_.codec_type -eq 'audio' })
$duration = [double]::Parse($probe.format.duration, [cultureinfo]::InvariantCulture)
if ($video.Count -ne 1 -or $video[0].codec_name -ne 'h264' -or
    $video[0].width -ne 1280 -or $video[0].height -ne 720 -or
    $audio.Count -ne 0 -or $duration -lt ($DurationSeconds - 1) -or
    $duration -gt ($DurationSeconds + 1)) {
    throw 'Unexpected codec, dimensions, audio track or duration.'
}

& $ffmpeg -hide_banner -nostdin -v error -xerror -i $playlist -map 0:v:0 -f null -
if ($LASTEXITCODE -ne 0) { throw 'Decoding the recorded HLS segments failed.' }

[pscustomobject]@{
    Playlist = $playlist
    Encoder = $Encoder
    DurationSeconds = $duration
    Width = $video[0].width
    Height = $video[0].height
    FrameRate = $video[0].avg_frame_rate
    AudioTracks = $audio.Count
    Segments = @(Get-ChildItem $directory.FullName -Filter '*.m4s').Count
    SizeMiB = [math]::Round((Get-ChildItem $directory.FullName | Measure-Object Length -Sum).Sum / 1MB, 2)
    EndlistVerified = $true
    DecodeVerified = $true
} | ConvertTo-Json
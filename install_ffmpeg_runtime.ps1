param(
  [string]$Url = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip",
  [switch]$Force,
  [switch]$UseExistingZip
)

$ErrorActionPreference = "Stop"

function Write-Step {
  param([string]$Message)
  Write-Host "[install_ffmpeg_runtime] $Message"
}

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$RuntimeDir = Join-Path $Root "runtime"
$InstallDir = Join-Path $RuntimeDir "ffmpeg"
$BinDir = Join-Path $InstallDir "bin"
$FfmpegExe = Join-Path $BinDir "ffmpeg.exe"
$DownloadDir = Join-Path $RuntimeDir "downloads"
$ZipPath = Join-Path $DownloadDir "ffmpeg-runtime.zip"
$ExtractDir = Join-Path $DownloadDir "ffmpeg-extract"

if ((Test-Path $FfmpegExe) -and -not $Force) {
  Write-Step "FFmpeg already exists: $FfmpegExe"
  & $FfmpegExe -version | Select-Object -First 1
  exit 0
}

New-Item -ItemType Directory -Force -Path $DownloadDir | Out-Null
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

if ((Test-Path $ZipPath) -and -not $UseExistingZip) {
  Remove-Item -LiteralPath $ZipPath -Force
}
if (Test-Path $ExtractDir) {
  Remove-Item -LiteralPath $ExtractDir -Recurse -Force
}
if ((Test-Path $InstallDir) -and $Force) {
  Remove-Item -LiteralPath $InstallDir -Recurse -Force
  New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
}

if ((Test-Path $ZipPath) -and $UseExistingZip) {
  Write-Step "Using existing package: $ZipPath"
} else {
  Write-Step "Downloading FFmpeg package"
  Write-Step $Url
  Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $ZipPath
}

Write-Step "Extracting package"
Expand-Archive -LiteralPath $ZipPath -DestinationPath $ExtractDir -Force

$ExtractedFfmpeg = Get-ChildItem -Path $ExtractDir -Recurse -Filter "ffmpeg.exe" | Select-Object -First 1
if (-not $ExtractedFfmpeg) {
  throw "ffmpeg.exe not found inside downloaded archive."
}

$ExtractedRoot = Split-Path -Parent (Split-Path -Parent $ExtractedFfmpeg.FullName)
Write-Step "Installing to $InstallDir"
Get-ChildItem -LiteralPath $ExtractedRoot -Force | ForEach-Object {
  Copy-Item -LiteralPath $_.FullName -Destination $InstallDir -Recurse -Force
}

if (-not (Test-Path $FfmpegExe)) {
  throw "Install finished but ffmpeg.exe was not found at $FfmpegExe"
}

Write-Step "Installed: $FfmpegExe"
& $FfmpegExe -version | Select-Object -First 1

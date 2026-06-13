param(
  [string]$DownloadUrl = "https://github.com/ollama/ollama/releases/latest/download/ollama-windows-amd64.zip",
  [switch]$Force
)

$ErrorActionPreference = "Stop"

function Write-Step {
  param([string]$Message)
  Write-Host "[install_ollama_runtime] $Message"
}

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$RuntimeDir = Join-Path $Root "runtime"
$OllamaDir = Join-Path $RuntimeDir "ollama"
$CacheDir = Join-Path $RuntimeDir "_cache"
$ZipPath = Join-Path $CacheDir "ollama-windows-amd64.zip"
$OllamaExe = Join-Path $OllamaDir "ollama.exe"

New-Item -ItemType Directory -Force -Path $OllamaDir, $CacheDir | Out-Null

if ((Test-Path $OllamaExe) -and -not $Force) {
  Write-Step "Ollama already exists: $OllamaExe"
  Write-Step "Use -Force to redownload and replace it."
  exit 0
}

Write-Step "Downloading Ollama standalone zip"
Write-Step $DownloadUrl
Invoke-WebRequest -Uri $DownloadUrl -OutFile $ZipPath

if ($Force -and (Test-Path $OllamaDir)) {
  Get-ChildItem -LiteralPath $OllamaDir -Force |
    Where-Object { $_.Name -ne ".gitkeep" } |
    Remove-Item -Recurse -Force
}

Write-Step "Extracting to runtime\ollama"
Expand-Archive -LiteralPath $ZipPath -DestinationPath $OllamaDir -Force

if (-not (Test-Path $OllamaExe)) {
  $found = Get-ChildItem -Path $OllamaDir -Recurse -Filter "ollama.exe" | Select-Object -First 1
  if ($found) {
    Copy-Item -LiteralPath $found.FullName -Destination $OllamaExe -Force
  }
}

if (-not (Test-Path $OllamaExe)) {
  throw "ollama.exe was not found after extracting the archive."
}

Write-Step "Installed: $OllamaExe"
Write-Step "Next: run .\start_local.ps1"

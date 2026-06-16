param(
  [string]$PythonExe = "",
  [string]$VoxCPMVersion = "2.0.3",
  [switch]$ForceRecreate
)

$ErrorActionPreference = "Stop"

function Write-Step {
  param([string]$Message)
  Write-Host "[install_voxcpm2] $Message"
}

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

if (-not $PythonExe) {
  $PythonExe = Get-Command python -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -First 1
}
if (-not $PythonExe) {
  throw "Python not found. Install Python 3.10+ or add Python to PATH."
}

$RuntimeDir = Join-Path $Root "runtime\tts"
$VenvDir = Join-Path $RuntimeDir "venv"
$CacheDir = Join-Path $RuntimeDir "cache"
New-Item -ItemType Directory -Force -Path $RuntimeDir, $CacheDir | Out-Null

if ($ForceRecreate -and (Test-Path $VenvDir)) {
  Write-Step "Removing existing venv: $VenvDir"
  Remove-Item -LiteralPath $VenvDir -Recurse -Force
}

if (-not (Test-Path $VenvDir)) {
  Write-Step "Creating venv with system site packages: $VenvDir"
  & $PythonExe -m venv --system-site-packages $VenvDir
}

$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
  throw "Venv python not found: $VenvPython"
}

Write-Step "Upgrading pip"
& $VenvPython -m pip install --progress-bar off --upgrade pip

Write-Step "Installing VoxCPM2 runtime package"
& $VenvPython -m pip install --progress-bar off "voxcpm==$VoxCPMVersion" soundfile

$VoxCPM = Join-Path $VenvDir "Scripts\voxcpm.exe"
if (-not (Test-Path $VoxCPM)) {
  throw "voxcpm.exe was not installed under $VenvDir"
}

Write-Step "VoxCPM2 ready: $VoxCPM"
Write-Step "Model cache directory: $CacheDir"
Write-Step "First synthesis will download model weights if they are not cached."

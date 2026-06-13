param(
  [string]$Model = "qwen3:8b-q4_K_M",
  [int]$Port = 8765,
  [string]$ModelsDir = "",
  [switch]$SkipModelPull,
  [switch]$NoBrowser,
  [switch]$KeepExistingOllama,
  [switch]$KeepExistingWeb
)

$ErrorActionPreference = "Stop"

function Write-Step {
  param([string]$Message)
  Write-Host "[start_local] $Message"
}

function Test-HttpOk {
  param(
    [string]$Url,
    [int]$TimeoutSec = 3
  )
  try {
    $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec $TimeoutSec
    return ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500)
  } catch {
    return $false
  }
}

function Wait-Http {
  param(
    [string]$Url,
    [int]$Seconds = 30
  )
  $deadline = (Get-Date).AddSeconds($Seconds)
  while ((Get-Date) -lt $deadline) {
    if (Test-HttpOk -Url $Url -TimeoutSec 2) {
      return $true
    }
    Start-Sleep -Seconds 1
  }
  return $false
}

function Stop-ListenerOnPort {
  param(
    [int]$TargetPort,
    [string]$Name
  )
  $connections = Get-NetTCPConnection -LocalPort $TargetPort -State Listen -ErrorAction SilentlyContinue
  $processIds = @($connections | Select-Object -ExpandProperty OwningProcess -Unique)
  foreach ($processId in $processIds) {
    if ($processId -and $processId -ne $PID) {
      Write-Step "Stopping existing $Name process on port ${TargetPort}: PID $processId"
      Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
    }
  }
}

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

if (-not $ModelsDir) {
  $ModelsDir = Join-Path $Root "runtime\models"
}
New-Item -ItemType Directory -Force -Path $ModelsDir | Out-Null
$env:OLLAMA_MODELS = $ModelsDir

$BundledOllama = Join-Path $Root "runtime\ollama\ollama.exe"
$OllamaExe = ""
if (Test-Path $BundledOllama) {
  $OllamaExe = $BundledOllama
}
if (-not $OllamaExe) {
  $OllamaExe = Get-Command ollama -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -First 1
}
if (-not $OllamaExe) {
  $InstalledOllama = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
  if (Test-Path $InstalledOllama) {
    $OllamaExe = $InstalledOllama
  }
}

if (-not $OllamaExe) {
  throw "Ollama not found. Install Ollama or put ollama.exe at runtime\ollama\ollama.exe."
}

Write-Step "Ollama: $OllamaExe"
Write-Step "Models: $env:OLLAMA_MODELS"
if (-not $KeepExistingOllama) {
  Stop-ListenerOnPort -TargetPort 11434 -Name "Ollama"
}
if ($KeepExistingOllama -and (Test-HttpOk -Url "http://127.0.0.1:11434/v1/models")) {
  Write-Step "Using existing Ollama service"
} else {
  Write-Step "Starting Ollama service"
  Start-Process -FilePath $OllamaExe -ArgumentList "serve" -WindowStyle Hidden
  if (-not (Wait-Http -Url "http://127.0.0.1:11434/v1/models" -Seconds 45)) {
    throw "Ollama service did not start within 45 seconds."
  }
}

if (-not $SkipModelPull) {
  Write-Step "Checking model: $Model"
  $modelList = & $OllamaExe list
  if (($modelList -join "`n") -notmatch [regex]::Escape($Model)) {
    Write-Step "Model $Model not found. Pulling it now. First download can take a while."
    & $OllamaExe pull $Model
  }
}

$PythonExe = Get-Command python -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -First 1
if (-not $PythonExe) {
  $PythonExe = Get-Command py -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -First 1
}
if (-not $PythonExe) {
  throw "Python not found. Install Python 3.10+ or add Python to PATH."
}

$env:OPENAI_API_KEY = "local"
$env:OPENAI_BASE_URL = "http://127.0.0.1:11434/v1"
$env:OPENAI_MODEL = $Model

$WebUrl = "http://127.0.0.1:$Port"
if (-not $KeepExistingWeb) {
  Stop-ListenerOnPort -TargetPort $Port -Name "web app"
}
if (-not (Test-HttpOk -Url $WebUrl)) {
  Write-Step "Starting web app: $WebUrl"
  $webArgs = @("my_workspace/web_app.py", "--port", "$Port")
  if ((Split-Path -Leaf $PythonExe) -ieq "py.exe") {
    $webArgs = @("-3") + $webArgs
  }
  Start-Process -FilePath $PythonExe -ArgumentList $webArgs -WorkingDirectory $Root -WindowStyle Hidden
  if (-not (Wait-Http -Url $WebUrl -Seconds 30)) {
    throw "Web app did not start within 30 seconds."
  }
}

Write-Step "Web app is ready: $WebUrl"
if (-not $NoBrowser) {
  Start-Process $WebUrl
}

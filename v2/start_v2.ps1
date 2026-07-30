param(
  [int]$Port = 8766,
  [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $Root
Set-Location $RepoRoot

foreach ($Name in @("V2_AGENT_MODEL_EXECUTION_ENABLED", "V2_EXTERNAL_PROVIDER_EXECUTION_ENABLED", "V2_FFMPEG_PATH")) {
  if (-not [Environment]::GetEnvironmentVariable($Name, "Process")) {
    $UserValue = [Environment]::GetEnvironmentVariable($Name, "User")
    if ($UserValue) { [Environment]::SetEnvironmentVariable($Name, $UserValue, "Process") }
  }
}

$FrontendDist = Join-Path $Root "frontend\dist\index.html"
if (-not (Test-Path $FrontendDist)) {
  throw "V2 frontend is not built. Run npm.cmd install and npm.cmd run build in v2\frontend."
}

$Runtime = Join-Path $Root "runtime"
New-Item -ItemType Directory -Force -Path $Runtime | Out-Null

$ExistingApiPids = @(
  Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique
)
foreach ($ExistingApiPid in $ExistingApiPids) {
  try {
    Stop-Process -Id $ExistingApiPid -Force -ErrorAction Stop
  } catch {
    throw "Cannot stop the existing V2 API process $ExistingApiPid on port ${Port}: $($_.Exception.Message)"
  }
}
for ($Index = 0; $Index -lt 50; $Index++) {
  if (-not (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)) {
    break
  }
  Start-Sleep -Milliseconds 100
}
if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
  throw "Port $Port is still occupied after stopping the existing V2 API."
}

$ExistingWorkerPids = @(
  Get-CimInstance Win32_Process |
    Where-Object {
      $_.Name -like "python*" -and
      $_.CommandLine -like "*-m v2.backend.app.workers.worker*"
    } |
    Select-Object -ExpandProperty ProcessId
)
foreach ($ExistingWorkerPid in $ExistingWorkerPids) {
  try {
    Stop-Process -Id $ExistingWorkerPid -Force -ErrorAction Stop
  } catch {
    throw "Cannot stop the existing V2 worker process ${ExistingWorkerPid}: $($_.Exception.Message)"
  }
}

$Python = (Get-Command python).Source
Push-Location (Join-Path $Root "backend")
try {
  & $Python -m alembic upgrade head
  if ($LASTEXITCODE -ne 0) {
    throw "V2 database migration failed."
  }
} finally {
  Pop-Location
}
$ApiProcess = Start-Process -FilePath $Python `
  -ArgumentList "-m", "uvicorn", "v2.backend.app.main:app", "--host", "127.0.0.1", "--port", "$Port" `
  -WorkingDirectory $RepoRoot `
  -RedirectStandardOutput (Join-Path $Runtime "api.out.log") `
  -RedirectStandardError (Join-Path $Runtime "api.err.log") `
  -WindowStyle Hidden `
  -PassThru

$WorkerProcess = Start-Process -FilePath $Python `
  -ArgumentList "-m", "v2.backend.app.workers.worker", "--poll-seconds", "0.5" `
  -WorkingDirectory $RepoRoot `
  -RedirectStandardOutput (Join-Path $Runtime "worker.out.log") `
  -RedirectStandardError (Join-Path $Runtime "worker.err.log") `
  -WindowStyle Hidden `
  -PassThru

$Url = "http://127.0.0.1:$Port"
$Ready = $false
for ($Index = 0; $Index -lt 30; $Index++) {
  if ($ApiProcess.HasExited) {
    break
  }
  Start-Sleep -Milliseconds 500
  try {
    $Response = Invoke-WebRequest -UseBasicParsing "$Url/api/v1/health" -TimeoutSec 2
    $ListenerPid = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop |
      Select-Object -ExpandProperty OwningProcess -First 1
    if ($Response.StatusCode -eq 200 -and $ListenerPid -eq $ApiProcess.Id) {
      $Ready = $true
      break
    }
  } catch {
  }
}

if (-not $Ready) {
  if (-not $ApiProcess.HasExited) {
    Stop-Process -Id $ApiProcess.Id -Force -ErrorAction SilentlyContinue
  }
  if (-not $WorkerProcess.HasExited) {
    Stop-Process -Id $WorkerProcess.Id -Force -ErrorAction SilentlyContinue
  }
  Get-Content (Join-Path $Runtime "api.err.log") -Tail 80
  throw "The newly started V2 API process did not become the healthy listener on port $Port."
}

Write-Host "Agency Studio V2 is ready: $Url (API PID $($ApiProcess.Id), Worker PID $($WorkerProcess.Id))"
if (-not $NoBrowser) {
  Start-Process $Url
}

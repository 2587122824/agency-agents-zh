param(
  [int]$Port = 8766,
  [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $Root
Set-Location $RepoRoot

$FrontendDist = Join-Path $Root "frontend\dist\index.html"
if (-not (Test-Path $FrontendDist)) {
  throw "V2 frontend is not built. Run npm.cmd install and npm.cmd run build in v2\frontend."
}

$Runtime = Join-Path $Root "runtime"
New-Item -ItemType Directory -Force -Path $Runtime | Out-Null

Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
  Select-Object -ExpandProperty OwningProcess -Unique |
  ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }

Get-CimInstance Win32_Process |
  Where-Object {
    $_.Name -like "python*" -and
    $_.CommandLine -like "*-m v2.backend.app.workers.worker*"
  } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

$Python = (Get-Command python).Source
Start-Process -FilePath $Python `
  -ArgumentList "-m", "uvicorn", "v2.backend.app.main:app", "--host", "127.0.0.1", "--port", "$Port" `
  -WorkingDirectory $RepoRoot `
  -RedirectStandardOutput (Join-Path $Runtime "api.out.log") `
  -RedirectStandardError (Join-Path $Runtime "api.err.log") `
  -WindowStyle Hidden

Start-Process -FilePath $Python `
  -ArgumentList "-m", "v2.backend.app.workers.worker", "--poll-seconds", "0.5" `
  -WorkingDirectory $RepoRoot `
  -RedirectStandardOutput (Join-Path $Runtime "worker.out.log") `
  -RedirectStandardError (Join-Path $Runtime "worker.err.log") `
  -WindowStyle Hidden

$Url = "http://127.0.0.1:$Port"
$Ready = $false
for ($Index = 0; $Index -lt 30; $Index++) {
  Start-Sleep -Milliseconds 500
  try {
    $Response = Invoke-WebRequest -UseBasicParsing "$Url/api/v1/health" -TimeoutSec 2
    if ($Response.StatusCode -eq 200) {
      $Ready = $true
      break
    }
  } catch {
  }
}

if (-not $Ready) {
  Get-Content (Join-Path $Runtime "api.err.log") -Tail 80
  throw "V2 service did not become ready on port $Port."
}

Write-Host "Agency Studio V2 is ready: $Url"
if (-not $NoBrowser) {
  Start-Process $Url
}

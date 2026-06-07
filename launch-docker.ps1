#Requires -Version 5.1
<#
  Odysseus - Docker launcher (recommended).

  One command to: stop any native instance on the app port, restart the Docker
  stack, wait until the server responds, and open the browser with the IDE
  layout ready (file tree, editor, terminal).

  Usage:
    powershell -ExecutionPolicy Bypass -File .\launch-docker.ps1
    powershell -ExecutionPolicy Bypass -File .\launch-docker.ps1 -Port 7000 -NoBrowser

  Native Windows (no Docker): use start-native.bat instead.
#>
param(
    [int]$Port = 0,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Write-Step($msg) { Write-Host ""; Write-Host ("==> " + $msg) -ForegroundColor Cyan }
function Fail($msg) {
    Write-Host ""
    Write-Host ("ERROR: " + $msg) -ForegroundColor Red
    Write-Host ""
    Read-Host "Press Enter to exit"
    exit 1
}

function Read-EnvPort {
    param([int]$Default = 7000)
    $envFile = Join-Path $PSScriptRoot ".env"
    if (-not (Test-Path $envFile)) { return $Default }
    foreach ($line in Get-Content $envFile -ErrorAction SilentlyContinue) {
        if ($line -match '^\s*APP_PORT\s*=\s*(\d+)\s*$') { return [int]$Matches[1] }
    }
    return $Default
}

function Stop-NativeListenersOnPort {
    param([int]$TargetPort)
    try {
        $conns = @(Get-NetTCPConnection -LocalPort $TargetPort -State Listen -ErrorAction SilentlyContinue)
        foreach ($c in $conns) {
            $proc = Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue
            if (-not $proc) { continue }
            $name = $proc.ProcessName.ToLower()
            if ($name -match '^(python|uvicorn)$') {
                Write-Host ("Stopping native {0} (PID {1}) on port {2}" -f $proc.ProcessName, $proc.Id, $TargetPort) -ForegroundColor Yellow
                Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
            }
        }
    } catch {
        # Get-NetTCPConnection may be unavailable on some editions; ignore.
    }
}

function Invoke-DockerCompose {
    param(
        [string[]]$ComposeArgs,
        [switch]$Quiet
    )
    # Docker writes progress to stderr; with $ErrorActionPreference Stop that becomes a false failure.
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        if ($Quiet) {
            & docker compose @ComposeArgs *>&1 | Out-Null
        } else {
            & docker compose @ComposeArgs
        }
        if ($LASTEXITCODE -ne 0) {
            throw "docker compose $($ComposeArgs -join ' ') failed (exit $LASTEXITCODE)"
        }
    } finally {
        $ErrorActionPreference = $prevEap
    }
}

function Wait-ForHttp {
    param([string]$Url, [int]$TimeoutSec = 180)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    $dots = 0
    while ((Get-Date) -lt $deadline) {
        try {
            $resp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 8 -ErrorAction Stop
            if ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 500) { return $true }
        } catch {
            $dots = ($dots + 1) % 4
            $pad = '.' * $dots
            Write-Host ("  Waiting for server{0}   " -f $pad) -NoNewline
            Write-Host "`r" -NoNewline
            Start-Sleep -Seconds 2
        }
    }
    return $false
}

if ($Port -le 0) { $Port = Read-EnvPort }

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Fail @(
        "Docker is not installed or not on PATH.",
        "",
        "Install Docker Desktop for Windows, then re-run start.bat.",
        "For native Windows (no Docker), use start-native.bat instead."
    ) -join [Environment]::NewLine
}

$composeFile = Join-Path $PSScriptRoot "docker-compose.yml"
if (-not (Test-Path $composeFile)) {
    Fail "docker-compose.yml not found in $PSScriptRoot"
}

Write-Host ""
Write-Host "=========================================" -ForegroundColor Cyan
Write-Host " Odysseus (Docker)" -ForegroundColor Cyan
Write-Host "=========================================" -ForegroundColor Cyan

Write-Step "Stopping any native instance on port $Port"
Stop-NativeListenersOnPort $Port

Write-Step "Restarting Odysseus container"
try {
    Invoke-DockerCompose -Quiet -ComposeArgs @('stop', 'odysseus')
} catch {
    # First run — container may not exist yet; up -d will create it.
}

Write-Step "Starting Docker stack (this may take a minute on first run)"
try {
    Invoke-DockerCompose -ComposeArgs @('up', '-d', '--remove-orphans')
} catch {
    Fail "docker compose up failed. Is Docker Desktop running?"
}

$url = "http://127.0.0.1:$Port"
Write-Step "Waiting for Odysseus at $url"
if (-not (Wait-ForHttp $url)) {
    Fail @(
        "Odysseus did not respond in time.",
        "",
        "Check container logs:",
        "  docker compose logs -f odysseus"
    ) -join [Environment]::NewLine
}

Write-Host ""
Write-Host "Odysseus is running at $url" -ForegroundColor Green
Write-Host "Workspace: /workspace (your Desktop is mounted there in the container)." -ForegroundColor DarkGray
Write-Host ""

if (-not $NoBrowser) {
    try { Start-Process $url } catch { Write-Host "Open $url in your browser." -ForegroundColor Yellow }
}

Write-Host "To stop:  docker compose down" -ForegroundColor DarkGray
Write-Host "Logs:     docker compose logs -f odysseus" -ForegroundColor DarkGray
Write-Host ""

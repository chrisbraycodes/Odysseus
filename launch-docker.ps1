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
    if (-not $NoBrowser -and [Environment]::UserInteractive) {
        Read-Host "Press Enter to exit"
    }
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

function Ensure-EnvFile {
    $envFile = Join-Path $PSScriptRoot ".env"
    $example = Join-Path $PSScriptRoot ".env.example"
    if (-not (Test-Path $envFile)) {
        if (-not (Test-Path $example)) {
            Fail "Missing .env and no .env.example to copy from."
        }
        Copy-Item $example $envFile
        Write-Host "Created .env from .env.example" -ForegroundColor Yellow
    }

    $lines = @(Get-Content $envFile -ErrorAction SilentlyContinue)
    if (-not ($lines | Where-Object { $_ -match '^\s*COMPOSE_PROJECT_NAME\s*=' })) {
        Add-Content -Path $envFile -Value "`nCOMPOSE_PROJECT_NAME=odysseus"
        Write-Host "Set COMPOSE_PROJECT_NAME=odysseus in .env (stable Docker project name)" -ForegroundColor Yellow
    }
    Ensure-HostAgentToken -EnvFile $envFile
}

function Ensure-HostAgentToken {
    param([string]$EnvFile)
    $lines = @(Get-Content $EnvFile -ErrorAction SilentlyContinue)
    $existing = $lines | Where-Object { $_ -match '^\s*WORKSPACE_HOST_AGENT_TOKEN\s*=\s*(\S+)\s*$' } | Select-Object -First 1
    if ($existing -and ($existing -replace '^\s*WORKSPACE_HOST_AGENT_TOKEN\s*=\s*', '').Trim()) {
        return
    }
    $token = [guid]::NewGuid().ToString('N')
    Add-Content -Path $EnvFile -Value "WORKSPACE_HOST_AGENT_TOKEN=$token"
    Write-Host "Generated WORKSPACE_HOST_AGENT_TOKEN in .env" -ForegroundColor Yellow
}

function Ensure-HostAgentVenv {
    # Kept for backwards compatibility; start-host-agent.bat owns venv setup now.
    $bat = Join-Path $PSScriptRoot "start-host-agent.bat"
    if (Test-Path $bat) { return }
    $venv = Join-Path $PSScriptRoot ".host-agent-venv"
    $python = Join-Path $venv "Scripts\python.exe"
    if (-not (Test-Path $python)) {
        Write-Host "Creating host agent virtualenv..." -ForegroundColor Yellow
        & python -m venv $venv
        if ($LASTEXITCODE -ne 0) { Fail "Could not create .host-agent-venv - install Python 3." }
    }
    $req = Join-Path $PSScriptRoot "scripts\host_agent_requirements.txt"
    & $python -m pip install -q -r $req
    if ($LASTEXITCODE -ne 0) { Fail "Could not install host agent dependencies." }
}

function Start-HostAgent {
    $bat = Join-Path $PSScriptRoot "start-host-agent.bat"
    if (-not (Test-Path $bat)) {
        Write-Host "Warning: missing start-host-agent.bat - host terminal disabled" -ForegroundColor Yellow
        return $false
    }
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    # Invoke via cmd /c (not Start-Process -Wait -NoNewWindow) so a detached pythonw child
    # does not keep the launcher blocked on the shared console.
    & cmd.exe /c "`"$bat`" -Restart -NoPause"
    $exitCode = $LASTEXITCODE
    $sw.Stop()
    if ($exitCode -eq 0) {
        Write-Host ("Windows host agent ready ({0} ms)" -f $sw.ElapsedMilliseconds) -ForegroundColor Green
        return $true
    }
    Write-Host ("Warning: host agent did not start (exit {0}, {1} ms) - see logs/host-agent.log" -f $exitCode, $sw.ElapsedMilliseconds) -ForegroundColor Yellow
    return $false
}

function Ensure-RuntimeDirs {
    $dirs = @(
        "data",
        "logs",
        "workspace",
        "data\ssh",
        "data\huggingface",
        "data\local"
    )
    foreach ($relative in $dirs) {
        $path = Join-Path $PSScriptRoot $relative
        if (-not (Test-Path $path)) {
            New-Item -ItemType Directory -Path $path -Force | Out-Null
        }
    }

    $searxngSettings = Join-Path $PSScriptRoot "config\searxng\settings.yml"
    if (-not (Test-Path $searxngSettings)) {
        Fail "Missing $searxngSettings - re-clone the repo or restore config/searxng/settings.yml."
    }
    if ((Get-Item $searxngSettings).PSIsContainer) {
        Fail @(
            "$searxngSettings is a directory, not a file.",
            "This usually means Docker created a placeholder after a bad bind mount.",
            "Remove that folder and restore the real settings.yml from the repo."
        ) -join [Environment]::NewLine
    }
}

function Test-DockerReady {
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & docker info *>&1 | Out-Null
        return $LASTEXITCODE -eq 0
    } finally {
        $ErrorActionPreference = $prevEap
    }
}

function Stop-ConflictingStackContainers {
    param([int[]]$Ports)
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        foreach ($port in $Ports) {
            $names = @(docker ps --format "{{.Names}}" --filter "publish=$port" 2>$null)
            foreach ($name in $names) {
                if ($name -match '(?i)(odysseus|searxng|chromadb|ntfy)') {
                    Write-Host ("Stopping stale container on port {0}: {1}" -f $port, $name) -ForegroundColor Yellow
                    docker stop $name 2>$null | Out-Null
                }
            }
        }
    } finally {
        $ErrorActionPreference = $prevEap
    }
}

function Get-RunningComposeServices {
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $lines = @(docker compose ps --services --filter status=running 2>$null)
        return @($lines | Where-Object { $_ })
    } finally {
        $ErrorActionPreference = $prevEap
    }
}

function Wait-ForAllServices {
    param(
        [string[]]$Services,
        [int]$TimeoutSec = 180
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        $running = Get-RunningComposeServices
        $missing = @($Services | Where-Object { $running -notcontains $_ })
        if ($missing.Count -eq 0) { return $true }
        Start-Sleep -Seconds 2
    }
    return $false
}

function Ensure-ComposeStack {
    param([string[]]$Services)

    Write-Step "Starting Docker stack (first run may take several minutes while the image builds)"
    Invoke-DockerCompose -ComposeArgs @('up', '-d', '--build', '--remove-orphans')

    if (Wait-ForAllServices -Services $Services) { return }

    Write-Host ""
    Write-Host "Some services did not start; retrying with force-recreate..." -ForegroundColor Yellow
    Invoke-DockerCompose -ComposeArgs @('up', '-d', '--build', '--force-recreate', '--remove-orphans')

    if (-not (Wait-ForAllServices -Services $Services -TimeoutSec 120)) {
        $running = Get-RunningComposeServices
        $missing = @($Services | Where-Object { $running -notcontains $_ })
        Fail @(
            "Not all Odysseus services are running.",
            "Missing: $($missing -join ', ')",
            "",
            "Check logs:",
            "  docker compose logs --tail=80 odysseus",
            "  docker compose logs --tail=80 searxng",
            "  docker compose ps -a"
        ) -join [Environment]::NewLine
    }
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

if (-not (Test-DockerReady)) {
    Fail @(
        "Docker Desktop is not running or not ready yet.",
        "",
        "Start Docker Desktop, wait until it shows Running, then re-run start.bat."
    ) -join [Environment]::NewLine
}

$composeFile = Join-Path $PSScriptRoot "docker-compose.yml"
if (-not (Test-Path $composeFile)) {
    Fail "docker-compose.yml not found in $PSScriptRoot"
}

Ensure-EnvFile
Ensure-RuntimeDirs
Write-Step "Starting Windows host agent"
Start-HostAgent | Out-Null

$requiredServices = @('odysseus', 'chromadb', 'searxng', 'ntfy')
$stackPorts = @(7000, 8080, 8100, 8091)
if ($Port -gt 0 -and $Port -ne 7000) {
    $stackPorts = @($Port) + ($stackPorts | Where-Object { $_ -ne 7000 })
}

Write-Host ""
Write-Host "=========================================" -ForegroundColor Cyan
Write-Host " Odysseus (Docker)" -ForegroundColor Cyan
Write-Host "=========================================" -ForegroundColor Cyan

Write-Step "Stopping any native instance on port $Port"
Stop-NativeListenersOnPort $Port

Write-Step "Clearing stale Odysseus containers that may block ports"
Stop-ConflictingStackContainers -Ports $stackPorts

Write-Step "Restarting Odysseus container"
try {
    Invoke-DockerCompose -Quiet -ComposeArgs @('stop', 'odysseus')
} catch {
    # First run - container may not exist yet; up -d will create it.
}

try {
    Ensure-ComposeStack -Services $requiredServices
} catch {
    Fail "docker compose up failed. Is Docker Desktop running?`n$($_.Exception.Message)"
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
Write-Host "Enable the Windows host terminal from chat for npm/dev servers on your computer." -ForegroundColor DarkGray
Write-Host ""

Write-Step "Ensuring Windows host agent is running"
Start-HostAgent | Out-Null

if (-not $NoBrowser) {
    try { Start-Process $url } catch { Write-Host "Open $url in your browser." -ForegroundColor Yellow }
}

Write-Host "To stop:  docker compose down" -ForegroundColor DarkGray
Write-Host "Logs:     docker compose logs -f odysseus" -ForegroundColor DarkGray
Write-Host ""

param(
    [int]$Port = 8100,
    [string]$ServerHost = '127.0.0.1',
    [switch]$NoReload
)

# Run from the repository root: app.main is imported as a package and the
# runs/ and docs/ folders resolve relative to it.
Set-Location -Path $PSScriptRoot

$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    Write-Host 'No virtual environment found. Creating one...' -ForegroundColor Yellow
    python -m venv .venv
    & $python -m pip install --upgrade pip
    & $python -m pip install -r requirements.txt
}

if (-not (Test-Path (Join-Path $PSScriptRoot '.env'))) {
    Write-Host '.env not found — starting unconfigured.' -ForegroundColor Yellow
    Write-Host 'The console will load and explain what each capability needs.' -ForegroundColor DarkGray
    Write-Host 'Copy .env.example to .env, or run ./scripts/write-env.ps1 after azd up.' -ForegroundColor DarkGray
    Write-Host ''
}

# Fail fast and say who has the port. uvicorn's own message is
# "[Errno 10048] ... only one usage of each socket address", which does not
# name the owner — and when it is started detached, that error scrolls past
# and the app simply appears not to be running.
$listener = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
    Select-Object -First 1
if ($listener) {
    $owner = Get-Process -Id $listener.OwningProcess -ErrorAction SilentlyContinue
    $name = if ($owner) { "$($owner.ProcessName) (pid $($owner.Id))" } else { "pid $($listener.OwningProcess)" }
    Write-Host "Port $Port is already in use by $name." -ForegroundColor Red
    Write-Host "Start on another port instead:  .\run.ps1 -Port 8101" -ForegroundColor Yellow
    Write-Host ''
    exit 1
}

$arguments = @(
    '-m', 'uvicorn', 'app.main:app',
    '--host', $ServerHost,
    '--port', $Port
)
if (-not $NoReload) {
    # Narrow the watcher: reloading on every change under runs/ would restart
    # the server each time a run is saved, killing any job still in flight.
    $arguments += @('--reload', '--reload-dir', 'app')
}

Write-Host "Document PII Redaction Studio  ->  http://${ServerHost}:${Port}" -ForegroundColor Cyan
& $python @arguments

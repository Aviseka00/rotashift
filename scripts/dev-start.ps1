# First-time or daily dev start: ensure .env exists, then run Docker Compose.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$envFile = Join-Path $root ".env"
$example = Join-Path $root ".env.example"
if (-not (Test-Path $envFile)) {
    if (-not (Test-Path $example)) {
        Write-Error ".env.example is missing. Cannot create .env."
    }
    Copy-Item $example $envFile
    Write-Host "Created .env from .env.example — edit MONGO_URI if you use MongoDB Atlas (shared data on every PC)."
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Error "Docker is not installed. Install Docker Desktop: https://www.docker.com/products/docker-desktop/"
}

Write-Host "Building and starting RotaShift (detached)..."
docker compose up -d --build
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$port = "8000"
if (Test-Path $envFile) {
    foreach ($line in Get-Content $envFile) {
        if ($line -match '^\s*ROTASHIFT_PORT\s*=\s*(\d+)') {
            $port = $Matches[1]
            break
        }
    }
}

Write-Host ""
Write-Host "RotaShift is running."
Write-Host "  App:    http://localhost:$port"
Write-Host "  Health: http://localhost:$port/health/live"
Write-Host "  Meta:   http://localhost:$port/api/meta/registration"
Write-Host ""
Write-Host "Stop:  docker compose down"
Write-Host "Logs:  docker compose logs -f api"

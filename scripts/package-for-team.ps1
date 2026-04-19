# Creates rotashift-team-test.zip from the current commit (no .git folder, respects .gitattributes export-ignore if any).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Error "Git is required. Install Git for Windows, then run this script again."
}
$zip = Join-Path $root "rotashift-team-test.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
git archive --format=zip -o $zip HEAD
Write-Host "Created: $zip"
Write-Host "Share this zip with your team. They should unzip, open README.md, and run: docker compose up --build"

# Installs the Arckenites VM Lab Agent as a Windows Service.
# Run this in an elevated (Administrator) PowerShell prompt, on the lab VM
# itself — never on a development machine.
#
# UNVERIFIED ON REAL HARDWARE: this script has not yet been run against a
# real Windows lab VM. Read it before running it.

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

if (-not (Test-Path "config.ini")) {
    Write-Host "config.ini not found." -ForegroundColor Yellow
    Write-Host "Copy config.ini.example to config.ini and fill in this VM's real agent token first."
    exit 1
}

Write-Host "Installing Python dependencies..."
python -m pip install -r requirements.txt

Write-Host "Registering the Windows Service..."
python service.py --startup auto install

Write-Host "Starting the service..."
python service.py start

Write-Host ""
Write-Host "Done. Check status with:  python service.py status" -ForegroundColor Green
Write-Host "Logs are written to:      $ScriptDir\agent.log"

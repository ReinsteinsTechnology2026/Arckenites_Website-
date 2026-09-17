# Stops and removes the Arckenites VM Lab Agent Windows Service.
# Run this in an elevated (Administrator) PowerShell prompt.
#
# This does NOT modify any local group membership or log anyone off —
# it only removes the service itself. If a student currently has RDP
# group membership granted by the agent, remove it manually afterward
# if you no longer want this VM enforcing anything.

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

Write-Host "Stopping the service..."
try { python service.py stop } catch { Write-Host "(service was not running)" }

Write-Host "Removing the service..."
python service.py remove

Write-Host "Done." -ForegroundColor Green

@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\restore-lite-backup.ps1" -BackupPath "%~1"
if errorlevel 1 pause

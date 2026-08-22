@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\check-packaged-lite.ps1"
set "GABAY_CHECK_EXIT=%ERRORLEVEL%"
echo.
if not "%GABAY_CHECK_EXIT%"=="0" echo GABAY Lite acceptance did not pass. Review the report and messages above.
pause
exit /b %GABAY_CHECK_EXIT%

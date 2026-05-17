@echo off
setlocal
cd /d "%~dp0"

echo [Overfield] Refreshing local live env from launcher and player logs...
py -3 -m AutoShopGather.refresh_live_env

if errorlevel 1 (
  echo.
  echo [Overfield] Refresh failed.
  pause
  exit /b 1
)

echo.
echo [Overfield] Refresh complete.
pause

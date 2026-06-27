@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [SETUP] Python environment is being created...
  py -3.12 -m venv .venv
  call ".venv\Scripts\python.exe" -m pip install --upgrade pip
  call ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo Setup failed.
    pause
    exit /b 1
  )
)

echo.
echo [RESTART] Stopping existing monitor processes...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$root = (Resolve-Path '.').Path; " ^
  "$selfPid = $PID; " ^
  "$rootEsc = [regex]::Escape($root); " ^
  "$targets = @(); " ^
  "$targets += Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique; " ^
  "$targets += Get-CimInstance Win32_Process | Where-Object { " ^
  "  $_.CommandLine -and $_.ProcessId -ne $selfPid -and (" ^
  "    ($_.CommandLine -match 'app\.py' -and $_.CommandLine -match $rootEsc) -or " ^
  "    ($_.CommandLine -match '(^|[\\/])\.venv[\\/]+Scripts[\\/]+python\.exe.*app\.py') -or " ^
  "    ($_.CommandLine -match 'nohup\.exe.*app\.py')" ^
  "  )" ^
  "} | Select-Object -ExpandProperty ProcessId; " ^
  "$targets = $targets | Where-Object { $_ } | Sort-Object -Unique; " ^
  "if ($targets.Count -eq 0) { Write-Output '[RESTART] No existing monitor process found.' } else { " ^
  "  foreach ($procId in $targets) { " ^
  "    try { Stop-Process -Id $procId -Force -ErrorAction Stop; Write-Output ('[RESTART] Stopped PID ' + $procId) } " ^
  "    catch { Write-Output ('[RESTART] Failed to stop PID ' + $procId + ': ' + $_.Exception.Message) } " ^
  "  } " ^
  "} "

echo.
echo Open the Rakuten RSS workbook in Excel before using this monitor.
echo Local URL: http://127.0.0.1:8765
echo.
call ".venv\Scripts\python.exe" app.py
pause

@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul

set "SCRIPT_DIR=%~dp0"
if not exist "%SCRIPT_DIR%logs" mkdir "%SCRIPT_DIR%logs"
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmmss"') do set "LOG_STAMP=%%i"
set "LOG_FILE=%SCRIPT_DIR%logs\generate-testcases-%LOG_STAMP%.log"
echo Log file: %LOG_FILE%

set "USER_ARGS=%*"
if "%~1"=="" (
  echo.
  set /p "PRD_INPUT=Please input PRD file path, scene, category, or All: "
  if "!PRD_INPUT!"=="" (
    echo No input provided.
    echo No input provided. > "%LOG_FILE%"
    echo Press any key to close...
    pause >nul
    exit /b 1
  )
  set "USER_ARGS=--prd-path "!PRD_INPUT!""
)

where python >nul 2>nul
if %errorlevel%==0 (
  if "%~1"=="" (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "& { python '%SCRIPT_DIR%scripts\generate_harmonyrun_testcases.py' @args 2>&1 | Tee-Object -FilePath '%LOG_FILE%'; exit $LASTEXITCODE }" -- --prd-path "!PRD_INPUT!"
  ) else (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "& { python '%SCRIPT_DIR%scripts\generate_harmonyrun_testcases.py' @args 2>&1 | Tee-Object -FilePath '%LOG_FILE%'; exit $LASTEXITCODE }" -- %*
  )
) else (
  if "%~1"=="" (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "& { py -3 '%SCRIPT_DIR%scripts\generate_harmonyrun_testcases.py' @args 2>&1 | Tee-Object -FilePath '%LOG_FILE%'; exit $LASTEXITCODE }" -- --prd-path "!PRD_INPUT!"
  ) else (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "& { py -3 '%SCRIPT_DIR%scripts\generate_harmonyrun_testcases.py' @args 2>&1 | Tee-Object -FilePath '%LOG_FILE%'; exit $LASTEXITCODE }" -- %*
  )
)
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
  echo.
  echo Generation failed with exit code %EXIT_CODE%.
  echo See log file:
  echo   %LOG_FILE%
  echo Look for lines starting with ERROR or "Batch summary".
  echo Press any key to close...
  pause >nul
)

endlocal

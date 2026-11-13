@echo off
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
set "LA_PROJECT=%~dp0"
if "%LA_PROJECT:~-1%"=="\" set "LA_PROJECT=%LA_PROJECT:~0,-1%"
if not defined LA_WORKDIR set "LA_WORKDIR=%CD%"
set "PYTHONPYCACHEPREFIX=%LA_PROJECT%\cache\python"
if not exist "%PYTHONPYCACHEPREFIX%" mkdir "%PYTHONPYCACHEPREFIX%" >nul 2>&1
cd /d "%LA_PROJECT%"

set "PYTHON=%LA_PROJECT%\.venv\Scripts\python.exe"
if not exist "%PYTHON%" (
  echo [ERROR] Chua cai moi truong Python.
  echo         Chay setup.bat trong %LA_PROJECT% de cai lai.
  exit /b 1
)

"%PYTHON%" -m app.cli %*

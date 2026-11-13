@echo off
setlocal EnableExtensions
chcp 65001 >nul 2>&1
title local-agents - setup
cd /d "%~dp0"

set "LA_SETUP_BAT=%~f0"
set "LA_SETUP_ARGS=%*"
set "LA_SETUP_PS1=%~dp0scripts\setup.ps1"

if not exist "%LA_SETUP_PS1%" (
    echo.
    echo [ERROR] Khong tim thay file scripts\setup.ps1
    echo         Hay chay lai setup.bat tu thu muc goc cua project.
    echo.
    pause
    exit /b 1
)

REM --- chay lai voi quyen Administrator -------------------------------------
fltmc >nul 2>&1
if errorlevel 1 (
    echo.
    echo [*] Script chay khong co quyen Administrator - dang mo lai voi quyen Admin...
    echo.
    powershell -NoProfile -ExecutionPolicy Bypass -Command "$a=$env:LA_SETUP_ARGS; if($a){Start-Process -FilePath $env:LA_SETUP_BAT -ArgumentList $a -Verb RunAs}else{Start-Process -FilePath $env:LA_SETUP_BAT -Verb RunAs}"
    exit /b 0
)

REM --- dam bao scripts\setup.ps1 co BOM UTF-8 (PowerShell 5.1 tu doc BOM) ----
powershell -NoProfile -ExecutionPolicy Bypass -Command "$p=$env:LA_SETUP_PS1; try{$r=New-Object System.Text.UTF8Encoding($false);$b=New-Object System.Text.UTF8Encoding($true);$t=[IO.File]::ReadAllText($p,$r);[IO.File]::WriteAllText($p,$t,$b)}catch{}"

powershell -NoProfile -ExecutionPolicy Bypass -File "%LA_SETUP_PS1%" %*
set "RC=%ERRORLEVEL%"

echo.
if "%RC%"=="0" (
    echo [DONE] Setup thanh cong. Xem setup-report.txt de xem chi tiet.
) else (
    echo [WARN] Setup ket thuc co loi ^(exit code %RC%^). Xem setup-report.txt va data\setup.log.
)
echo.
pause
exit /b %RC%

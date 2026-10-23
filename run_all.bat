@echo off
setlocal EnableDelayedExpansion
rem ------------------------------------------------------------------
rem run_all.bat - double-click to run the whole experiment inside WSL.
rem
rem The repository must live inside the WSL file system (e.g. ~/repo).
rem Open it from Windows Explorer as \\wsl$\<distro>\home\...\repo and
rem double-click this file, OR set WSL_REPO_DIR below to the Linux path.
rem Optional: set REPS (default 100).
rem ------------------------------------------------------------------
set "REPS=100"
set "WSL_REPO_DIR="

where wsl.exe >nul 2>nul
if errorlevel 1 (
    echo WSL is not installed or not on PATH.
    pause
    exit /b 1
)

set "HERE=%~dp0"
if "%HERE:~-1%"=="\" set "HERE=%HERE:~0,-1%"
set "DISTRO="

if not "%WSL_REPO_DIR%"=="" goto :run

rem Detect a \\wsl$\<distro>\... or \\wsl.localhost\<distro>\... location.
set "P=%HERE%"
if /i "%P:~0,6%"=="\\wsl$" (
    set "P=!P:~7!"
) else if /i "%P:~0,15%"=="\\wsl.localhost" (
    set "P=!P:~16!"
) else (
    echo This folder is not inside the WSL file system:
    echo   %HERE%
    echo Move the repository into WSL ^(e.g. ~/repo^) or set WSL_REPO_DIR in this file.
    pause
    exit /b 1
)
for /f "tokens=1* delims=\" %%a in ("!P!") do (
    set "DISTRO=%%a"
    set "LINUXPATH=%%b"
)
set "LINUXPATH=/!LINUXPATH:\=/!"
set "WSL_REPO_DIR=!LINUXPATH!"

:run
echo Repository (WSL): %WSL_REPO_DIR%
if not "%DISTRO%"=="" (
    set "DISTRO_ARG=-d %DISTRO%"
) else (
    set "DISTRO_ARG="
)
echo Starting; this window shows live progress. Do not close it.
echo.
wsl.exe %DISTRO_ARG% -- bash -lic "cd '%WSL_REPO_DIR%' && if [ -d venv ]; then source venv/bin/activate; fi; python3 run_all.py --reps %REPS%"
set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" (
    echo Finished successfully. The zip file to send back is listed above.
) else (
    echo Finished with errors ^(exit code %RC%^). See run.log in the results folder.
)
pause
exit /b %RC%

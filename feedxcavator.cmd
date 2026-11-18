@echo off
rem Starts Feedxcavator from this directory and starts it again when it exits with code 3.
setlocal
cd /d "%~dp0"
set "PATH=%~dp0;%PATH%"
set FEEDXCAVATOR_SUPERVISED=1
set "PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON%" set PYTHON=python

:run
"%PYTHON%" -m feedxcavator run %*
if %ERRORLEVEL% EQU 3 goto run
exit /b %ERRORLEVEL%

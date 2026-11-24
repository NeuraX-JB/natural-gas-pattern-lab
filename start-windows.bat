@echo off
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
    python -m gaslab demo
) else (
    py -3 -m gaslab demo
)
if errorlevel 1 pause

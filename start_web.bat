@echo off
title StreamCap Web Launcher
echo Starting StreamCap in Web Mode...

:: Check if python is installed
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo Python is not installed or not in your PATH.
    echo Please install Python 3.10+ and try again.
    pause
    exit /b
)

:: Run the application in web mode
python main.py --web

if %errorlevel% neq 0 (
    echo Application exited with an error.
    pause
)

@echo off
title StreamCap Launcher
echo Starting StreamCap...

:: Check if python is installed
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo Python is not installed or not in your PATH.
    echo Please install Python 3.10+ and try again.
    pause
    exit /b
)

:: Run the application
python main.py

if %errorlevel% neq 0 (
    echo Application exited with an error.
    pause
)

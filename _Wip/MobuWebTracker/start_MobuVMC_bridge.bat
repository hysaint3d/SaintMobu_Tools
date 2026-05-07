@echo off
title MobuVMC-Bridge
echo ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
echo  MobuVMC-Bridge Launcher
echo ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
echo.
python MobuVMC_bridge.py
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo !!! Bridge exited with error.
    echo Make sure Python is installed and added to PATH.
    pause
)
pause

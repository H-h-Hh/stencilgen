@echo off
cd /d "%~dp0"
py -3 -m stencilgen
if errorlevel 1 pause

@echo off
chcp 65001 >nul
cd /d "%~dp0Doclab-Inbox"
call dsh web --port 3080
pause

@echo off
rem Double-click to let phones on your home Wi-Fi open oTube (asks for admin once).
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0allow-phones.ps1"

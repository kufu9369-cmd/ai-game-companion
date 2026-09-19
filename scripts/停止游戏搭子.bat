@echo off
title 停止 AI 游戏搭子
echo 正在停止后端（端口 12393 / 12394）...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":12393" ^| findstr LISTENING') do taskkill /F /PID %%p >nul 2>&1
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":12394" ^| findstr LISTENING') do taskkill /F /PID %%p >nul 2>&1
echo 正在停止 ollama（端口 11434）...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":11434" ^| findstr LISTENING') do taskkill /F /PID %%p >nul 2>&1
taskkill /F /IM ollama.exe >nul 2>&1
taskkill /F /IM llama-server.exe >nul 2>&1
echo.
echo 已全部停止。
ping -n 4 127.0.0.1 >nul

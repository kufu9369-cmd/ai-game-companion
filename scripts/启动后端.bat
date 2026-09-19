@echo off
setlocal EnableDelayedExpansion
title AI 游戏搭子 · 后端（手机连接用）
set ROOT=D:\claude-code-ku\ai-game-companion
set VT=%ROOT%\Open-LLM-VTuber
set OLLAMA_EXE=%LOCALAPPDATA%\Programs\Ollama\ollama.exe
if not exist "%ROOT%\logs" mkdir "%ROOT%\logs" >nul 2>&1

echo ============================================
echo    AI 游戏搭子 · 后端（手机连接用）
echo ============================================

netstat -ano | findstr ":12393" | findstr LISTENING >nul 2>&1
if not errorlevel 1 (
  echo 后端已经在运行了（端口 12393），手机可以直接连。
  echo 手机端填写：IP=本机局域网 IP（下方 openssl 输出里有打印）  端口=12393  口令=ai-gc-2026
  echo 按任意键关闭本窗口（后端继续在后台跑）。
  pause >nul
  exit /b 0
)

netstat -ano | findstr ":11434" | findstr LISTENING >nul 2>&1
if errorlevel 1 (
  if exist "%OLLAMA_EXE%" (
    echo 启动 ollama ...
    set OLLAMA_KEEP_ALIVE=-1
    set OLLAMA_FLASH_ATTENTION=1
    set OLLAMA_KV_CACHE_TYPE=q8_0
    set OLLAMA_CONTEXT_LENGTH=4096
    start "AI游戏搭子-ollama" /min "%OLLAMA_EXE%" serve
    ping -n 6 127.0.0.1 >nul
  )
)

echo 启动后端 ...（这个窗口保持开着 = 后端在运行；关掉窗口 = 停止后端）
echo 下面会打印本机 IP，手机 App 里就填这个 IP，端口 12393，口令 ai-gc-2026
echo ------------------------------------------------------------
call "%VT%\_run_backend.cmd"
echo.
echo 后端已退出。日志：%ROOT%\logs\backend.log
pause

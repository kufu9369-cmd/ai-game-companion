@echo off
setlocal EnableDelayedExpansion
title AI 游戏搭子 · PC 端
set ROOT=D:\claude-code-ku\ai-game-companion
set VT=%ROOT%\Open-LLM-VTuber
set LOGDIR=%ROOT%\logs
set OLLAMA_EXE=%LOCALAPPDATA%\Programs\Ollama\ollama.exe
set EDGE=%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe
if not exist "%EDGE%" set EDGE=C:\Program Files\Google\Chrome\Application\chrome.exe
if not exist "%LOGDIR%" mkdir "%LOGDIR%" >nul 2>&1

echo ============================================
echo    AI 游戏搭子 · PC 端
echo ============================================

rem ---------- 1/4 ollama ----------
netstat -ano | findstr ":11434" | findstr LISTENING >nul 2>&1
if errorlevel 1 (
  if exist "%OLLAMA_EXE%" (
    echo [1/4] 启动 ollama ...
    set OLLAMA_KEEP_ALIVE=-1
    set OLLAMA_FLASH_ATTENTION=1
    set OLLAMA_KV_CACHE_TYPE=q8_0
    set OLLAMA_CONTEXT_LENGTH=4096
    start "AI游戏搭子-ollama" /min "%OLLAMA_EXE%" serve
    ping -n 6 127.0.0.1 >nul
  ) else (
    echo [1/4] [!] 没找到 ollama，语音对话用不了，界面仍会打开
  )
) else (
  echo [1/4] ollama 已在运行
)

rem ---------- 2/4 后端 ----------
netstat -ano | findstr ":12393" | findstr LISTENING >nul 2>&1
if errorlevel 1 (
  echo [2/4] 启动后端（最小化窗口，日志 logs\backend.log）...
  start "AI游戏搭子-后端" /min cmd /c ""%VT%\_run_backend.cmd""
) else (
  echo [2/4] 后端已在运行
)

rem ---------- 3/4 等待就绪 ----------
echo [3/4] 等待后端就绪（首次要加载语音模型，约 20~40 秒）...
set /a TRIES=0
:WAITLOOP
ping -n 3 127.0.0.1 >nul
netstat -ano | findstr ":12393" | findstr LISTENING >nul 2>&1
if errorlevel 1 (
  set /a TRIES+=1
  if !TRIES! LSS 45 goto WAITLOOP
  echo   [!] 等待超时，请查看 %LOGDIR%\backend.log
  pause
  exit /b 1
)
echo   后端已就绪

rem ---------- 4/4 打开界面 ----------
echo [4/4] 打开 PC 端界面 ...
start "" "%EDGE%" --app="http://localhost:12393/?token=ai-gc-2026"

echo.
echo ============================================
echo   已完成！
echo   · 界面窗口已打开（关掉它不影响后端继续运行）
echo   · 停止后端和 ollama：双击「停止游戏搭子.bat」
echo   · 只启动后端给手机连：双击「启动后端.bat」
echo ============================================
ping -n 11 127.0.0.1 >nul

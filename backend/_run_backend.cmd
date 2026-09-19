@echo off
title AI游戏搭子-后端
cd /d "%~dp0"

rem 刷新 HTTPS 证书（换网络 / 换 IP 后手机不用重装 App）
set "OPENSSL_BIN=C:\Program Files\Git\usr\bin\openssl.exe"
if exist "ssl\gen_cert.py" (
    ".venv\Scripts\python.exe" "ssl\gen_cert.py" >nul 2>&1
)

rem ==========================================================
rem  API 密钥配置
rem
rem  请不要把密钥直接写在这个文件里（尤其是要提交到 Git 时）。
rem  两种安全做法，任选其一：
rem    1) 用系统环境变量：在「系统属性 → 环境变量」里新建
rem       OPENAI_COMPATIBLE_API_KEY / DEEPSEEK_API_KEY
rem    2) 取消下面这行的注释并填上你自己的密钥（注意 .gitignore）
rem
rem  set OPENAI_COMPATIBLE_API_KEY=你的密钥
rem ==========================================================

if not exist "..\logs" mkdir "..\logs"

".venv\Scripts\python.exe" run_server.py > "..\logs\backend.log" 2>&1

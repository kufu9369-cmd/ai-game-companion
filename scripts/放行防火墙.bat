@echo off
setlocal
title Allow Firewall 12393 12394

rem 自动提权（UAC 点一次"是"）
net session >nul 2>&1
if %%errorlevel%% neq 0 (
    echo 正在请求管理员权限...
    powershell -NoProfile -Command "Start-Process -FilePath '%%~dpnx0' -Verb RunAs"
    exit /b
)

echo.
echo 正在放行 TCP 12393 (HTTP) 与 12394 (HTTPS) ...
netsh advfirewall firewall delete rule name="AI Game Companion 12393" >nul 2>&1
netsh advfirewall firewall add rule name="AI Game Companion 12393" dir=in action=allow protocol=TCP localport=12393
netsh advfirewall firewall delete rule name="AI Game Companion 12394" >nul 2>&1
netsh advfirewall firewall add rule name="AI Game Companion 12394" dir=in action=allow protocol=TCP localport=12394
echo.
echo 完成。同一局域网内的手机现在可以连接了。
timeout /t 6 >nul
exit /b

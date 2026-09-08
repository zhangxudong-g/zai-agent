@echo off
REM 完全卸载 zai-agent，包括用户数据目录

echo 正在卸载 zai-agent...

REM 卸载 pip 包
pip uninstall zai-agent -y >nul 2>&1

REM 删除用户数据目录
if exist "%USERPROFILE%\.zai" (
    echo 删除用户数据目录: %USERPROFILE%\.zai
    rmdir /s /q "%USERPROFILE%\.zai"
)

echo 卸载完成！
pause

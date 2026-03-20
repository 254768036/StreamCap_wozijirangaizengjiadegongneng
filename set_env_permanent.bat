@echo off
echo 为StreamCap永久设置FFmpeg环境变量...

:: 设置要添加的FFmpeg路径
set FFMPEG_PATH=G:\AI\ffmpeg-7.1.1-essentials_build\bin

:: 添加到用户环境变量 (永久)
echo 当前用户PATH中添加FFmpeg路径...
setx PATH "%PATH%;%FFMPEG_PATH%"

if %errorlevel% equ 0 (
    echo.
    echo ✅ FFmpeg路径已永久添加到用户环境变量
    echo 路径: %FFMPEG_PATH%
    echo.
    echo 注意: 请重启命令提示符或重新登录系统使环境变量生效
) else (
    echo.
    echo ❌ 设置环境变量失败
)

echo.
pause
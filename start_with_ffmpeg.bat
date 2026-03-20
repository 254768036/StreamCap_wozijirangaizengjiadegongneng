@echo off
title StreamCap with FFmpeg

echo ==============================================
echo           🎙️ 启动 StreamCap (语音识别)
echo ==============================================
echo.

:: 设置FFmpeg路径
set FFMPEG_PATH=G:\AI\ffmpeg-7.1.1-essentials_build\bin
set PATH=%FFMPEG_PATH%;%PATH%

:: 验证FFmpeg
echo 检查FFmpeg环境...
ffmpeg -version >nul 2>&1
if %errorlevel% neq 0 (
    echo ❌ FFmpeg未找到或配置错误！
    echo 请检查FFmpeg路径是否正确：G:\AI\ffmpeg-7.1.1-essentials_build\bin
    pause
    exit /b
)

echo ✅ FFmpeg环境正常 (版本 7.1.1)
echo.

:: 运行程序
echo 启动StreamCap程序...
python main.py

if %errorlevel% neq 0 (
    echo.
    echo ❌ 程序运行出错，错误代码：%errorlevel%
    echo 请检查Python环境和依赖包
    pause
)
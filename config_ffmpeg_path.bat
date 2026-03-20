@echo off
echo 配置StreamCap使用现有FFmpeg...

:: 设置FFmpeg路径到环境变量
set FFMPEG_PATH=G:\AI\ffmpeg-7.1.1-essentials_build\bin

:: 临时添加到PATH
set PATH=%FFMPEG_PATH%;%PATH%

:: 验证FFmpeg
echo 验证FFmpeg安装...
ffmpeg -version

if %errorlevel% equ 0 (
    echo FFmpeg配置成功！
) else (
    echo FFmpeg配置失败，请检查路径
)

pause
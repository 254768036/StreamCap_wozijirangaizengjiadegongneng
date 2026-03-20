#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
修复StreamCap的FFmpeg路径配置
"""

import os
import sys
import subprocess
from pathlib import Path

def add_ffmpeg_to_path():
    """将FFmpeg路径添加到环境变量"""
    ffmpeg_path = r"G:\AI\ffmpeg-7.1.1-essentials_build\bin"
    
    # 添加到当前进程的环境变量
    current_path = os.environ.get("PATH", "")
    if ffmpeg_path not in current_path:
        os.environ["PATH"] = ffmpeg_path + os.pathsep + current_path
        print(f"[OK] 已添加FFmpeg路径: {ffmpeg_path}")

def check_ffmpeg():
    """检查FFmpeg是否可用"""
    try:
        result = subprocess.run(["ffmpeg", "-version"], 
                              capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            version_line = result.stdout.split('\n')[0]
            print(f"[OK] FFmpeg检测成功: {version_line}")
            return True
        else:
            print(f"[ERROR] FFmpeg检测失败: {result.stderr}")
            return False
    except Exception as e:
        print(f"[ERROR] FFmpeg检测异常: {e}")
        return False

def main():
    print("=" * 50)
    print("[StreamCap] FFmpeg路径修复工具")
    print("=" * 50)
    
    # 添加FFmpeg路径
    add_ffmpeg_to_path()
    
    # 检查FFmpeg
    if check_ffmpeg():
        print("\n[SUCCESS] 配置完成! FFmpeg已正确配置")
        print("现在可以正常启动StreamCap了")
    else:
        print("\n[FAILED] 配置失败! 请检查FFmpeg安装")
    
    print("\n提示: 使用 start_with_ffmpeg.bat 启动程序可确保FFmpeg配置生效")

if __name__ == "__main__":
    main()
#!/bin/bash
set -e

echo "Starting Xvfb..."
# 启动虚拟显存
Xvfb :99 -screen 0 1920x1080x24 &
export DISPLAY=:99

echo "Starting web2api service..."
# 使用 uv 启动主程序
exec uv run python main.py

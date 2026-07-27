#!/bin/bash
# 电商礼盒优惠对比 · 在线工具 启动脚本
# 依赖：managed python venv (含 openpyxl / pillow / flask)
VENV=/Users/carlinchen/.workbuddy/binaries/python/envs/default
PORT="${1:-5055}"
cd "$(dirname "$0")"
exec "$VENV/bin/python" app.py --port "$PORT"

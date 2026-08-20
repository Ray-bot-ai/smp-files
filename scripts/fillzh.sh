#!/bin/bash
# fill_zh 独立启动器：只补译文缺口，不碰 archive.org（网络抖动期也能跑）。
# 1) unset PYTHONPATH/PYTHONHOME —— 防 Hermes 终端环境污染（numpy ABI 崩溃教训）
# 2) nohup + caffeinate 脱离会话、防休眠
# 用法：bash scripts/fillzh.sh
unset PYTHONPATH PYTHONHOME
cd "$(dirname "$0")/.." || exit 1
nohup caffeinate -is .venv/bin/python -u fill_zh.py > /tmp/smp-fillzh.log 2>&1 < /dev/null &
echo "launched pid=$!"

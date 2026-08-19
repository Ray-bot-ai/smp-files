#!/bin/bash
# SMP 跑批启动器（持久版，位于项目 scripts/）：
# 1) unset PYTHONPATH/PYTHONHOME —— Hermes 终端会话自带 PYTHONPATH 指向
#    hermes-agent 的 python3.11 venv，会让项目的 python3.13 进程 import numpy
#    时 ABI 不匹配直接崩（2026-08-16 实测 run_chunked 启动即崩）
# 2) nohup 摘 SIGHUP；脚本立即退出，子进程被 launchd 收养（PPID=1）
# 用法：bash scripts/relaunch.sh
unset PYTHONPATH PYTHONHOME
cd "$(dirname "$0")/.." || exit 1
nohup caffeinate -is ./run_all.sh > /tmp/smp-run.log 2>&1 < /dev/null &
echo "launched pid=$!"

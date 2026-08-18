#!/bin/bash
# 先补译文缺口（短），再放全量跑批（长）。串行，避免两边同时写 data/。
cd "$(dirname "$0")"
echo "=== $(date '+%m-%d %H:%M') 补译文缺口 ==="
.venv/bin/python -u fill_zh.py 2>&1 | grep --line-buffered -vE "SyntaxWarning|r'http"

echo
echo "=== $(date '+%m-%d %H:%M') 全量跑批开始 ==="
.venv/bin/python -u run_chunked.py --chunk 40 2>&1 | grep --line-buffered -vE "SyntaxWarning|r'http"

echo
echo "=== $(date '+%m-%d %H:%M') 全部结束 ==="

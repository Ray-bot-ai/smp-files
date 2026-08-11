#!/bin/bash
# 整夜无人值守：等取图完成 → 转录 → 翻译 → 重建站点。
# caffeinate 防休眠；每步都可断点续传，中断后重跑本脚本即可。
cd "$(dirname "$0")"
echo "=== $(date +%H:%M) 等取图结束 ==="
while pgrep -f fetch_corpus.py >/dev/null; do sleep 60; done
echo "=== $(date +%H:%M) 取图完成，开始转录 ==="
.venv/bin/python corpus_run.py ocr 2>&1 | grep -vE "SyntaxWarning|r'http"
echo "=== $(date +%H:%M) 转录完成，开始翻译 ==="
.venv/bin/python corpus_run.py translate 2>&1 | grep -vE "SyntaxWarning|r'http"
echo "=== $(date +%H:%M) 翻译完成，重建站点 ==="
.venv/bin/python build_site.py 2>&1 | grep -vE "SyntaxWarning|r'http"
.venv/bin/python consistency.py 2>&1 | tail -4
echo "=== $(date +%H:%M) 全部完成 ==="

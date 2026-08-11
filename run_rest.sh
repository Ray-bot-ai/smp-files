#!/bin/bash
cd "$(dirname "$0")"
echo "=== $(date +%H:%M) 开始并发翻译 ==="
.venv/bin/python corpus_run.py translate 2>&1 | grep -vE "SyntaxWarning|r'http"
echo "=== $(date +%H:%M) 翻译完成，重建站点 ==="
.venv/bin/python build_site.py 2>&1 | grep -vE "SyntaxWarning|r'http"
echo "=== $(date +%H:%M) 全部完成 ==="

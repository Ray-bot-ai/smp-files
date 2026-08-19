#!/bin/bash
# SMP 安全暂存（持久版，scripts/）：只把「15 分钟前完成且校验通过」的数据文件
# 加进暂存区。校验：JSON 可解析 + 转录页齐全 + 无静默缺失页。
# 用法：bash scripts/stage_safe.sh  （在 build_site.py 之后运行）
cd "$(dirname "$0")/.." || exit 1

python3 - <<'PY' > /tmp/safe_add.txt
import json, glob, os, time
now = time.time()
out = []
for f in glob.glob('data/*.json'):
    if os.path.getmtime(f) > now - 900:
        continue
    try:
        d = json.load(open(f, encoding='utf-8'))
    except Exception:
        continue
    if d.get('pages_done', 0) != d.get('pages', 0):
        continue
    if not all((q.get('en') or '').strip() or q.get('error')
               for q in d.get('page_data', [])):
        continue
    out.append(f)
print('\n'.join(out))
PY

echo "安全文件数: $(wc -l < /tmp/safe_add.txt)"
xargs git add < /tmp/safe_add.txt
git add docs/data
echo "--- 暂存的数据文件总数 ---"
git diff --cached --name-only -- data | wc -l
echo "--- 混入检查（应无输出）---"
git diff --cached --name-only -- data | while read -r f; do
  age=$(( $(date +%s) - $(stat -f %m "$f") ))
  if [ "$age" -lt 900 ]; then echo "⚠ 混入: $f（$age 秒前改过）"; fi
done || true
echo "--- 暂存汇总 ---"
git diff --cached --stat | tail -1
echo "--- 未暂存的数据（应为在写批次）---"
git status --short data/ | grep -cv "^[MA] " || true

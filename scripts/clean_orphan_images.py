#!/usr/bin/env python3
"""SMP 图片孤儿目录安全清理。
删除判据（双重保守）：
  ① 对应 data/*.json 存在且 pages_done == pages（转录已完成，图片不再需要）
  ② 目录 mtime 和 data 文件 mtime 都 > 90 分钟（绝不碰当前活跃批次）
其余一律保留。清理前打印统计。"""
import glob
import json
import os
import shutil
import sys
import time

DRY = "--dry" in sys.argv

ROOT = "/Users/yangrui/projects/smp-vlm-ocr"
IMG = os.path.join(ROOT, "images")
DATA = os.path.join(ROOT, "data")
now = time.time()
WINDOW = 5400  # 90 分钟

to_del = []
keep = {"活跃(90分钟内)": 0, "无数据文件": 0, "转录未完成": 0, "数据不可解析": 0, "非目录": 0}

for d in sorted(glob.glob(os.path.join(IMG, "*"))):
    if not os.path.isdir(d):
        keep["非目录"] += 1
        continue
    name = os.path.basename(d)
    f = os.path.join(DATA, name + ".json")
    if not os.path.exists(f):
        keep["无数据文件"] += 1
        continue
    if os.path.getmtime(d) > now - WINDOW or os.path.getmtime(f) > now - WINDOW:
        keep["活跃(90分钟内)"] += 1
        continue
    try:
        doc = json.load(open(f, encoding="utf-8"))
    except Exception:
        keep["数据不可解析"] += 1
        continue
    if doc.get("pages_done", 0) != doc.get("pages", 0):
        keep["转录未完成"] += 1
        continue
    to_del.append(d)

size = sum(os.path.getsize(os.path.join(r, fn))
           for d in to_del for r, _, fns in os.walk(d) for fn in fns)

print(f"将删除: {len(to_del)} 个目录，约 {size/2**30:.2f} GB")
print(f"保留: {keep}")
if DRY:
    print("[DRY] 不实删。待删目录样例:",
          [os.path.basename(x) for x in to_del[:8]],
          "…共", len(to_del), "个" if len(to_del) > 8 else "")
    raise SystemExit(0)
if not to_del:
    print("无孤儿可清，结束")
    raise SystemExit(0)

for d in to_del:
    shutil.rmtree(d)
print(f"已删除 {len(to_del)} 个目录")

left = sum(os.path.getsize(os.path.join(r, fn))
           for d in glob.glob(os.path.join(IMG, "*"))
           for r, _, fns in os.walk(d) for fn in fns)
print(f"images/ 剩余大小: {left/2**30:.2f} GB")

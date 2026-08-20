#!/usr/bin/env python3
"""修复 27 页 HTML 坏图：删除假影像文件 + 重置 error/tries，让跑批重取重转。
用法：
    python3 scripts/repair_bad_images.py --dry    # 只列不改
    python3 scripts/repair_bad_images.py          # 实改
逻辑：
    1) 扫 data/*.json，找出 error 含 InvalidParameter 的页（27 页，3 件）
    2) 删 images/<file>/n<page>.jpg（这些是 IA 错误页 HTML）
    3) 该页 error=''、tries=0（让流水线自动重取+重转+重译）
"""
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
IMAGES = os.path.join(ROOT, "images")
DRY = "--dry" in sys.argv

found = []
for f in sorted(glob.glob(f"{DATA}/*.json")):
    d = json.load(open(f, encoding="utf-8"))
    for q in d.get("page_data", []):
        if "InvalidParameter" in (q.get("error") or ""):
            found.append((f, q["n"]))

print(f"找到 {len(found)} 页（error=InvalidParameter）")
bad_files = []
for f, n in found:
    iid = os.path.basename(f)[:-5]
    img = os.path.join(IMAGES, iid, f"n{n}.jpg")
    exists = os.path.exists(img)
    if not exists:
        print(f"  ⚠ {iid} n{n}: 影像文件不存在（跳过删除）")
        continue
    head = open(img, "rb").read(8)
    is_html = head.lstrip()[:1] == b"<" or b"html" in head.lower()
    print(f"  {iid} n{n}: {img} ({'HTML 坏图' if is_html else '非JPEG'})")
    bad_files.append((f, n, img, iid))

if DRY:
    print(f"\n[DRY] 将删除 {len(bad_files)} 个文件并重置 {len(found)} 页，未做任何修改")
    sys.exit(0)

# 实改：先删文件，再改数据（重置独立于删除——影像可能已被清理）
deleted = 0
for f, n, img, iid in bad_files:
    if os.path.exists(img):
        os.remove(img)
        deleted += 1

changed_files = set()
reset_count = 0
for f, n in found:
    d = json.load(open(f, encoding="utf-8"))
    for q in d["page_data"]:
        if q.get("n") == n and "InvalidParameter" in (q.get("error") or ""):
            q["error"] = ""
            q["tries"] = 0
            reset_count += 1
    json.dump(d, open(f, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    changed_files.add(os.path.basename(f))

print(f"\n完成：删除 {deleted} 个坏文件，重置 {reset_count} 页（{len(changed_files)} 件）")

# 自验
for f, n in found:
    d = json.load(open(f, encoding="utf-8"))
    q = [x for x in d["page_data"] if x.get("n") == n][0]
    assert q["error"] == "" and q["tries"] == 0, f"未重置: {os.path.basename(f)} n{n}"
for f, n, img, iid in bad_files:
    assert not os.path.exists(img), f"文件未删: {img}"
assert reset_count == len(found), f"重置数 {reset_count} != 找到数 {len(found)}"
print("自验 PASS：状态已重置、文件已删")

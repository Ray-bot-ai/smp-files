"""只补译文缺口：扫全库，把「有英文、非中文原件、却没有译文」的页补上。

为什么单独一个入口：run_chunked 只处理**转录还没做完**的件，
转录已完成但译文有缺口的件它根本不会碰。这类缺口的来源是模型偶尔吞掉
⟦p数字⟧ 页码标记，属于翻译阶段的问题，得单独扫一遍。

用法：.venv/bin/python fill_zh.py
"""
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import demo_run as D

if __name__ == "__main__":
    gaps, npg = [], 0
    for f in glob.glob(os.path.join(D.DATA, "*.json")):
        d = json.load(open(f, encoding="utf-8"))
        m = D._missing_zh(d)
        if m:
            gaps.append(d["ia_id"])
            npg += len(m)
    print(f"译文缺口：{npg} 页，分布在 {len(gaps)} 件", flush=True)
    if not gaps:
        sys.exit(0)
    D.DEMO = sorted(gaps)
    D.run_translate()
    left = 0
    for iid in gaps:
        p = os.path.join(D.DATA, f"{iid}.json")
        if os.path.exists(p):
            left += len(D._missing_zh(json.load(open(p, encoding="utf-8"))))
    print(f"补译后仍缺 {left} 页", flush=True)

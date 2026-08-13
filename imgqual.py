"""按原件影像的成像质量预测转录可信度。

来由：肉眼看一眼影像就大致知道这页转录靠不靠谱——过曝发白、糊成一团的页，
模型要么标满 □，要么干脆编造（674/p6 就是：影像几乎读不出字，
模型编出一段司馬遷《史記》，而且**零个 □**，所有文本侧判据都抓不到）。
如果这个直觉成立，就能在**转录之前**先筛出高风险页，成本几乎为零。

四个指标，都不需要 scipy：
  ink      前景（暗）像素占比。太低=几乎空白；太高=糊成一片黑
  contrast 灰度标准差。越低越平（过曝/欠曝都会让它变低）
  sharp    拉普拉斯响应的标准差。越低越糊
  blown    极亮像素占比。缩微胶卷过曝的典型特征
"""
import glob
import json
import os
import sys

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
IMG_DIR = os.path.join(HERE, "images")


def metrics(path, box=1400):
    """算一页影像的成像指标。缩到 box 宽再算，快且不影响判别。"""
    try:
        im = Image.open(path).convert("L")
    except Exception:
        return None
    if im.width > box:
        im = im.resize((box, max(1, int(im.height * box / im.width))))
    a = np.asarray(im, dtype=np.float32)
    # 拉普拉斯（4 邻域），不依赖 scipy
    lap = (a[1:-1, 2:] + a[1:-1, :-2] + a[2:, 1:-1] + a[:-2, 1:-1] - 4 * a[1:-1, 1:-1])
    return {
        "mean": float(a.mean()),
        "contrast": float(a.std()),
        "sharp": float(lap.std()),
        "ink": float((a < 128).mean()),
        "blown": float((a > 245).mean()),
    }


def page_path(iid, n):
    return os.path.join(IMG_DIR, iid, f"n{n}.jpg")


if __name__ == "__main__":
    out = {}
    for d in sorted(glob.glob(os.path.join(IMG_DIR, "*"))):
        iid = os.path.basename(d)
        for p in sorted(glob.glob(os.path.join(d, "n*.jpg"))):
            n = int(os.path.basename(p)[1:-4])
            m = metrics(p)
            if m:
                out[f"{iid}#{n}"] = m
        print(f"\r{len(out)} 页", end="", flush=True)
    json.dump(out, open(os.path.join(HERE, "imgqual.json"), "w"), separators=(",", ":"))
    print(f"\n写入 imgqual.json（{len(out)} 页）")

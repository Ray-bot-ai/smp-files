"""检出被旋转 90° 放置的页面。

为什么不能用长宽比：实测 145 页横向图里，绝大多数是**多页并排扫在一起**
（如 smpa-files-1313 p42 是三页手写笔记并扫），文字方向是正的。横向 ≠ 旋转。

真正的判据：文字行会在**垂直于行方向**上形成周期性的墨迹疏密。
正立页面的行投影（每行墨量）起伏剧烈、列投影平缓；旋转 90° 则反过来。
用两个方向投影的归一化方差之比来判定，再要求页面确实有足够墨迹（排除空白页）。

用法：.venv/bin/python detect_rotation.py [--fix]
      --fix 会把判定为旋转的页转正后另存 images_rot/<iid>/n<N>.jpg（不动原图）
"""
import glob, json, os, sys

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
IMG = os.path.join(HERE, "images")
OUT = os.path.join(HERE, "docs", "data", "rotated.json")


def profile_ratio(path, max_side=900):
    """返回 (行投影方差 / 列投影方差, 墨迹占比)。>1 = 正立，<1 = 疑似旋转。"""
    try:
        im = Image.open(path).convert("L")
    except Exception:
        return None, 0
    im.thumbnail((max_side, max_side), Image.BILINEAR)
    a = np.asarray(im, dtype=np.float32)
    ink = (a < 140).astype(np.float32)
    dens = ink.mean()
    if dens < 0.01 or dens > 0.5:          # 空白页或全黑页，无从判断
        return None, dens
    row = ink.mean(1)                       # 每行墨量
    col = ink.mean(0)                       # 每列墨量
    # 只看有内容的区域，避免大片白边把方差压低
    row = row[row > row.max() * 0.05] if row.max() else row
    col = col[col > col.max() * 0.05] if col.max() else col
    if len(row) < 10 or len(col) < 10:
        return None, dens
    rv = row.var() / (row.mean() ** 2 + 1e-9)
    cv = col.var() / (col.mean() ** 2 + 1e-9)
    return rv / (cv + 1e-9), dens


def scan(limit=None):
    files = sorted(glob.glob(os.path.join(IMG, "*", "n*.jpg")))
    if limit:
        files = files[:limit]
    out = []
    for i, f in enumerate(files, 1):
        r, d = profile_ratio(f)
        if r is None:
            continue
        if r < 0.55:                        # 列向起伏明显强于行向 → 疑似旋转
            iid = os.path.basename(os.path.dirname(f))
            n = int(os.path.basename(f)[1:-4])
            out.append({"iid": iid, "n": n, "ratio": round(float(r), 3), "ink": round(float(d), 3)})
        if i % 1000 == 0:
            print(f"  扫描 {i}/{len(files)}…", flush=True)
    return out, len(files)


if __name__ == "__main__":
    rot, total = scan()
    rot.sort(key=lambda x: x["ratio"])
    print(f"\n扫描 {total:,} 页，疑似旋转 {len(rot)} 页（{len(rot)/max(total,1)*100:.2f}%）")
    for x in rot[:25]:
        print(f"  {x['iid']:<20} p{x['n']:<4} ratio={x['ratio']:<6} ink={x['ink']}")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(rot, open(OUT, "w"), ensure_ascii=False)
    print(f"→ {OUT}")

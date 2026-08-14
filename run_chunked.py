"""分批跑：下载一批影像 → 转录 → 翻译 → 记录影像指标 → 删掉本地影像 → 下一批。

为什么这么做：全库 92,769 页原生影像约 34 GB，没必要同时躺在盘上。
影像是**可再取的**（Internet Archive 是源头，每页 URL 都记在 data/*.json 的
image_url 里），转录产出才是不可替换的——所以删影像安全，删产出不行。

三条安全前提，缺一不可：
  1. **先验证再删**：这一批每一页都要么有正文、要么有明确记录的错误，
     并且 data/*.json 确实落盘了，才允许删影像。宁可留着占地方，不可删错。
  2. **影像指标必须在删之前算**：imgqual 要读原图，删了就再也算不了
     （除非重下）。所以顺序是「算指标 → 删图」，不能颠倒。
  3. 只删本批次自己下的目录，不碰别的。

用法：
    .venv/bin/python run_chunked.py --chunk 40            # 每批 40 件
    .venv/bin/python run_chunked.py --chunk 40 --keep     # 不删图（调试用）
    .venv/bin/python run_chunked.py --stat
"""
import argparse
import concurrent.futures as cf
import glob
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import demo_run as D
import imgqual
from common import IMG_DIR
from pipeline import download

HERE = os.path.dirname(os.path.abspath(__file__))
QUAL = os.path.join(HERE, "imgqual.json")


def load_qual():
    return json.load(open(QUAL, encoding="utf-8")) if os.path.exists(QUAL) else {}


def save_qual(q):
    json.dump(q, open(QUAL, "w", encoding="utf-8"), separators=(",", ":"))


def targets():
    """还没转录完的件，按清单顺序。"""
    mm = D.manifest_map()
    out = []
    for iid, m in mm.items():
        if m.get("error") or not m.get("pages"):
            continue
        p = os.path.join(D.DATA, f"{iid}.json")
        if os.path.exists(p):
            d = json.load(open(p, encoding="utf-8"))
            if d.get("pages_done") == m["pages"]:
                continue
        out.append((iid, m["pages"]))
    return sorted(out)


def fetch_chunk(chunk, workers=24):
    """把这一批的影像拉全。返回成功页数。"""
    jobs = [(iid, n) for iid, pages in chunk for n in range(pages)]
    ok = 0
    with cf.ThreadPoolExecutor(workers) as ex:
        for p, _cached in ex.map(lambda a: download(*a), jobs):
            if p:
                ok += 1
    return ok, len(jobs)


def recoverable(err):
    """这个失败还值不值得再试一次。

    **不是所有「有错误记录」都等于处理完了。** 第一版 verified() 只要求
    「要么有正文、要么有错误」，结果把取图失败也当成了完成——那一件随即通过验证、
    影像被删、跑批向前，这一页就**永久空白**。实测 1,651 页里丢了 10 页（0.6%），
    照这个比例剩下 85,000 页会丢约 500 页，而且全程不报错。

    可修复：取图失败、超时、接口抖动——重来一次多半就好了。
    没救：内容审查两侧都拒（Input/Output），而且已经走过备用端点。
    """
    if not err:
        return False
    if "DataInspection" in err or "data_inspection" in err.lower():
        return False
    return True


def verified(iid, pages):
    """这一件是不是真的处理完了。

    完成 = 每页要么有正文，要么有**没救的**错误且已试满次数。
    可修复的失败一律算没完成——影像因此保留，下一轮还能重取重转。
    """
    p = os.path.join(D.DATA, f"{iid}.json")
    if not os.path.exists(p):
        return False
    d = json.load(open(p, encoding="utf-8"))
    got = {q["n"]: q for q in d.get("page_data", [])}
    if len(got) < pages:
        return False
    for n in range(pages):
        q = got.get(n)
        if q is None:
            return False
        if (q.get("en") or "").strip():
            continue
        err = q.get("error")
        if not err:
            return False        # 既没正文又没报错 = 静默失败
        if recoverable(err) and q.get("tries", 0) < D.MAX_TRIES:
            return False        # 还能救，别急着当完成（影像因此保住）
    return True


def missing_images(chunk):
    """取图失败的页。这类是网络抖动，重取大概率就有了。"""
    out = []
    for iid, _pages in chunk:
        p = os.path.join(D.DATA, f"{iid}.json")
        if not os.path.exists(p):
            continue
        d = json.load(open(p, encoding="utf-8"))
        for q in d.get("page_data", []):
            if (q.get("en") or "").strip():
                continue
            if "无图" in (q.get("error") or "") and q.get("tries", 0) < D.MAX_TRIES:
                out.append((iid, q["n"]))
    return out


def record_quality(chunk, qual):
    """趁影像还在本地，把成像指标算下来存好。删图之后就没机会了。"""
    n_new = 0
    for iid, pages in chunk:
        for n in range(pages):
            k = f"{iid}#{n}"
            if k in qual:
                continue
            m = imgqual.metrics(imgqual.page_path(iid, n))
            if m:
                qual[k] = {kk: round(vv, 4) for kk, vv in m.items()}
                n_new += 1
    return n_new


def drop_images(chunk):
    freed = 0
    for iid, _pages in chunk:
        d = os.path.join(IMG_DIR, iid)
        if not os.path.isdir(d):
            continue
        for f in glob.glob(os.path.join(d, "*")):
            freed += os.path.getsize(f)
        shutil.rmtree(d)
    return freed


def repair(ocr_workers=12):
    """全库扫一遍：把「还能救」的失败页重取重转。

    用途是补第一版 verified() 放过去的那些页——它当时把取图失败也当成完成，
    影像随即被删、跑批向前，页面永久空白。这里按 data/*.json 里记的
    image_url 重新取图（Internet Archive 是源头，随时能拉回来）。
    """
    todo = {}
    for f in glob.glob(os.path.join(D.DATA, "*.json")):
        d = json.load(open(f, encoding="utf-8"))
        for q in d.get("page_data", []):
            if (q.get("en") or "").strip():
                continue
            if recoverable(q.get("error")) and q.get("tries", 0) < D.MAX_TRIES:
                todo.setdefault(d["ia_id"], []).append(q["n"])
    npg = sum(len(v) for v in todo.values())
    print(f"可修复的失败页：{npg} 页，分布在 {len(todo)} 件")
    if not npg:
        return
    jobs = [(iid, n) for iid, ns in todo.items() for n in ns]
    with cf.ThreadPoolExecutor(16) as ex:
        got = sum(1 for p_, _ in ex.map(lambda a: download(*a), jobs) if p_)
    print(f"重新取图 {got}/{len(jobs)}")
    D.DEMO = sorted(todo)
    D.run_ocr(workers=ocr_workers)
    D.run_translate()
    left = 0
    for iid in todo:
        d = json.load(open(os.path.join(D.DATA, f"{iid}.json"), encoding="utf-8"))
        left += sum(1 for q in d["page_data"]
                    if not (q.get("en") or "").strip() and recoverable(q.get("error")))
    print(f"修复后仍缺 {left} 页")


def main(chunk_size, keep, ocr_workers, max_chunks=0):
    todo = targets()
    print(f"待处理 {len(todo)} 件 / {sum(p for _, p in todo):,} 页")
    qual = load_qual()
    nchunk = 0
    for i in range(0, len(todo), chunk_size):
        if max_chunks and nchunk >= max_chunks:
            print(f"\n已达 --max-chunks {max_chunks}，停在这里（其余未动）")
            break
        nchunk += 1
        chunk = todo[i:i + chunk_size]
        tag = f"[{i // chunk_size + 1}/{(len(todo) + chunk_size - 1) // chunk_size}]"
        print(f"\n{tag} {len(chunk)} 件 / {sum(p for _, p in chunk):,} 页")

        ok, tot = fetch_chunk(chunk)
        print(f"{tag} 取图 {ok}/{tot}")

        D.DEMO = [iid for iid, _ in chunk]
        D.run_ocr(workers=ocr_workers)

        # 取图失败的页重取重转。不做这一步，网络抖一下就是一页永久空白。
        for attempt in range(2):
            miss = missing_images(chunk)
            if not miss:
                break
            print(f"{tag} 取图失败 {len(miss)} 页，重取第 {attempt + 1} 轮", flush=True)
            with cf.ThreadPoolExecutor(16) as ex:
                list(ex.map(lambda a: download(*a), miss))
            D.DEMO = sorted({iid for iid, _ in miss})
            D.run_ocr(workers=ocr_workers)

        D.DEMO = [iid for iid, _ in chunk]
        D.run_translate()

        n_new = record_quality(chunk, qual)
        save_qual(qual)
        print(f"{tag} 影像指标 +{n_new} 页（已存 imgqual.json）")

        done = [(iid, p) for iid, p in chunk if verified(iid, p)]
        stuck = [iid for iid, p in chunk if not verified(iid, p)]
        if keep:
            print(f"{tag} --keep：保留影像")
        elif done:
            freed = drop_images(done)
            print(f"{tag} 已验证 {len(done)}/{len(chunk)} 件，删影像释放 {freed/2**30:.2f} GB")
        if stuck:
            print(f"{tag} ⚠ 未通过验证、影像保留：{stuck}")
    print("\n全部批次结束")
    D.stat()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--chunk", type=int, default=40, help="每批多少件")
    ap.add_argument("--keep", action="store_true", help="不删影像")
    ap.add_argument("--ocr-workers", type=int, default=12)
    ap.add_argument("--max-chunks", type=int, default=0, help="只跑前 N 批（试跑用）")
    ap.add_argument("--repair", action="store_true", help="全库补跑可修复的失败页")
    ap.add_argument("--stat", action="store_true")
    a = ap.parse_args()
    if a.repair:
        repair(a.ocr_workers)
    elif a.stat:
        t = targets()
        q = load_qual()
        used = sum(os.path.getsize(f) for f in glob.glob(os.path.join(IMG_DIR, "*", "*")))
        print(f"未完成 {len(t)} 件 / {sum(p for _, p in t):,} 页")
        print(f"本地影像 {used/2**30:.2f} GB")
        print(f"影像指标已记录 {len(q):,} 页")
    else:
        main(a.chunk, a.keep, a.ocr_workers, a.max_chunks)

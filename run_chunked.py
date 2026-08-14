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


def verified(iid, pages):
    """这一件是不是真的处理完了——落盘且每页都有交代（正文 或 明确的错误）。"""
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
        if not (q.get("en") or "").strip() and not q.get("error"):
            return False        # 既没正文又没报错 = 静默失败，不能当完成
    return True


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
    ap.add_argument("--stat", action="store_true")
    a = ap.parse_args()
    if a.stat:
        t = targets()
        q = load_qual()
        used = sum(os.path.getsize(f) for f in glob.glob(os.path.join(IMG_DIR, "*", "*")))
        print(f"未完成 {len(t)} 件 / {sum(p for _, p in t):,} 页")
        print(f"本地影像 {used/2**30:.2f} GB")
        print(f"影像指标已记录 {len(q):,} 页")
    else:
        main(a.chunk, a.keep, a.ocr_workers, a.max_chunks)

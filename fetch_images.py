"""按清单批量取单页图（原生分辨率 _w2400）。

关键前提（别改）：
- **必须原生分辨率**。降到 1000px 会让模型从「认错」变成「编造」，见 README。
- 用 curl 不用 urllib：IA 上 urllib 会 IncompleteRead。
- IA 文件下载并发有效（实测 P=24 约 3.5 件/秒），与 metadata 的服务端限速不同。

可断点续传：已存在且体积正常的文件直接跳过。
用法：python3 fetch_images.py run [并发数] | stat
"""
import concurrent.futures as cf
import json, os, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
MANIFEST = os.path.join(HERE, "manifest.jsonl")
IMG_ROOT = os.path.join(HERE, "images")
MIN_BYTES = 5000          # 小于此视为失败/空图
WORKERS = 24


def targets():
    """(iid, page_index, 落地路径) 逐页展开。按件分子目录，免得单目录 9 万文件。"""
    out = []
    for line in open(MANIFEST, encoding="utf-8"):
        r = json.loads(line)
        if r.get("error") or not r.get("pages"):
            continue
        d = os.path.join(IMG_ROOT, r["ia_id"])
        for n in range(r["pages"]):
            out.append((r["ia_id"], n, os.path.join(d, f"n{n}.jpg")))
    return out


def grab(t):
    iid, n, path = t
    if os.path.exists(path) and os.path.getsize(path) >= MIN_BYTES:
        return "skip"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    url = f"https://archive.org/download/{iid}/page/n{n}_w2400.jpg"
    subprocess.run(["curl", "-sL", "--max-time", "120", "-o", path, url],
                   capture_output=True)
    if os.path.exists(path) and os.path.getsize(path) >= MIN_BYTES:
        return "ok"
    if os.path.exists(path):
        os.remove(path)          # 别把空壳留下，否则续传会误判为已完成
    return "fail:" + f"{iid}/n{n}"


def run(workers=WORKERS):
    ts = targets()
    print(f"总页数 {len(ts):,}，并发 {workers}")
    t0 = time.time()
    ok = skip = 0
    fails = []
    with cf.ThreadPoolExecutor(workers) as ex:
        for i, r in enumerate(ex.map(grab, ts), 1):
            if r == "ok":
                ok += 1
            elif r == "skip":
                skip += 1
            else:
                fails.append(r[5:])
            if i % 500 == 0:
                el = time.time() - t0
                rate = (ok + skip) / el
                print(f"  {i:,}/{len(ts):,}  新下{ok:,} 跳过{skip:,} 失败{len(fails)}  "
                      f"{rate:.1f}页/秒  预计还需 {(len(ts)-i)/max(rate,.1)/60:.0f}min", flush=True)
    print(f"\n完成：新下 {ok:,}，跳过 {skip:,}，失败 {len(fails)}，"
          f"用时 {(time.time()-t0)/60:.1f} 分钟")
    if fails:
        open(os.path.join(HERE, "fetch_failures.txt"), "w").write("\n".join(fails))
        print(f"失败清单已写入 fetch_failures.txt（重跑本脚本会自动补）")


def stat():
    n = tot = 0
    for root, _, files in os.walk(IMG_ROOT):
        for f in files:
            if f.endswith(".jpg"):
                n += 1
                tot += os.path.getsize(os.path.join(root, f))
    want = len(targets()) if os.path.exists(MANIFEST) else 0
    print(f"已下 {n:,} 页 / 应有 {want:,}  ({tot/2**30:.1f} GB，均 {tot/max(n,1)/1024:.0f} KB/页)")


if __name__ == "__main__":
    a = sys.argv[1:] or ["run"]
    if a[0] == "stat":
        stat()
    else:
        run(int(a[1]) if len(a) > 1 else WORKERS)

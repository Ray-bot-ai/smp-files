"""下载 + 上传流水线：IA 取图 → 本地存一份 → 传 OSS。

为什么做成流水线而不是先下完再传：
下行和上行是两条独立的带宽，串行 7h + 9.5h = 16.5h，重叠执行约 10h。

进度记在 state.jsonl（一行一页），断点续传按「两边都成功」判断，
不靠文件存在与否——本项目踩过：下载失败留下的 0 字节空壳会被续传误判为已完成。

用法：
  .venv/bin/python pipeline.py run [--dl N] [--up N] [--limit N]
  .venv/bin/python pipeline.py stat
  .venv/bin/python pipeline.py retry      # 只重试失败的页
"""
import argparse, json, os, queue, subprocess, sys, threading, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import IMG_DIR, HERE
from oss_check import bucket

MANIFEST = os.path.join(HERE, "manifest.jsonl")
STATE = os.path.join(HERE, "state.jsonl")
MIN_BYTES = 5000
OSS_PREFIX = "pages"

_lock = threading.Lock()
_stats = {"dl": 0, "dl_skip": 0, "up": 0, "up_skip": 0, "fail": 0, "bytes": 0}


def load_state():
    """{(iid,n): {'dl':bool,'up':bool}}；同一页多条记录以最新为准。"""
    st = {}
    if os.path.exists(STATE):
        for line in open(STATE, encoding="utf-8"):
            try:
                r = json.loads(line)
            except Exception:
                continue
            st[(r["iid"], r["n"])] = r
    return st


def all_pages():
    """去重后的清单（成功记录优先），逐页展开。"""
    by_id = {}
    for line in open(MANIFEST, encoding="utf-8"):
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r["ia_id"] not in by_id or (by_id[r["ia_id"]].get("error") and not r.get("error")):
            by_id[r["ia_id"]] = r
    out = []
    for r in by_id.values():
        if r.get("error") or not r.get("pages"):
            continue
        for n in range(r["pages"]):
            out.append((r["ia_id"], n))
    return out


def local_path(iid, n):
    return os.path.join(IMG_DIR, iid, f"n{n}.jpg")


def oss_key(iid, n):
    return f"{OSS_PREFIX}/{iid}/n{n}.jpg"


def download(iid, n):
    p = local_path(iid, n)
    if os.path.exists(p) and os.path.getsize(p) >= MIN_BYTES:
        return p, True                      # 已有，跳过
    os.makedirs(os.path.dirname(p), exist_ok=True)
    url = f"https://archive.org/download/{iid}/page/n{n}_w2400.jpg"   # 原生分辨率，别改
    subprocess.run(["curl", "-sL", "--max-time", "120", "-o", p, url], capture_output=True)
    if os.path.exists(p) and os.path.getsize(p) >= MIN_BYTES:
        return p, False
    if os.path.exists(p):
        os.remove(p)                        # 删空壳，否则续传误判为已完成
    return None, False


def upload(iid, n, p):
    bucket.put_object_from_file(oss_key(iid, n), p)


def worker(q, out_q, do_upload):
    while True:
        item = q.get()
        if item is None:
            q.task_done()
            return
        iid, n, need_dl, need_up = item
        rec = {"iid": iid, "n": n, "t": int(time.time())}
        try:
            if need_dl:
                p, skipped = download(iid, n)
                if p is None:
                    rec["error"] = "download"
                    out_q.put(rec); q.task_done(); continue
                with _lock:
                    _stats["dl_skip" if skipped else "dl"] += 1
                    _stats["bytes"] += os.path.getsize(p)
            else:
                p = local_path(iid, n)
            rec["dl"] = True
            if do_upload and need_up:
                upload(iid, n, p)
                with _lock:
                    _stats["up"] += 1
            rec["up"] = True if do_upload else False
        except Exception as e:
            rec["error"] = f"{type(e).__name__}: {e}"[:120]
            with _lock:
                _stats["fail"] += 1
        out_q.put(rec)
        q.task_done()


def run(dl_workers, up_workers, limit, retry_only, do_upload):
    st = load_state()
    pages = all_pages()
    todo = []
    for iid, n in pages:
        r = st.get((iid, n), {})
        if retry_only and not r.get("error"):
            continue
        need_dl = not r.get("dl")
        need_up = do_upload and not r.get("up")
        if need_dl or need_up:
            todo.append((iid, n, need_dl, need_up))
    if limit:
        todo = todo[:limit]
    n_workers = max(dl_workers, up_workers)
    print(f"清单共 {len(pages):,} 页｜本次待办 {len(todo):,} 页｜线程 {n_workers}"
          f"｜上传 {'开' if do_upload else '关'}")
    if not todo:
        return stat()

    q, out_q = queue.Queue(maxsize=n_workers * 4), queue.Queue()
    threads = [threading.Thread(target=worker, args=(q, out_q, do_upload), daemon=True)
               for _ in range(n_workers)]
    for t in threads:
        t.start()

    def writer():
        with open(STATE, "a", encoding="utf-8") as fh:
            done = 0
            t0 = time.time()
            while done < len(todo):
                rec = out_q.get()
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                done += 1
                if done % 200 == 0:
                    fh.flush()
                    el = time.time() - t0
                    mb = _stats["bytes"] / 2**20
                    print(f"  {done:,}/{len(todo):,}  {done/el:.1f}页/秒  {mb/el:.2f}MB/s  "
                          f"下{_stats['dl']:,} 传{_stats['up']:,} 失败{_stats['fail']}  "
                          f"预计还需 {(len(todo)-done)/max(done/el,.01)/3600:.1f}h", flush=True)
            fh.flush()

    wt = threading.Thread(target=writer)
    wt.start()
    for item in todo:
        q.put(item)
    for _ in threads:
        q.put(None)
    wt.join()
    print(f"\n完成：下载 {_stats['dl']:,}（跳过 {_stats['dl_skip']:,}）"
          f"，上传 {_stats['up']:,}，失败 {_stats['fail']}")
    stat()


def stat():
    st = load_state()
    pages = all_pages() if os.path.exists(MANIFEST) else []
    dl = sum(1 for r in st.values() if r.get("dl"))
    up = sum(1 for r in st.values() if r.get("up"))
    err = sum(1 for r in st.values() if r.get("error"))
    tot = 0
    for root, _, fs in os.walk(IMG_DIR):
        tot += sum(os.path.getsize(os.path.join(root, f)) for f in fs if f.endswith(".jpg"))
    print(f"\n=== 流水线状态 ===")
    print(f"应处理 {len(pages):,} 页")
    print(f"已下载 {dl:,}  已上传 {up:,}  失败 {err:,}")
    print(f"本地占用 {tot/2**30:.1f} GB")
    if err:
        from collections import Counter
        c = Counter(r.get("error", "")[:40] for r in st.values() if r.get("error"))
        for k, v in c.most_common(5):
            print(f"  {v:>5}  {k}")
        print("  → 用 `pipeline.py retry` 重试")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", default="run", choices=["run", "stat", "retry"])
    ap.add_argument("--dl", type=int, default=24, help="下载并发（IA 实测 24 约 3.5 件/秒）")
    ap.add_argument("--up", type=int, default=32, help="上传并发（OSS 实测 32 仍在涨）")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 页，用于试跑")
    ap.add_argument("--no-upload", action="store_true", help="只下载不传 OSS")
    a = ap.parse_args()
    if a.cmd == "stat":
        stat()
    else:
        run(a.dl, a.up, a.limit, a.cmd == "retry", not a.no_upload)

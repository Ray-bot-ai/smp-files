"""全量：沿 IA 上每卷 M1750 胶卷走一遍，定出每件卷宗的起始帧（第一轮，本机 Apple Vision）。

用 ~/.ocr-vision-venv/bin/python 跑（要 pyobjc Vision）：
  ~/.ocr-vision-venv/bin/python tools/locate_frames.py [--workers 6] [--rolls 23,24]
可随时中断、重跑续上：每帧的识别文字缓存在 .roll_frames/<roll>/n0000.v.txt，图片识别完即删。
每卷走完写 roll_walk/<roll>.json（placed + 每个识别过的帧的匹配结果），日志 roll_walk/progress.log。
帧数取自 roll_walk/rolls_frames.json（IA page_numbers.json）。
"""
import argparse, json, os, sys, time, urllib.request, concurrent.futures as cf

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "tools"))
CACHE = os.path.join(HERE, ".roll_frames")
OUTD = os.path.join(HERE, "roll_walk")
INDEX_PKL = os.path.join(OUTD, "page_index.pkl")


def log(msg):
    with open(os.path.join(OUTD, "progress.log"), "a") as f:
        f.write(time.strftime("%H:%M:%S ") + msg + "\n")


PDF_DIR = os.path.join(CACHE, "_pdf")      # IA 上只有整本 PDF、没有逐页图的卷（6、12、44、45）


def fetch(ia, n, fp):
    pdf = os.path.join(PDF_DIR, f"{ia}.pdf")
    if os.path.exists(pdf):                  # 本地 PDF：第 n 页即第 n 帧，渲染成约 1200px 宽
        import fitz
        doc = fitz.open(pdf)
        if n >= doc.page_count:
            return False
        pg = doc[n]
        pix = pg.get_pixmap(matrix=fitz.Matrix(1200 / pg.rect.width, 1200 / pg.rect.width))
        pix.save(fp)
        return True
    url = f"https://archive.org/download/{ia}/page/n{n}_w1200.jpg"
    for t in range(5):
        try:
            with urllib.request.urlopen(url, timeout=90) as r:
                data = r.read()
            if len(data) > 1500:
                open(fp, "wb").write(data)
                return True
        except Exception:
            pass
        time.sleep(3 * (t + 1))
    return False


def run_roll(roll, ia, nframes):
    from walk_roll import PageIndex, walk
    from vocr_lib import vision_text
    out = os.path.join(OUTD, f"{roll}.json")
    if os.path.exists(out):
        return roll, "already done"
    idx = PageIndex(HERE, cache=INDEX_PKL)
    R = json.load(open(os.path.join(HERE, "docs/data/rolls.json")))["files"]
    prior = {i for i, v in R.items() if int(roll) in v["r"]}
    d = os.path.join(CACHE, str(roll))
    os.makedirs(d, exist_ok=True)
    pool = cf.ThreadPoolExecutor(4)
    pending = {}

    def img(n):
        return os.path.join(d, f"n{n:04d}.jpg")

    def prefetch(n):
        if 0 <= n < nframes and n not in pending and not os.path.exists(os.path.join(d, f"n{n:04d}.v.txt")):
            pending[n] = pool.submit(fetch, ia, n, img(n))

    seen = {}

    def ocr_fn(n):
        tf = os.path.join(d, f"n{n:04d}.v.txt")
        if not os.path.exists(tf):
            prefetch(n)
            ok = pending.pop(n).result()
            txt = vision_text(img(n)) if ok else ""
            open(tf, "w").write(txt)
            if os.path.exists(img(n)):
                os.remove(img(n))
        for k in (1, 2):
            prefetch(n + k)
        txt = open(tf).read()
        seen[n] = idx.match(txt)
        return txt

    t0 = time.time()
    placed, nocr = walk(nframes, ocr_fn, idx, prior=prior)
    pool.shutdown(wait=False, cancel_futures=True)
    for f in os.listdir(d):
        if f.endswith(".jpg"):
            os.remove(os.path.join(d, f))
    json.dump({"roll": roll, "ia": ia, "frames": nframes, "ocr_frames": nocr, "engine": "vision",
               "placed": placed, "seen": {str(k): v for k, v in sorted(seen.items())}},
              open(out, "w"), ensure_ascii=False)
    msg = f"roll {roll}: {len(placed)} files placed, {nocr}/{nframes} frames OCR'd, {time.time()-t0:.0f}s"
    log(msg)
    return roll, msg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--rolls", default="")
    a = ap.parse_args()
    os.makedirs(OUTD, exist_ok=True)
    info = json.load(open(os.path.join(OUTD, "rolls_frames.json")))
    todo = [(r, v["ia"], v["frames"]) for r, v in info.items()
            if v.get("frames") and (v.get("page_images") or v.get("pdf")) and (not a.rolls or r in a.rolls.split(","))]
    todo.sort(key=lambda x: int(x[0]))
    if not os.path.exists(INDEX_PKL):                 # 先在主进程建好索引缓存，免得各进程同时建
        from walk_roll import PageIndex
        PageIndex(HERE, cache=INDEX_PKL)
    log(f"start: {len(todo)} rolls, workers={a.workers}")
    with cf.ProcessPoolExecutor(a.workers) as ex:
        futs = {ex.submit(run_roll, *t): t[0] for t in todo}
        for fu in cf.as_completed(futs):
            try:
                print(*fu.result(), flush=True)
            except Exception as e:
                log(f"roll {futs[fu]} FAILED: {e!r}")
                print(futs[fu], "FAILED", e, flush=True)
    log("all done")


if __name__ == "__main__":
    main()

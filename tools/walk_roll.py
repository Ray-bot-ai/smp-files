"""沿一卷 M1750 胶卷走一遍，定出卷内每件卷宗的起始帧。

原理：IA 上的单件（smpa-files-N）是从整卷（smpa-R）里按顺序切出来的，一卷 = 各件首尾相接，
外加片头、标板和少量重拍/空白帧。所以不必整卷识别：
  1. 识别当前帧，拿文字到全库本站转录里做容错匹配（见 PageIndex），得「某件第 k 页」；
  2. 起点 = 当前帧 − k；随即用下一帧核对（应是同一件第 k+1 页），两帧都对上才落定；
  3. 落定后直接跳到「起点 + 该件页数」，即下一件的预测起点；认不出的帧（片头、空白、标板）往后挪一帧。
每件大约只识别 2–3 帧。

OCR 引擎可替换（ocr_fn: 帧号 → 文字）；匹配只信本站转录，不用 IA 自带 OCR。
"""
import collections, glob, json, math, os, pickle, re

WORD = re.compile(r"[a-z]{4,}|\d{3,}")


def toks(t):
    return set(WORD.findall((t or "").lower()))


class PageIndex:
    """全库每页的词集倒排索引。"""

    def __init__(self, repo, cache=None):
        if cache and os.path.exists(cache):
            self.pages, self.inv, self.df, self.npages = pickle.load(open(cache, "rb"))
        else:
            self.pages, inv, self.npages = [], collections.defaultdict(list), {}
            for f in glob.glob(os.path.join(repo, "docs/data/doc/*.json")):
                d = json.load(open(f))
                self.npages[d["i"]] = len(d["pages"])
                for k, p in enumerate(d["pages"]):
                    self.pages.append((d["i"], k))
                    for t in toks(p.get("en")):
                        inv[t].append(len(self.pages) - 1)
            self.inv = dict(inv)
            self.df = {t: len(v) for t, v in self.inv.items()}
            if cache:
                pickle.dump((self.pages, self.inv, self.df, self.npages), open(cache, "wb"))
        self.N = len(self.pages)

    def match(self, text, top=3):
        ts = {t for t in toks(text) if 0 < self.df.get(t, 0) < 2000}
        if len(ts) < 5:
            return []
        w = {t: math.log(self.N / self.df[t]) for t in ts}
        tot = sum(w.values())
        sc = collections.Counter()
        for t in ts:
            for idx in self.inv[t]:
                sc[idx] += w[t]
        return [(self.pages[i][0], self.pages[i][1], s / tot) for i, s in sc.most_common(top)]


def walk(n_frames, ocr_fn, index, thr=0.55, prior=None, log=None, start=0):
    """返回 {file_id: {"start": 帧号, "conf": 核对帧数, "pages": 页数}}。
    prior: 预计在本卷的卷宗集合；不在其中的匹配要求更高分（防跨卷误配）。"""
    placed, p, cache = {}, start, {}

    def ocr(n):
        if n not in cache:
            cache[n] = ocr_fn(n)
        return cache[n]

    def best(n):
        m = index.match(ocr(n))
        if not m:
            return None
        f, k, s = m[0]
        need = thr if (prior is None or f in prior) else max(thr, 0.75)
        return (f, k, s) if s >= need else None

    while p < n_frames:
        b = best(p)
        if not b:
            p += 1
            continue
        f, k, s = b
        st = p - k
        if f in placed:                       # 同一件的后续帧（重拍/多出的帧）：往后走
            p += 1
            continue
        # 用下一帧核对：应为同一件的第 k+1 页（允许多出一帧）
        ok = 0
        for q in (p + 1, p + 2):
            if q >= n_frames or k + 1 >= index.npages.get(f, 0):
                break
            m = index.match(ocr(q))
            if any(ff == f and kk in (k + 1, k + 2) and ss >= thr * 0.8 for ff, kk, ss in m):
                ok = 1
                break
        single = index.npages.get(f, 0) <= k + 1     # 一页的件、或正落在末页：无下一帧可核
        if not ok and not (single and s >= 0.85):
            p += 1
            continue
        placed[f] = {"start": st, "conf": 1 + ok, "score": round(s, 3), "pages": index.npages.get(f)}
        if log:
            log(f"  frame {p}: {f} p{k} (score {s:.2f}) → start {st}, pages {index.npages.get(f)}")
        p = max(p + 1, st + index.npages.get(f, 1))
    return placed, len(cache)

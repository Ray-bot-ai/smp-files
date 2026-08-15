"""站点构建：从 manifest.jsonl + data/*.json 生成静态站所需的全部数据文件。

检索设计（关键）：
- 中文用「归一化 bigram」。每个字先经 variants.json 归一到同一字形，再取二字滑窗。
  `國民黨` → 归一 `国民党` → bigram 国民/民党；用户输入简体走完全相同的路径，自然命中。
  不需要分词器，长度≥2 的任何中文查询都能用。
  这是把用户自建 Obsidian 插件「简繁异体通搜」的思路搬到静态站。
- 英文按词、小写。
- 索引按 token 哈希分 N 片，客户端只取用到的那几片。

为什么不用 Pagefind：它对 CJK 只做字符切分，**没有字形归一化**，
搜「国民党」命不中原件里的「國民黨」——这批档案全是繁体，这条是致命的。

用法：.venv/bin/python build_site.py
"""
import glob, hashlib, json, os, re, sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
OUT = os.path.join(HERE, "docs", "data")
SHARDS = 64

from glyphnorm import NORM, GROUPS, normalize   # 见该模块：两源并查集合并

CJK = re.compile(r"[㐀-鿿]")
WORD = re.compile(r"[a-z][a-z'\-]{1,}")

# ── 严重残破页的标记 ────────────────────────────────────────────────────────
# 只标记、不删除：原文照旧展示，旁边加一条说明，让读者知道这页不能信。
# 标记是**构建时算出来的**，不写回 data/——史料产出是不可替换的，能不动就不动。
#
# 两类残破，判据完全不同，都要覆盖：
#   ① 模型认怂：满页 □（它自己说读不出）。可自动判，全库 22 页。
#   ② 模型编造：文字通顺但内容与原件无关，**□ 数为 0**，自动判据一个也抓不到。
#      唯一可靠的检测是同页转两次比对，成本高，已决定不做全库第二遍
#      （原件影像就在旁边，读者自己一眼能核）。所以第②类走人工名单
#      unusable_pages.json，发现一页记一页。
BOX_RATIO = 0.30


def load_manual_flags():
    p = os.path.join(HERE, "unusable_pages.json")
    if not os.path.exists(p):
        return {}
    return {(r["ia_id"], r["n"]): r["reason"]
            for r in json.load(open(p, encoding="utf-8"))}


def damage_flag(iid, n, en, manual):
    """返回该页的「不可用」说明；正常页返回 None。"""
    r = manual.get((iid, n))
    if r:
        return r
    if not en:
        return None
    box = en.count("□")
    if box / len(en) >= BOX_RATIO:
        return (f"本页约 {round(box / len(en) * 100)}% 的字模型无法辨认（转录中标为 □）。"
                f"残余文字仅供定位，不可引用，请直接看左侧原件影像。")
    # 转录本身退化成重复循环。翻译那边一直有退化检测，**转录这边一直没有**，
    # 于是这种垃圾直接进了库（实见 3560/p496：「15/6/6/6/6/6/6/…」一路重复到底）。
    #
    # 判据必须挑剔，否则误伤一大片——这批档案里本来就有大量「合法的重复」：
    #   □□□□□  转录时标不可辨认的占位符（这是我们自己要求模型打的）
    #   xxxxx  打字机划掉的字，原件上就长这样
    #   ......  表格填空线
    # 所以：先剥掉分隔线，再要求**重复单元里含真正的内容字符**
    #（数字/字母/汉字，且把 x、X 排除掉）才算退化。
    probe = re.sub(r"[.·…\-_—–=*~#+　\s]{6,}", " ", en)
    real = re.compile(r"[0-9a-wyzA-WYZ一-鿿]")
    for pat in (r"([^\s])\1{25,}", r"(.{1,6}?)\1{15,}"):
        m = re.search(pat, probe)
        if m and real.search(m.group(1)):
            return ("本页转录退化成了重复循环（模型卡住反复吐同一串字符），"
                    "内容不可信。保留原样只为存证，请直接看左侧原件影像。")
    return None


SKIPPED = []


def load_doc(f):
    """读一份 data/*.json，读不了就跳过而不是让整个构建挂掉。

    全量跑批要跑好几天，期间会不断重建站点让新内容可检索。而跑批正在写
    data/，json.dump 不是原子写——正好读到写一半的文件，原来会抛
    JSONDecodeError 把整次构建带崩，等于「跑批一直在写，站点就一直建不出来」。
    跳过 + 记录，下次重建自然会带上。
    """
    try:
        return json.load(open(f, encoding="utf-8"))
    except Exception as e:
        SKIPPED.append((os.path.basename(f), type(e).__name__))
        return None


def tokens(text):
    """英文词 + 中文归一化 bigram + 每个单字。返回 set（同页重复只算一次，索引小很多）。

    为什么要单字：bigram 索引里单字**只在孤立成词时**才被索引——「工人罢工」
    只产出 工人/人罢/罢工 三个 bigram，没有「工」。搜「工」就只能命中
    「工」单独成词的那几页（实测 12,424 页里只中 8 页），单字检索等于失效。
    所以把每个 CJK 字符本身也加进索引；代价是索引 +约 20%，换来单字检索可用。
    """
    t = set()
    low = text.lower()
    t.update(WORD.findall(low))
    # 中文：抽出连续汉字段，归一化后取 bigram
    for seg in re.findall(r"[㐀-鿿]+", text):
        n = normalize(seg)
        if len(n) == 1:
            t.add(n)
            continue
        for i in range(len(n) - 1):
            t.add(n[i:i + 2])
        t.update(n)          # 每个单字也是 token（见上面注释）
    return t


def shard_of(tok):
    # 必须与 docs/assets/search.js 的 md5hex()（实为 SHA-256）完全一致，
    # 否则客户端会去错误的分片里找 token —— 永远零命中，且不报任何错。
    return int(hashlib.sha256(tok.encode()).hexdigest()[:4], 16) % SHARDS


# ── 目录（全库，不依赖 OCR）──────────────────────────────────
def build_catalog():
    seen = {}
    for line in open(os.path.join(HERE, "manifest.jsonl"), encoding="utf-8"):
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r["ia_id"] not in seen or (seen[r["ia_id"]].get("error") and not r.get("error")):
            seen[r["ia_id"]] = r
    done = {os.path.basename(f)[:-5] for f in glob.glob(os.path.join(DATA, "*.json"))}
    items = []
    for r in seen.values():
        if r.get("error") or not r.get("pages"):
            continue
        items.append({"i": r["ia_id"].replace("smpa-files-", ""),   # 省体积
                      "t": r.get("title", ""), "s": r.get("series", ""),
                      "n": r.get("nara_file_no", ""), "p": r["pages"],
                      "d": 1 if r["ia_id"] in done else 0})
    items.sort(key=lambda x: x["t"])
    os.makedirs(OUT, exist_ok=True)
    json.dump({"items": items,
               "total_files": len(items),
               "total_pages": sum(x["p"] for x in items),
               "done_files": sum(x["d"] for x in items),
               "done_pages": sum(x["p"] for x in items if x["d"])},
              open(os.path.join(OUT, "catalog.json"), "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))
    return len(items), sum(x["p"] for x in items)


def build_titleidx(items):
    """全库标题索引：token → [件短号…]，单文件不分片。

    为什么要有它：正文倒排索引只覆盖**已转录**部分（约 1/5），
    其余 60% 的卷宗在检索页完全隐形。标题索引覆盖全部 3,866 件的标题，
    让「先按标题找到卷宗、再去 IA 看原件」成为可能——尤其对还没转到的题材。
    分片一致性（shard_of 两端必须一致）的坑在这里不存在：单文件，无分片。
    但 token 语义仍须与 search.js 的 tokenize() 完全一致（同一套
    tokens()/normalize，正文索引已验过这条），否则同样零命中不报错。
    """
    post = {}
    for x in items:
        for tok in tokens(x["t"]):
            post.setdefault(tok, []).append(x["i"])
    for v in post.values():
        v.sort()
    json.dump(post, open(os.path.join(OUT, "titleidx.json"), "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))
    return len(post)


# ── 单件全文 + 倒排索引 ──────────────────────────────────────
def build_docs_and_index():
    os.makedirs(os.path.join(OUT, "doc"), exist_ok=True)
    os.makedirs(os.path.join(OUT, "idx"), exist_ok=True)
    inv = defaultdict(list)          # token -> [[doc, page], ...]
    ndoc = npage = nbad = 0
    manual = load_manual_flags()
    for f in sorted(glob.glob(os.path.join(DATA, "*.json"))):
        d = load_doc(f)
        if d is None:
            continue
        short = d["ia_id"].replace("smpa-files-", "")
        pages = []
        for q in d.get("page_data", []):
            en = (q.get("en") or "").strip()
            zh = (q.get("zh") or "").strip()
            bad = damage_flag(d["ia_id"], q["n"], en, manual)
            if bad:
                nbad += 1
            pages.append({"n": q["n"], "en": en, "zh": zh,
                          **({"bad": bad} if bad else {}),
                          # 译文出过问题就照实说明，不管最后有没有留下内容：
                          # 有内容 = 部分可读（已清理重复段），没内容 = 整页译不出来。
                          # 留个空白让人以为是漏了，比说清楚更糟。
                          **({"zhbad": q["zh_status"]} if q.get("zh_status") else {}),
                          **({"zhpart": True} if q.get("zh_partial") and zh else {}),
                          **({"note": q["zh_note"]} if q.get("zh_note") else {})})
            if not en and not zh:
                continue
            npage += 1
            for tok in tokens(en + "\n" + zh):
                inv[tok].append([short, q["n"]])
        json.dump({"i": short, "t": d.get("title", ""), "s": d.get("series", ""),
                   "p": d.get("pages", 0), "ia": d.get("ia_url", ""),
                   "ia_ocr": (d.get("ia_ocr") or "")[:20000],
                   "pages": pages},
                  open(os.path.join(OUT, "doc", f"{short}.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, separators=(",", ":"))
        ndoc += 1
    # 分片写出。丢弃出现在超过 40% 页面的 token（停用词级别，检索无意义且占体积）。
    # 但中文单字**一律保留**：单字是合法检索词（工/党/厂/会/俄…），
    # 40% 阈值会把它们几乎全丢光；连「的」这种也保留——搜它是噪声，
    # 但静默丢掉单字会让用户撞上「搜单字永远零命中」的坑（实测抓出）。
    cutoff = max(20, int(npage * 0.4))
    shards = defaultdict(dict)
    kept = 0
    for tok, posts in inv.items():
        is_cjk1 = len(tok) == 1 and "\u3400" <= tok <= "\u9fff"
        if not is_cjk1 and len(posts) > cutoff:
            continue
        shards[shard_of(tok)][tok] = posts
        kept += 1
    for s in range(SHARDS):
        json.dump(shards.get(s, {}),
                  open(os.path.join(OUT, "idx", f"{s}.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, separators=(",", ":"))
    return ndoc, npage, len(inv), kept, nbad


def build_variants_min():
    """给前端的紧凑字形表：每组一个字符串，首字为归一形。约 3,100 组。"""
    groups = sorted(GROUPS)
    p = os.path.join(OUT, "variants.min.json")
    json.dump(groups, open(p, "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))
    return len(groups), os.path.getsize(p)


def build_triage():
    """人工扫图队列：把已转录的页按「可疑程度」排序，供人一眼一眼扫。

    为什么是人来扫而不是算法判：**大模型出大错的页，恰恰是人也读不出来的页**。
    这类页写算法很难（实测成像指标单指标 AUC 只有 0.69–0.76，把误伤压到 0 时
    只抓得到 8% 的坏页），但人扫一眼就知道。所以别指望判据，把人的注意力用好。

    成像指标在这里唯一的正当用途：**给人排队**，让有限的目光先落在最可疑的页上，
    而不是替人下结论。没有 imgqual.json 时退化为按 □ 比例排序，一样能用。
    """
    qpath = os.path.join(HERE, "imgqual.json")
    qual = json.load(open(qpath, encoding="utf-8")) if os.path.exists(qpath) else {}
    med = mad = None
    if qual:
        import statistics as st
        keys = ("contrast", "sharp", "ink", "mean", "blown")
        med = {k: st.median([v[k] for v in qual.values()]) for k in keys}
        mad = {k: max(st.median([abs(v[k] - med[k]) for v in qual.values()]), 1e-6)
               for k in keys}

    rows, titles = [], {}
    for f in sorted(glob.glob(os.path.join(DATA, "*.json"))):
        d = load_doc(f)
        if d is None:
            continue
        short = d["ia_id"].replace("smpa-files-", "")
        titles[short] = d.get("title", "")
        for q in d.get("page_data", []):
            en = (q.get("en") or "").strip()
            if not en:
                continue
            m = qual.get(f"{d['ia_id']}#{q['n']}")
            if m and med:
                score = max(abs(m[k] - med[k]) / mad[k] for k in med)
            else:                      # 没有影像指标就用 □ 比例兜底
                score = en.count("□") / len(en) * 10
            rows.append([short, q["n"], round(score, 2)])
    rows.sort(key=lambda r: -r[2])
    json.dump({"pages": rows, "titles": titles, "scored": bool(qual)},
              open(os.path.join(OUT, "triage.json"), "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))
    return len(rows), bool(qual)


def build_progress(nd, npg, nbad):
    """把「干到哪了」写成数据文件，首页去读它。

    为什么不直接写死在 index.html 里：写死就会过期。本项目实见——
    首页上「已完成 — / ~90,000、译文待启动、站点将在全部完成后发布」
    挂了很久，而那时早就转录了 7,600 页、译文也跑完、站点也上线了。
    现在每次 build_site 都会重算，改不改 HTML 都不会再说谎。
    """
    files_total = pages_total = 0
    for line in open(os.path.join(HERE, "manifest.jsonl"), encoding="utf-8"):
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("error") or r["ia_id"].startswith("smpa-") and "-files-" not in r["ia_id"]:
            continue        # 60 件 smpa-N 是缩微整卷，与散件重复，不计
        files_total += 1
        pages_total += r.get("pages", 0) or 0
    zh = 0
    for f in glob.glob(os.path.join(DATA, "*.json")):
        d = load_doc(f)
        if d is None:
            continue
        zh += sum(1 for q in d.get("page_data", []) if (q.get("zh") or "").strip())
    p = {"files_done": nd, "files_total": files_total,
         "pages_done": npg, "pages_total": pages_total,
         "zh_pages": zh, "unusable": nbad,
         "updated": __import__("time").strftime("%Y-%m-%d")}
    json.dump(p, open(os.path.join(OUT, "progress.json"), "w", encoding="utf-8"),
              ensure_ascii=False)
    return p


if __name__ == "__main__":
    nf, np_ = build_catalog()
    print(f"目录：{nf:,} 件 / {np_:,} 页 → docs/data/catalog.json")
    ntoks_t = build_titleidx(json.load(open(os.path.join(OUT, "catalog.json"),
                                            encoding="utf-8"))["items"])
    print(f"标题索引：{ntoks_t:,} token → docs/data/titleidx.json（覆盖全库标题，含未转录）")
    nd, npg, ntok, kept, nbad = build_docs_and_index()
    idx_mb = sum(os.path.getsize(p) for p in glob.glob(os.path.join(OUT, "idx", "*.json"))) / 2**20
    doc_mb = sum(os.path.getsize(p) for p in glob.glob(os.path.join(OUT, "doc", "*.json"))) / 2**20
    print(f"全文：{nd} 件 / {npg:,} 页；token {ntok:,}（保留 {kept:,}，去掉高频停用）")
    print(f"标记为不可用的页：{nbad}（内容保留，只加说明）")
    print(f"索引 {idx_mb:.2f} MB / {SHARDS} 片（均 {idx_mb*1024/SHARDS:.0f} KB）；正文 {doc_mb:.2f} MB")
    if SKIPPED:
        print(f"⚠ 跳过 {len(SKIPPED)} 个读不了的文件（多半是跑批正在写）：{SKIPPED[:5]}")
        print("  它们这次不会进索引，下次重建会自动带上。")
    ng, gb = build_variants_min()
    print(f"字形表：{ng:,} 组 / {gb/1024:.0f} KB → docs/data/variants.min.json")
    nt, scored = build_triage()
    print(f"人工扫图队列：{nt:,} 页 → docs/data/triage.json"
          f"（{'按成像指标排序' if scored else '按 □ 比例排序，缺 imgqual.json'}）")
    pr = build_progress(nd, npg, nbad)
    print(f"进度：{pr['files_done']}/{pr['files_total']:,} 件、"
          f"{pr['pages_done']:,}/{pr['pages_total']:,} 页、译文 {pr['zh_pages']:,} 页 "
          f"→ docs/data/progress.json")

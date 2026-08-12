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
    return None


def tokens(text):
    """英文词 + 中文归一化 bigram。返回 set（同页重复只算一次，索引小很多）。"""
    t = set()
    low = text.lower()
    t.update(WORD.findall(low))
    # 中文：抽出连续汉字段，归一化后取 bigram
    for seg in re.findall(r"[㐀-鿿]+", text):
        n = normalize(seg)
        if len(n) == 1:
            t.add(n)
        for i in range(len(n) - 1):
            t.add(n[i:i + 2])
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


# ── 单件全文 + 倒排索引 ──────────────────────────────────────
def build_docs_and_index():
    os.makedirs(os.path.join(OUT, "doc"), exist_ok=True)
    os.makedirs(os.path.join(OUT, "idx"), exist_ok=True)
    inv = defaultdict(list)          # token -> [[doc, page], ...]
    ndoc = npage = nbad = 0
    manual = load_manual_flags()
    for f in sorted(glob.glob(os.path.join(DATA, "*.json"))):
        d = json.load(open(f, encoding="utf-8"))
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
                          # 译不出来的终态：站点上照实说明，不要留个空白让人以为漏了
                          **({"zhbad": q["zh_status"]} if q.get("zh_status") and not zh else {}),
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
    # 分片写出。丢弃出现在超过 40% 页面的 token（停用词级别，检索无意义且占体积）
    cutoff = max(20, int(npage * 0.4))
    shards = defaultdict(dict)
    kept = 0
    for tok, posts in inv.items():
        if len(posts) > cutoff:
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
        d = json.load(open(f, encoding="utf-8"))
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
    nd, npg, ntok, kept, nbad = build_docs_and_index()
    idx_mb = sum(os.path.getsize(p) for p in glob.glob(os.path.join(OUT, "idx", "*.json"))) / 2**20
    doc_mb = sum(os.path.getsize(p) for p in glob.glob(os.path.join(OUT, "doc", "*.json"))) / 2**20
    print(f"全文：{nd} 件 / {npg:,} 页；token {ntok:,}（保留 {kept:,}，去掉高频停用）")
    print(f"标记为不可用的页：{nbad}（内容保留，只加说明）")
    print(f"索引 {idx_mb:.2f} MB / {SHARDS} 片（均 {idx_mb*1024/SHARDS:.0f} KB）；正文 {doc_mb:.2f} MB")
    ng, gb = build_variants_min()
    print(f"字形表：{ng:,} 组 / {gb/1024:.0f} KB → docs/data/variants.min.json")
    pr = build_progress(nd, npg, nbad)
    print(f"进度：{pr['files_done']}/{pr['files_total']:,} 件、"
          f"{pr['pages_done']:,}/{pr['pages_total']:,} 页、译文 {pr['zh_pages']:,} 页 "
          f"→ docs/data/progress.json")

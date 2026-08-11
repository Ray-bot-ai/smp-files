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

# ── 字形归一化 ────────────────────────────────────────────────
_V = json.load(open(os.path.join(HERE, "variants.json"), encoding="utf-8"))
_EXTRA = ["吴吳", "吕呂", "强強", "么麽", "户戶", "为爲為", "别別", "沉沈",
          "画畫", "冰氷", "污汙汚", "床牀", "秘祕", "杯盃", "群羣", "峰峯"]
NORM = {}
for _g in list(_V.values()) + _EXTRA:
    for _c in _g:
        NORM[_c] = _g[0]

CJK = re.compile(r"[㐀-鿿]")
WORD = re.compile(r"[a-z][a-z'\-]{1,}")


def normalize(s):
    return "".join(NORM.get(c, c) for c in s)


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
    ndoc = npage = 0
    for f in sorted(glob.glob(os.path.join(DATA, "*.json"))):
        d = json.load(open(f, encoding="utf-8"))
        short = d["ia_id"].replace("smpa-files-", "")
        pages = []
        for q in d.get("page_data", []):
            en = (q.get("en") or "").strip()
            zh = (q.get("zh") or "").strip()
            pages.append({"n": q["n"], "en": en, "zh": zh,
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
    return ndoc, npage, len(inv), kept


def build_variants_min():
    """给前端的紧凑字形表：每组一个字符串，首字为归一形。约 3,100 组。"""
    groups = sorted({g for g in list(_V.values())} | set(_EXTRA))
    p = os.path.join(OUT, "variants.min.json")
    json.dump(groups, open(p, "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))
    return len(groups), os.path.getsize(p)


if __name__ == "__main__":
    nf, np_ = build_catalog()
    print(f"目录：{nf:,} 件 / {np_:,} 页 → docs/data/catalog.json")
    nd, npg, ntok, kept = build_docs_and_index()
    idx_mb = sum(os.path.getsize(p) for p in glob.glob(os.path.join(OUT, "idx", "*.json"))) / 2**20
    doc_mb = sum(os.path.getsize(p) for p in glob.glob(os.path.join(OUT, "doc", "*.json"))) / 2**20
    print(f"全文：{nd} 件 / {npg:,} 页；token {ntok:,}（保留 {kept:,}，去掉高频停用）")
    print(f"索引 {idx_mb:.2f} MB / {SHARDS} 片（均 {idx_mb*1024/SHARDS:.0f} KB）；正文 {doc_mb:.2f} MB")
    ng, gb = build_variants_min()
    print(f"字形表：{ng:,} 组 / {gb/1024:.0f} KB → docs/data/variants.min.json")

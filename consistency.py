"""件内自洽性检查：不需要外部参照就能定位「必有一方是错」的位置。

来由（实测）：smpa-files-3115 这一件 22 页里，同一个人名出现三个版本——
p1「汪宗明」、p2「汪正祥」、p3「汪玉祥」。正确的是汪正祥（p2 正文 + 卷宗标题
Wang Cheng-Hsiang + 手写批注三处佐证），模型三次只对一次。
**这种自相矛盾本身就是检测信号**：一件里同一个名字三种写法，必有两种是错的。

设计原则：**宁缺毋滥**。第一版把简繁异体、同姓不同人、被误当门牌号的日期全报了出来，
真信号被几十条误报淹没——一个天天喊狼来了的质检工具等于没有。
现在只报高置信度的：只差一个字、且排除简繁异体、且长度足够。

用法：.venv/bin/python consistency.py [ia_id ...]
"""
import glob, json, os, re, sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")

from glyphnorm import NORM as ST, normalize   # 见该模块：两源并查集合并

CJK = re.compile(r"[一-鿿]{3,4}")               # 3–4 字才算人名，2 字误报太多
# 真门牌号：第一段 3–4 位数（上海租界门牌如 1143/106），排除 21/11 这种日期
ADDR = re.compile(r"\b(\d{3,4})\s*/\s*(\d{1,4}[A-Za-z]?)\b")
DATE_NUM = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{2,4})\b")


def norm(s):
    return normalize(s)


def one_char_apart(a, b):
    """同长、恰好差一个字，且不是简繁差异 → 高置信度的「同名不同写法」。"""
    if len(a) != len(b) or a == b:
        return False
    if norm(a) == norm(b):
        return False                      # 简繁/异体，不是错误
    return sum(1 for x, y in zip(a, b) if x != y) == 1


def check(doc):
    issues = []
    pages = [q for q in doc.get("page_data", []) if q.get("en")]

    # ① 汉字人名：只报「同长且只差一字」的
    where = defaultdict(set)
    for q in pages:
        for m in CJK.findall(q["en"]):
            where[m].add(q["n"])
    # 按「归一化后的骨架」聚类，把 A/B/C 三种写法归成一组而不是报三对
    # 先按归一化形式合并：顧阿新 与 顾阿新 是同一个名字的两种字形，
    # 不先合并的话会被拆成两个簇，反而虚增标记数（第一版就是这么错的）。
    # surface: 归一形 → 正文中真实出现的写法。**报告必须用真实写法**——
    # 报归一形的话，正文是繁体而标记是简体，人在页面上根本找不到，
    # 高亮也会失效，抽样校验工具就废了。踩过。
    bynorm = defaultdict(set)
    for n in where:
        bynorm[norm(n)].add(n)
    surface = {k: v for k, v in bynorm.items()}
    for k, forms in bynorm.items():
        for f in forms:
            where[k] |= where[f]
    clusters = defaultdict(set)
    names = sorted(bynorm)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if one_char_apart(a, b):
                key = min(a, b)
                clusters[key] |= {a, b}
    merged = []
    for k, grp in clusters.items():
        for m in merged:
            if m & grp:
                m |= grp; break
        else:
            merged.append(set(grp))
    for grp in merged:
        ps = sorted(set().union(*(where[n] for n in grp)))
        real = sorted(set().union(*(surface.get(n, {n}) for n in grp)))
        issues.append(("汉字名不一致", " / ".join(real), ps))

    # ② 门牌号：只报第一段相同、第二段接近的（同一地址的不同转录）
    addr = defaultdict(set)
    for q in pages:
        for m in ADDR.finditer(q["en"]):
            addr[(m.group(1), m.group(2))].add(q["n"])
    bystreet = defaultdict(list)
    for (a, b), ps in addr.items():
        bystreet[a].append((b, ps))
    for a, lst in bystreet.items():
        if len(lst) > 1:
            forms = sorted({b for b, _ in lst})
            if len(forms) > 1 and all(len(f) == len(forms[0]) for f in forms):
                if sum(1 for x, y in zip(forms[0], forms[1]) if x != y) <= 1:
                    ps = sorted(set().union(*(set(p) for _, p in lst)))
                    issues.append(("门牌号不一致",
                                   " / ".join(f"{a}/{f}" for f in forms), ps))

    # ③ 转录过短：可能整页漏转
    short = [q["n"] for q in pages if len(q["en"].strip()) < 20]
    if short:
        issues.append(("转录过短", f"{len(short)} 页不足 20 字符", short))
    return issues


def main(ids=None):
    files = ([os.path.join(DATA, f"{i}.json") for i in ids] if ids
             else sorted(glob.glob(os.path.join(DATA, "*.json"))))
    tot = Counter(); npages = 0; nflag = 0
    for f in files:
        if not os.path.exists(f):
            continue
        doc = json.load(open(f, encoding="utf-8"))
        npages += doc.get("pages_done", 0)
        issues = check(doc)
        print(f"\n█ {doc['ia_id']}  {doc['pages']}页  {doc['title'][:52]}")
        if not issues:
            print("   件内自洽")
        for kind, detail, ps in issues:
            tot[kind] += 1
            nflag += len(ps)
            pp = ",".join(f"p{n}" for n in ps[:10]) + ("…" if len(ps) > 10 else "")
            print(f"   [{kind}] {detail}   → {pp}")
    print(f"\n{'='*62}")
    print(f"共 {npages} 页，标记 {sum(tot.values())} 处：{dict(tot) or '无'}")
    print("这些位置应优先比对原件影像——件内矛盾必有一方错。")


if __name__ == "__main__":
    main(sys.argv[1:] or None)

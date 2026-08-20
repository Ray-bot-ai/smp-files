"""从转录正文里抽年份，给每件档案打年份标签。

**这是线索，不是事实。** 年份来自 OCR，数字是模型最容易错的一类
（本项目实测：旧 OCR 把 May 30 读成 May 50；新转录准得多但仍会错）。
标签的用途是缩小检索范围，不能反过来当作「此件属于某年」的证据——
要确定日期，回原件影像看。

抽取分三档，按可信度加权，因为不是所有「19xx」都同样可靠：
  权重 3  表头日期行  `Date February 12, 1938.` —— 报告自己的日期字段，最可信
  权重 3  月名+日+年  `March 23rd. 1937`
  权重 2  日.月.两位年 `13.12.44` —— SMP 表格里极常见，需补世纪
  权重 1  裸四位年     正文里随口提到的年份，可能是引述往事，不代表本件年代

只有加权分达到阈值的年份才进标签，避免正文里偶然提一句「自 1911 年以来」
就把整件标成 1911。
"""
import glob, json, os, re, sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
LO, HI = 1894, 1950          # 馆藏 1894–1949，只放宽一年。再往后基本是数字误读：
# 实见 22/12/52 其实是 32（5↔3 是本项目记录在案的混淆）

MONTH = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?"
P_DATELINE = re.compile(r"Dated?[^\n]{0,40}?((?:18|19)\d{2})")
P_MONTHY = re.compile(MONTH + r"\s+\d{1,2}(?:st|nd|rd|th)?[.,]?\s*((?:18|19)\d{2})")
P_DMY2 = re.compile(r"(?<![\d./-])(\d{1,2})[./-](\d{1,2})[./-](\d{2})(?![\d./-])")
P_BARE = re.compile(r"(?<![\d.,/-])((?:18|19)\d{2})(?![\d])")


def _yy(two):
    """两位年补世纪。馆藏 1894–1949：00–52 → 19xx，94–99 → 18xx。"""
    v = int(two)
    if v <= 50:
        return 1900 + v
    if v >= 94:
        return 1800 + v
    return None


def year_scores(text):
    """返回 {年份: 加权分}。"""
    sc = Counter()
    for m in P_DATELINE.finditer(text):
        y = int(m.group(1))
        if LO <= y <= HI: sc[y] += 3
    for m in P_MONTHY.finditer(text):
        y = int(m.group(1))
        if LO <= y <= HI: sc[y] += 3
    for m in P_DMY2.finditer(text):
        # 校验日/月讲不讲得通。不校验的话档号、门牌、表格数字串都会被当成日期
        # （实见 224 被一处孤立数字标成 1901）。
        d1, d2 = int(m.group(1)), int(m.group(2))
        if not ((1 <= d1 <= 31 and 1 <= d2 <= 12) or (1 <= d1 <= 12 and 1 <= d2 <= 31)):
            continue
        y = _yy(m.group(3))
        if y and LO <= y <= HI: sc[y] += 2
    for m in P_BARE.finditer(text):
        y = int(m.group(1))
        if LO <= y <= HI: sc[y] += 1
    return sc


def file_years(text, min_score=3, keep_frac=0.15):
    """这一件的年份标签。

    min_score：低于这个分的年份丢掉（单次裸提及=1分，挡掉「自1911年以来」这类）。
    keep_frac：再按最高分的比例过滤，只留真正有分量的几个年份。
    """
    sc = year_scores(text)
    if not sc:
        return [], None
    top = max(sc.values())
    keep = sorted(y for y, v in sc.items() if v >= min_score and v >= top * keep_frac)
    if not keep:
        keep = [max(sc, key=lambda y: (sc[y], -y))]
    main = max(keep, key=lambda y: (sc[y], -y))
    return keep, main


def doc_text(d):
    return "\n".join((q.get("en") or "") for q in d.get("page_data", []))


# 标题里的年份权重最高：题名来自 NARA 编的纸本著录，是**人工编目**，不是 OCR。
# 实测把它并进来能直接修掉一批数字混淆（1935 被读成 1933、1944 读成 1934 之类）。
P_TITLE_Y = re.compile(r"(?<!\d)(18[6-9]\d|19[0-4]\d)(?!\d)")
P_TITLE_DMY = re.compile(r"(?<![\d./-])(\d{1,2})[./-](\d{1,2})[./-](\d{2})(?![\d./-])")


def title_years(title):
    """题名里的年份。题名来自 NARA 人工编目，比正文 OCR 可信。"""
    ys = [int(x) for x in P_TITLE_Y.findall(title)]
    for m in P_TITLE_DMY.finditer(title):
        d1, d2 = int(m.group(1)), int(m.group(2))
        if (1 <= d1 <= 31 and 1 <= d2 <= 12) or (1 <= d1 <= 12 and 1 <= d2 <= 31):
            y = _yy(m.group(3))
            if y: ys.append(y)
    return [y for y in ys if LO <= y <= HI]


def build():
    out = {}
    for f in sorted(glob.glob(os.path.join(DATA, "*.json"))):
        d = json.load(open(f, encoding="utf-8"))
        sc = year_scores(doc_text(d))
        tys = title_years(d.get("title", ""))
        for y in tys:
            sc[y] += 4                         # 题名是人工编目，高于任何 OCR 证据
        if not sc:
            continue
        top = max(sc.values())
        keep = sorted(y for y, v in sc.items() if v >= 3 and v >= top * 0.15)
        keep = sorted(set(keep) | set(tys))    # 题名年一律保留
        # **没有兜底**：一条证据都不够格就不给标签。宁可这件没有年份，
        # 也不要拿一次孤立提及去标它——错标签比没标签更坏，因为用户会拿它去筛，
        # 而被筛掉的东西是看不见的。
        if not keep:
            continue
        main = max(keep, key=lambda y: (sc[y], -y))
        out[d["ia_id"].replace("smpa-files-", "")] = {
            "y": keep, "m": main, **({"t": True} if tys else {})}
    return out


if __name__ == "__main__":
    y = build()
    json.dump(y, open(os.path.join(HERE, "years.json"), "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))
    spans = Counter(len(v["y"]) for v in y.values())
    print(f"有年份标签的件：{len(y):,}")
    print("每件标签数分布：", dict(sorted(spans.items())[:8]))
    allm = Counter(v["m"] for v in y.values())
    print("主年份 top10：", allm.most_common(10))

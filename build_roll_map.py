"""卷宗 → M1750 roll 对照 → docs/data/rolls.json

依据：m1750_appendix_a.tsv（NARA 说明册附录 A，MinerU 重做 OCR 并对照原图核对过，见其文件头；
由 tools/build_appendix_a.py 生成）。辅以 m1750_rolls.tsv（说明册卷次总表，只用于附录 A 的空当）。
不用 IA 自带 OCR。

一、认出每件卷宗「自己的号」——全部取自本站转录，规则来自逐件阅读样本：
  cover    封面标签：第一张有字的页只写号码（"D-7758"、"N441(c)"、"I.O. 9701"、
           "D-7544 & D-7545"）。一件 IA 卷宗可能合了几个 SMP 卷宗，标签上的号全收。
           字头被认错的（"59-5671"、"89-4543"）只有在别处出现「D + 同一号码」时才收。
  registry 没有封面标签时，用登记章（"REGISTRY No. D 5766"、"No. S. B. D. 2042"、
           "I. O. REG ⏎ No. 338"）：取出现页数最多的号。并列第一时，若查出的卷相同照收，否则不给。
  title    以上都没有、而 IA 标题本身就是号码时（"D7544"、"D9948-3"）用标题。
  排除交叉引用（"See D 8107"、"(see file D-5703)"）和表格编号（"FM. L. D. 1"）。
二、到附录 A 查 roll（连后缀比：N-418 在 roll 61，N-418C 在 roll 60）：
  exact    附录 A 有此号且标了 roll；只有后缀对不上时按主号查，主号下各条 roll 一致才收；
  nearby   附录 A 无此号，按号码排序前后相邻两条 roll 相同 → 推定为该卷；
  range    附录 A 的空当（如它根本没列 D-7734–8102，即 Roll 33–40）：用卷次总表的号码区间，
           且总表给出的卷必须落在空当前后两条的卷之间，否则不给；
  none     其余一律不给（宁缺毋滥）。
用法： .venv/bin/python build_roll_map.py [--report 路径]
"""
import bisect, collections, csv, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "docs", "data", "rolls.json")
APPX = os.path.join(HERE, "m1750_appendix_a.tsv")
SUMMARY = os.path.join(HERE, "m1750_rolls.tsv")
ROLL_IA = {r: f"smpa-{r}" for r in range(1, 68)}
ROLL_IA[1] = "smpa-1-3"
ROLL_ON_IA = set(range(1, 64)) - {29, 39}          # IA 上实有的 61 卷
SERIES_OF = {"D Files": "D", "N Files": "N", "U Files": "U", "IO Files": "IO"}
ORDER = {"D": 0, "N": 1, "U": 2, "IO": 3, "CID": 4}

# 后缀：字母须紧贴或隔一个空格（不跨行），且后面不能再接号码（那是下一个号的字头）
SUF = r"(?:[ ]?[A-Z](?![A-Za-z])(?![ ]?[-–.:#]?[ ]?\d))?(?:[ ]?/[ ]?\d{1,3}[A-Z]?)?(?:[ ]?\([ ]?[A-Za-z][ ]?\))?"
# 字头：IO 的 OCR 变体（1.0.、J.O.、Y.O.）必须带点，免得把普通数字 "10 1234" 读成 IO
PFX = r"(?:C\.?\s?I\.?\s?D\.?|I\.?\s?O\.?|[1lJY]\.\s?[O0]\.|I\.\s?0\.|D|N|U)"
TOK = re.compile(rf"(?<![A-Za-z0-9])({PFX})\s?[-–.:#]?\s?-?\s?(\d{{1,5}})({SUF})")
GARBLED = re.compile(r"(?m)^\s*(\d{1,2})\s?-\s?(\d{3,5})\s*$")
IO_REG = re.compile(r"(?<![A-Za-z])[IJYS1l]\s?[.:]?\s?[O0]\s?[.:]?\s?REG\.?\s*(?:No\.?)?\s*(\d{1,5})(?!\d)")
REG_CTX = re.compile(r"(REGIST|REG\b|REG\.|File\s+No|FILE\s+NO|\bNo\.?\s|S\.\s?B\.\s?D|S\.\s?D\.\s?D|C\.\s?I\.\s?D)", re.I)
XREF = re.compile(r"(see|also|refer|cf\.|files?\s*$|\(\s*$)", re.I)
FORMNO = re.compile(r"(F\s?M\.?|P\s?M\.?|Form)\s*L\.\s*$", re.I)
TITLE_NO = re.compile(r"^\s*(D|N|U|IO)\s?-?\s?(\d{2,5})(?:\s?[-/]\s?\d{1,3})?\s*$")


def norm_series(s):
    s = re.sub(r"[^A-Z0-9]", "", s.upper())
    if re.fullmatch(r"[IJY1L][O0]", s) or s == "IO":
        return "IO"
    return s


def norm_file(no, suf=""):
    return re.sub(r"[\s()]", "", (no + suf).upper())


def base_no(f):
    m = re.match(r"\d+", f)
    return int(m.group()) if m else None


def rolls_of(rollstr):
    return [int(x) for x in re.findall(r"\d+", rollstr or "")]


def c_class(f):
    """带 (C) 的卷宗另摄在单独的卷上（D 的在 Roll 59，N 的在 Roll 60），推定时与普通卷宗分开。"""
    return bool(re.match(r"\d+(?:[A-Z]?/[\w]*)?C\b|\d+C", f)) or "(C)" in f.upper()


def load_appx():
    rows = [r for r in csv.DictReader((l for l in open(APPX, encoding="utf-8") if not l.startswith("#")), delimiter="\t")]
    by_key, by_base, seq = collections.defaultdict(list), collections.defaultdict(list), collections.defaultdict(list)
    for r in rows:
        r["key"] = norm_file(r["file"])
        by_key[(r["series"], r["key"])].append(r)
        b = base_no(r["key"])
        if b is not None:
            cc = c_class(r["file"])
            by_base[(r["series"], b, cc)].append(r)
            if r["roll"]:
                seq[(r["series"], cc)].append((b, r["roll"], r["pdf_page"]))
    for s in seq:
        seq[s].sort()
    return by_key, by_base, seq


def load_summary():
    lines = [l for l in open(SUMMARY, encoding="utf-8") if not l.startswith("#")]
    out = []
    for r in csv.DictReader(lines, delimiter="\t"):
        out.append((int(r["roll"]), (ORDER[r["from_series"]], int(r["from_no"])), (ORDER[r["to_series"]], int(r["to_no"]))))
    return out


# ---------- 认号 ----------
def cover_numbers(pages, want):
    cover = next((t for t in pages if t.strip()), "")
    if not (0 < len(re.sub(r"\s", "", cover)) <= 60):
        return []
    toks = [(norm_series(m.group(1)), norm_file(m.group(2), m.group(3))) for m in TOK.finditer(cover)]
    rest = TOK.sub(" ", cover)
    garbled = [g.group(2) for g in GARBLED.finditer(rest)]
    rest = GARBLED.sub(" ", rest)
    # 标签页上除号码外只允许极少的连接字（&、and、Pt.、圈码等）
    if len(re.sub(r"[\W_]|and|pt|part", "", rest, flags=re.I)) > 6:
        return []
    if want:
        toks = [t for t in toks if t[0] == want]
    if any(base_no(f) is not None and base_no(f) >= 100 for _, f in toks):   # 有正经号时丢掉一两位数的碎片
        toks = [t for t in toks if base_no(t[1]) is None or base_no(t[1]) >= 100 or t[0] == "IO"]
    later = "\n".join(pages[1:])
    for g in garbled:
        s = want or "D"
        if re.search(rf"(?<![A-Za-z0-9]){s}\s?[-.]?\s?{g}(?!\d)", later):
            toks.append((s, g))
    return list(dict.fromkeys(toks))


def registry_votes(pages, want):
    votes = collections.Counter()
    for t in pages:
        seen = set()
        for m in TOK.finditer(t):
            pre = t[max(0, m.start() - 40):m.start()]
            if XREF.search(pre[-14:]) or FORMNO.search(pre[-8:]) or not REG_CTX.search(pre):
                continue
            s = norm_series(m.group(1))
            if s in ("D", "N", "U") and len(m.group(2)) <= 2:   # 一两位数多是被拆碎的号（"D. 65 82"）
                continue
            if not want or s == want:
                seen.add((s, norm_file(m.group(2), m.group(3))))
        if not want or want == "IO":
            for m in IO_REG.finditer(t):
                seen.add(("IO", m.group(1)))
        for k in seen:
            votes[k] += 1
    return votes


# ---------- 查表 ----------
def lookup(key, A, summary):
    by_key, by_base, seq = A
    s, f = key
    hit = [r for r in by_key.get((s, f), []) if r["roll"]]
    if hit:
        return sorted({x for r in hit for x in rolls_of(r["roll"])}), "exact", hit[0]["pdf_page"]
    b = base_no(f)
    if b is None:
        return [], "none", None
    cc = c_class(f)
    if f != str(b):
        same = [r for r in by_base.get((s, b, cc), []) if r["roll"]]
        rs = {tuple(rolls_of(r["roll"])) for r in same}
        if len(rs) == 1:
            return list(next(iter(rs))), "exact", same[0]["pdf_page"]
        if len(rs) > 1:
            return [], "none", None
    arr = seq.get((s, cc), [])
    i = bisect.bisect_left(arr, (b,))
    before = arr[i - 1] if i > 0 else None
    after = arr[i] if i < len(arr) else None
    if before and after and before[1] == after[1]:
        return rolls_of(before[1]), "nearby", before[2]
    if before and after and s in ORDER and not cc:
        lo, hi = max(rolls_of(before[1])), min(rolls_of(after[1]))
        k = (ORDER[s], b)
        rs = [r for r, a, z in summary if a <= k <= z]
        if rs and all(lo <= r <= hi for r in rs):
            return rs, "range", None
    return [], "none", None


def decide(nums, A, summary):
    res = [(k, *lookup(k, A, summary)) for k in nums]
    good = [x for x in res if x[1]]
    if not good:
        return [], "none", res
    rolls = sorted({r for _, rs, _, _ in good for r in rs})
    if rolls and rolls[-1] - rolls[0] > 3:           # 横跨 4 卷以上（如 D-8039A 各子卷推成 34–40）太宽，不给
        return [], "none", res
    if rolls:                                        # 跨卷的大卷宗（D-8149 在 41–43）：中间的卷补齐
        rolls = list(range(rolls[0], rolls[-1] + 1))
    rank = {"exact": 0, "nearby": 1, "range": 2}
    meth = max((m for _, _, m, _ in good), key=lambda m: rank[m])
    return rolls, meth, res


PDF_ONLY = {6: "RG263_M1750-6.pdf", 12: "RG263_M1750-12.pdf", 44: "SMPA Roll 44.pdf", 45: "SMPA Roll 45.pdf"}


def load_located():
    """{file_id: [(roll, start_frame, how, conf), ...]}；优先读合并后的 roll_walk/final.json。"""
    wd = os.path.join(HERE, "roll_walk")
    fin = os.path.join(wd, "final.json")
    src = {}
    if os.path.exists(fin):
        src = {r: v["placed"] for r, v in json.load(open(fin, encoding="utf-8")).items()}
    else:
        import glob
        for f in glob.glob(os.path.join(wd, "[0-9]*.json")):
            d = json.load(open(f, encoding="utf-8"))
            src[str(d["roll"])] = d["placed"]
    out = collections.defaultdict(list)
    for r, placed in src.items():
        for i, v in placed.items():
            out[i].append((int(r), v["start"], v.get("how", "vision"), v.get("conf", 1)))
    return out


def page_frames():
    """{file_id: [[roll, start, k0, k1], ...]}：单件第 k 页（k0 ≤ k ≤ k1）= 该卷第 start + k 帧。

    两边的起点都已实测确认，页与帧按「起始帧 + 页序」对应，不逐页识别。
    但只在**首尾相接**的卷宗上用：同卷下一件实测起点正好 = 本件起点 + 页数
    （卷内最后一件则是正好到卷尾）。其余一律不给，网页改用 IA 单件影像。

    依据（2026-11-07 抽 50 件、94 帧，本机 Vision 识别对照）：
    - 首尾相接的约 35 件全部对上；仅 2656 号一例「单件重复扫了一页、又缺胶卷上一页」，
      两相抵消、首尾仍相接，中间错配一页。
    - 下一件起点多出几帧的（695 件），约三成是那几帧夹在本件中间，从夹入处起整页错开一帧
      （7、312、2801 号）；从数据上分不出是哪些件，故不用。
    - 重叠的（单件页数多于胶卷帧数）、跨卷续接的同理：3278 号第 4 卷第 2 帧起就错一页
      （单件第 1、2 页重复），续到第 5 卷时第 0 帧是片头。
    IA 上只有整本 PDF 的卷没有逐页图，不给。
    同一件在两卷都首尾相接（IA 上第 51/52 卷大段重复）时取识别把握高的那卷。
    """
    fin = json.load(open(os.path.join(HERE, "roll_walk", "final.json"), encoding="utf-8"))
    cand = collections.defaultdict(list)
    for r, R in fin.items():
        if int(r) in PDF_ONLY:
            continue
        starts = sorted(v["start"] for v in R["placed"].values())
        for i, v in R["placed"].items():
            st, n = v["start"], v["pages"]
            nxt = next((s for s in starts if s > st), R["frames"])
            if st < 0 or nxt != st + n:
                continue
            prio = (-v.get("conf", 1), -v.get("score", 0), int(r))
            cand[i].append((prio, int(r), st, 0, n - 1))
    out = {}
    for i, cs in cand.items():
        page = {}                                          # 页 → (roll, start)，先到先得
        for _, r, st, k0, k1 in sorted(cs):
            for k in range(k0, k1 + 1):
                page.setdefault(k, (r, st))
        segs = []
        for k in sorted(page):
            r, st = page[k]
            if segs and segs[-1][0] == r and segs[-1][1] == st and segs[-1][3] == k - 1:
                segs[-1][3] = k
            else:
                segs.append([r, st, k, k])
        out[i] = segs
    return out


def main():
    report = sys.argv[sys.argv.index("--report") + 1] if "--report" in sys.argv else None
    A, summary = load_appx(), load_summary()
    cat = json.load(open(os.path.join(HERE, "docs", "data", "catalog.json"), encoding="utf-8"))["items"]
    files, how_c, meth_c, rep = {}, collections.Counter(), collections.Counter(), []
    for it in cat:
        d = json.load(open(os.path.join(HERE, "docs", "data", "doc", f"{it['i']}.json"), encoding="utf-8"))
        pages = [p.get("en") or "" for p in d["pages"]]
        want = SERIES_OF.get(it.get("s", ""))
        nums, how = cover_numbers(pages, want), "cover"
        if not nums:
            votes = registry_votes(pages, want)
            top = votes.most_common()
            if top:
                best = [k for k, v in top if v == top[0][1]]
                if len(best) == 1:
                    nums, how = best, "registry"
                else:                                   # 并列：查出的卷一致才收
                    outs = {tuple(decide([k], A, summary)[0]) for k in best}
                    if len(outs) == 1 and next(iter(outs)):
                        nums, how = best, "registry-tie-agree"
                    else:
                        how = "tie"
            else:
                how = "none"
        if not nums:
            m = TITLE_NO.match(it["t"])
            if m and (not want or norm_series(m.group(1)) == want):
                nums, how = [(norm_series(m.group(1)), m.group(2))], "title"
        how_c[how] += 1
        rolls, meth, res = decide(nums, A, summary)
        meth_c[meth] += 1
        rep.append({"i": it["i"], "t": it["t"], "how": how, "nums": [f"{s}-{f}" for s, f in nums],
                    "res": [(f"{k[0]}-{k[1]}", rs, m, pg) for k, rs, m, pg in res], "rolls": rolls, "meth": meth})
        if rolls:
            files[it["i"]] = {"no": [f"{'I.O.' if s == 'IO' else s}-{f}" for s, f in nums], "r": rolls, "m": meth}
        elif nums:
            files[it["i"]] = {"no": [f"{'I.O.' if s == 'IO' else s}-{f}" for s, f in nums], "r": [], "m": "none"}
    # ---- 沿卷实测定位（tools/locate_frames.py + tools/fill_gaps.py）优先于附录 A 推出的卷号 ----
    located = load_located()
    nloc = ndiff = 0
    for i, ps in located.items():
        ps.sort(key=lambda x: (x[1] < 0, x[0]))            # 起点 ≥0 的卷在前；负起点 = 从前一卷续入
        prev = files.get(i, {})
        if prev.get("r") and not set(prev["r"]) & {r for r, *_ in ps}:
            ndiff += 1
        files[i] = {"no": prev.get("no", []), "r": sorted({r for r, *_ in ps}), "m": "located",
                    "pos": [[r, max(st, 0)] for r, st, *_ in ps],
                    **({"cont": sorted(r for r, st, *_ in ps if st < 0)} if any(st < 0 for _, st, *_ in ps) else {}),
                    **({"single": 1} if all(c <= 1 for *_, c in ps) else {})}
        nloc += 1
    pf = page_frames()
    for i, segs in pf.items():
        files[i]["pf"] = segs
    npg = {it["i"]: it["p"] for it in cat}
    nroll = sum(len({k for _, _, k0, k1 in segs for k in range(k0, k1 + 1)}) for segs in pf.values())
    print(f"影像取 roll 帧的页：{nroll:,} / 全库 {sum(npg.values()):,} 页；其余页用 IA 单件影像")
    files = {i: v for i, v in files.items() if v["r"]}
    meta = {str(r): {"ia": ROLL_IA[r] if r in ROLL_ON_IA else None} for r in range(1, 68)}
    for r, name in PDF_ONLY.items():                       # IA 上只有整本 PDF 的卷：链接指向 PDF 页码
        meta[str(r)]["pdf"] = name
    print(f"沿卷实测定位：{nloc:,} 件（其中与附录 A 推出的卷号不同、以实测为准：{ndiff}）")
    json.dump({"rolls": meta, "files": files}, open(OUT, "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))
    n = len(cat)
    print("认号方式：", ", ".join(f"{k} {v:,}（{v/n:.1%}）" for k, v in how_c.most_common()))
    print("查表结果：", ", ".join(f"{k} {v:,}（{v/n:.1%}）" for k, v in meth_c.most_common()))
    noia = sum(1 for v in files.values() if not any(meta[str(r)]["ia"] for r in v["r"]))
    print(f"给出 roll 的 {len(files):,} 件中，所在卷 IA 上没有：{noia}")
    if report:
        json.dump(rep, open(report, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    print(f"→ {os.path.relpath(OUT, HERE)}")


if __name__ == "__main__":
    main()

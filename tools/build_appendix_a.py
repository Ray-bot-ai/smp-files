"""从 MinerU 结果重建 NARA M1750 说明册附录 A → m1750_appendix_a.tsv

输入（ref/m1750_pamphlet/）：
  single_content_list.json  整本说明册（跨页切成 192 张单页、剥掉 IA 原文字层）的 MinerU 结果
  cols_content_list.json    第 33–38、148、152–158 单页只裁「Box/Roll/File」三列、放大一倍后的
                            MinerU 结果——只用来交叉核对，不进表（这些页的行主结果里已有）
  cols4_content_list.json   第 13、40、42、44 单页同法重识别——主结果把这 4 页的表当成了普通文字，
                            行结构丢失，用它补
  single_map.json / cols4_map.json  单页序号 ↔ 说明册 PDF 页码（左/右半）
人工录入：第 153 单页（PDF 第 77 页右半）MinerU 两次都只给图片，按原图录入 42 行；
          它与主结果里已有的同段一致，因此同样只作核对。

已知 MinerU 行为（都在这批数据里实测到）：
  - 跨页续表的行并进第一页那张表，后面几页只剩图片占位；所以行的页码按「表格首页」记。
  - 续表有时没有表头；按 Box/Roll/File 三列接收。
  - 页边扫描杂边（标尺、色卡）会被幻读成假表（「序号/名称/数量」「±0.01」），按位置剔除。
用法： python3 tools/build_appendix_a.py
"""
import csv, html, json, os, re

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REF = os.path.join(HERE, "ref", "m1750_pamphlet")
OUT = os.path.join(HERE, "m1750_appendix_a.tsv")
APPX_PAGES = range(9, 186)          # 附录 A 所在单页；186 起是附录 B 与卷次总表
SERIES_START = [(9, "D"), (149, "IO"), (166, "MIS"), (167, "N"), (179, "U"), (182, "W"), (183, "CS"), (184, "F")]
FILE_RE = re.compile(r"^\d{1,5}")


def series_of(pg):
    s = None
    for p, n in SERIES_START:
        if pg >= p:
            s = n
    return s


def tables(name, pagemap=None, margin_filter=True):
    cl = json.load(open(os.path.join(REF, name), encoding="utf-8"))
    for b in cl:
        if b.get("type") != "table" or not b.get("table_body"):
            continue
        if margin_filter and b["bbox"][0] < 60:    # 整页结果里贴着页边 = 扫描杂边上的假表（裁过的页不适用）
            continue
        pg = pagemap[b["page_idx"]] if pagemap else b["page_idx"]
        trs = [[html.unescape(re.sub(r"<.*?>", "", c)).strip()
                for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, flags=re.S)]
               for tr in re.findall(r"<tr>(.*?)</tr>", b["table_body"], flags=re.S)]
        yield pg, trs


def rows_from(name, pagemap=None, how="mineru", margin_filter=True):
    out = []
    for pg, trs in tables(name, pagemap, margin_filter):
        if pg not in APPX_PAGES:
            continue
        low = [h.lower() for h in trs[0]]
        if any(h.startswith("file") for h in low):
            ci = {k: next((i for i, h in enumerate(low) if h.startswith(k)), None) for k in ("box", "roll", "file")}
            width, body = len(trs[0]), trs[1:]
        elif len(trs[0]) == 3 and FILE_RE.match(trs[0][2] or ""):
            ci, width, body = {"box": 0, "roll": 1, "file": 2}, 3, trs   # 无表头续表
        else:
            continue
        for c in body:
            if any(x.lower().startswith("file") for x in c):
                continue
            if len(c) == width:
                g = lambda k: c[ci[k]] if ci[k] is not None else ""
                box, roll, f, src = g("box"), g("roll"), g("file"), how
            else:                                   # 缺列行：MinerU 少给了一格
                v = [x for x in c if x]
                if len(v) >= 2 and re.fullmatch(r"\d{1,2}", v[0]) and FILE_RE.match(v[1]):
                    box, roll, f, src = "", v[0], v[1], how + "(缺列行，首格按 roll 读)"
                elif v and FILE_RE.match(v[0]) and not re.fullmatch(r"\d{1,2}", v[0]):
                    box, roll, f, src = "", "", v[0], how + "(缺列行，只有卷宗号)"
                else:
                    continue
            if f and FILE_RE.match(f):
                out.append((pg, box, roll, f, src))
    return out


def main():
    smap = json.load(open(os.path.join(REF, "single_map.json")))
    rows = rows_from("single_content_list.json")
    rows += rows_from("cols4_content_list.json", json.load(open(os.path.join(REF, "cols4_map.json"))),
                      "mineru(裁三列重识别)", margin_filter=False)
    # 单页 42（PDF 第 22 页右半）：MinerU 只认出第一个 2871，其余按原图人工补
    rows = [r for r in rows if r[0] != 42]
    rows += [(42, "22", "6", f, "人工录入(照原图)") for f in ("2871", "2871", "2872", "2873", "2874", "2875", "2876")]
    rows.sort(key=lambda r: r[0])                   # 稳定排序：同页内保持 MinerU 给出的顺序
    with open(OUT, "w", encoding="utf-8", newline="") as fh:
        fh.write("# NARA M1750 说明册附录 A（Box / Roll / File），用于「卷宗号 → roll」对照。由 tools/build_appendix_a.py 生成。\n")
        fh.write("# 来源：Records of the Shanghai Municipal Police 1894-1949, Microfilm Pamphlet Describing M1750\n")
        fh.write("#   （Internet Archive: records-of-the-shanghai-municipal-police-1894-1949-microfilm-pamphlet）\n")
        fh.write("# 做法：剥掉 IA 原有文字层（它在这张表上大量漏 roll 号），跨页切成单页，用 MinerU 精准解析 API 重做 OCR（2026-11-06）。\n")
        fh.write("# 核对：单页 13、40、44、58、100、172 对照原图逐行一致（19+11+21+23+17+25 行）；单页 33–38、148、152–158\n")
        fh.write("#   另裁三列重跑一次，两次独立结果 290 行全同；单页 153 人工录入 42 行与主结果一致。\n")
        fh.write("#   单页 42 MinerU 漏了 6 行，按原图人工补（source 标「人工录入」）。\n")
        fh.write("# 注意：附录 A 本身没有列出 D-7734 至 D-8102（对应 Roll 33–40），原件第 113 页 7733 之后直接是 8103。\n")
        fh.write("# roll 为空 = 该行没有 roll 号（只有纸本）；同一号码常出现两行（一行纸本只有 box、一行胶卷只有 roll）。\n")
        fh.write("# pdf_page = 说明册 PDF 页码（1 起）；按表格首页记，跨页续表的行可能记在前一页。\n")
        w = csv.writer(fh, delimiter="\t")
        w.writerow(["series", "file", "roll", "box", "pdf_page", "source"])
        n = 0
        for pg, box, roll, f, src in rows:
            s = series_of(pg)
            if s:
                w.writerow([s, f, roll, box, smap[pg]["src_page"] + 1, src]); n += 1
    print(f"附录 A：{n} 行 → {os.path.relpath(OUT, HERE)}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""全库中文合并 PDF 导出：每 500 件一卷（8 卷）。
文本规则：zh 有值用 zh；zh 空且 zh_status='native_zh' 或原文是中文 → 用 en（原文）；
其余无译文页 → 〔本页无译文〕占位。
每页页眉：档案号｜第 N 页；每件开头带标题行。
用法：uv run --with reportlab scripts/export_zh_pdfs.py
"""
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import demo_run as D

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (BaseDocTemplate, Frame, PageTemplate,
                                Paragraph, Spacer)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "export_pdf")
os.makedirs(OUT, exist_ok=True)
VOL_SIZE = 500

# ── CJK 字体 ──────────────────────────────────────────────
FONT_CANDIDATES = [
    ("/System/Library/Fonts/PingFang.ttc", 2),
    ("/System/Library/Fonts/Hiragino Sans GB.ttc", 0),
    ("/System/Library/Fonts/STHeiti Light.ttc", 0),
]
font_name = None
for path, idx in FONT_CANDIDATES:
    if not os.path.exists(path):
        continue
    try:
        pdfmetrics.registerFont(TTFont("CJK", path, subfontIndex=idx))
        font_name = "CJK"
        print(f"字体: {path} (subfont {idx})")
        break
    except Exception as e:
        print(f"  字体 {path} 失败: {e}")
if not font_name:
    sys.exit("找不到可用 CJK 字体")

STYLE = ParagraphStyle("body", fontName=font_name, fontSize=10.5,
                       leading=17, spaceAfter=2)
TITLE_STYLE = ParagraphStyle("title", fontName=font_name, fontSize=13,
                             leading=20, spaceAfter=6, spaceBefore=10)
HEADER_STYLE = ParagraphStyle("header", fontName=font_name, fontSize=9,
                              leading=13, textColor="#555555", spaceAfter=4)
MARK_STYLE = ParagraphStyle("mark", fontName=font_name, fontSize=10.5,
                            leading=17, textColor="#888888")


def esc(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def page_text(q):
    zh = (q.get("zh") or "").strip()
    if zh:
        return zh, False
    en = (q.get("en") or "").strip()
    if not en:
        return "", True
    if q.get("zh_status") == "native_zh" or D.is_chinese(en):
        return en, False          # 原文即中文，用原文
    return "", True               # 有英文转录但无译文（放弃/终态）


def filename_of(iid):
    return f"{iid}.json"


files = []
for f in glob.glob(f"{ROOT}/data/smpa-files-*.json"):
    m = re.search(r"smpa-files-(\d+)\.json$", f)
    if m:
        files.append((int(m.group(1)), f))
files.sort()

if "--limit" in sys.argv:
    lim = int(sys.argv[sys.argv.index("--limit") + 1])
    files = files[:lim]
    print(f"[冒烟] 仅处理前 {lim} 件")

def header_footer(canvas, doc):
    canvas.saveState()
    canvas.setFont(font_name, 8)
    canvas.setFillColor("#888888")
    canvas.drawCentredString(A4[0] / 2, A4[1] - 12 * mm, doc.page_title or "")
    canvas.drawRightString(A4[0] - 14 * mm, A4[1] - 12 * mm,
                           f"第 {doc.page} 页")
    canvas.drawCentredString(A4[0] / 2, 10 * mm, "上海公共租界工部局警务处档案（NARA M1750）中文译本合卷")
    canvas.restoreState()

total_pages_written = 0
part_no = 0

# 按页数切分：每份 ≤ 4500 页（≈7.5MB，打开不卡）
def flush_part(cur_files, lo, hi):
    global part_no, total_pages_written
    part_no += 1
    out_pdf = os.path.join(OUT, f"smpa-files-{lo}-{hi}.pdf")
    if os.path.exists(out_pdf) and "--limit" not in sys.argv:
        print(f"[{part_no}] {os.path.basename(out_pdf)} 已存在，跳过")
        return
    doc = BaseDocTemplate(out_pdf, pagesize=A4,
                          leftMargin=18 * mm, rightMargin=18 * mm,
                          topMargin=20 * mm, bottomMargin=16 * mm,
                          title=f"SMP 中文合册 {lo}-{hi}")
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="f")
    doc.addPageTemplates([PageTemplate(id="p", frames=[frame], onPage=header_footer)])
    doc.page_title = f"档案 smpa-files-{lo} 至 {hi}"
    story = [Paragraph(f"档案 smpa-files-{lo} 至 {hi}", TITLE_STYLE),
             Paragraph("说明：正文为中文译本；原文即中文的页直接录原文；"
                       "无译文的页以〔本页无译文〕标出。", MARK_STYLE),
             Spacer(1, 8)]
    for idx, path in cur_files:
        iid = os.path.basename(path)[:-5]
        try:
            d = json.load(open(path, encoding="utf-8"))
        except Exception:
            continue
        title = d.get("title") or ""
        n_pages = len(d.get("page_data", []))
        story.append(Paragraph(f"—— {esc(iid)}｜{esc(title)}｜共 {n_pages} 页 ——",
                               TITLE_STYLE))
        for q in sorted(d.get("page_data", []), key=lambda x: x.get("n", 0)):
            text, missing = page_text(q)
            story.append(Paragraph(f"{esc(iid)}｜第 {q.get('n', 0) + 1} 页",
                                   HEADER_STYLE))
            if missing:
                story.append(Paragraph("〔本页无译文〕", MARK_STYLE))
            else:
                for para in text.split("\n"):
                    para = para.strip()
                    if para:
                        story.append(Paragraph(esc(para), STYLE))
            story.append(Spacer(1, 4))
        total_pages_written += n_pages
    doc.build(story)
    print(f"[{part_no}] {os.path.basename(out_pdf)}：{len(cur_files)} 件 / "
          f"{os.path.getsize(out_pdf) / 1e6:.1f} MB")

cur, cur_pages = [], 0
for iid_int, path in files:
    try:
        d = json.load(open(path, encoding="utf-8"))
    except Exception:
        continue
    np_ = len(d.get("page_data", []))
    if cur and cur_pages + np_ > 4500:
        lo = re.search(r"smpa-files-(\d+)", os.path.basename(cur[0][1])).group(1)
        hi = re.search(r"smpa-files-(\d+)", os.path.basename(cur[-1][1])).group(1)
        flush_part(cur, lo, hi)
        cur, cur_pages = [], 0
    cur.append((iid_int, path))
    cur_pages += np_
if cur:
    lo = re.search(r"smpa-files-(\d+)", os.path.basename(cur[0][1])).group(1)
    hi = re.search(r"smpa-files-(\d+)", os.path.basename(cur[-1][1])).group(1)
    flush_part(cur, lo, hi)

print(f"完成：{part_no} 份，{len(files)} 件 / {total_pages_written:,} 页")

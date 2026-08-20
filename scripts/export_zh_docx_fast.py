#!/usr/bin/env python3
"""全库中文合并 Word 导出 v3：直写 docx XML（无 python-docx 依赖）。
命名：纯 ID 范围（smpa-files-{lo}-{hi}.docx），不加「卷」等自造分类标签。
拆分：每份 ≤ ~15,000 页（≈8MB），保证打开不卡。
用法：python3 scripts/export_zh_docx_fast.py [--limit N]
"""
import glob
import json
import os
import re
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import demo_run as D

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "export_docx")
os.makedirs(OUT, exist_ok=True)
PAGES_PER_PART = 15000          # ≈ 7.8MB/份

XML_ILLEGAL = re.compile(
    "[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")


def esc(s):
    s = XML_ILLEGAL.sub("", s or "")
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;"))


def run(text, size="21", bold="0", color=""):
    col = f'<w:color w:val="{color}"/>' if color else ""
    b = "<w:b/>" if bold == "1" else ""
    return (f'<w:r><w:rPr><w:rFonts w:ascii="Times New Roman" '
            f'w:hAnsi="Times New Roman" w:eastAsia="宋体"/>'
            f'<w:sz w:val="{size}"/>{b}{col}</w:rPr>'
            f'<w:t xml:space="preserve">{esc(text)}</w:t></w:r>')


def para(runs, space_after="40"):
    return (f'<w:p><w:pPr><w:spacing w:after="{space_after}"/></w:pPr>'
            + runs + "</w:p>")


def page_text(q):
    zh = (q.get("zh") or "").strip()
    if zh:
        return zh, False
    en = (q.get("en") or "").strip()
    if not en:
        return "", True
    if q.get("zh_status") == "native_zh" or D.is_chinese(en):
        return en, False
    return "", True


def file_body(iid, title, pages):
    """一件的 XML 片段 + 页数"""
    body = [para(run(f"{iid}｜{title}｜共 {len(pages)} 页", "26", "1"),
                 space_after="80")]
    for q in sorted(pages, key=lambda x: x.get("n", 0)):
        body.append(para(run(f"{iid}｜第 {q.get('n', 0) + 1} 页", "18", "0",
                             "808080"), space_after="20"))
        text, missing = page_text(q)
        if missing:
            body.append(para(run("〔本页无译文〕", "21", "0", "999999")))
        else:
            for line in text.split("\n"):
                line = line.strip()
                if line:
                    body.append(para(run(line)))
    return body


def make_docx(body):
    xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/'
           'wordprocessingml/2006/main"><w:body>'
           + "".join(body) +
           '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
           '<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" '
           'w:left="1134" w:header="708" w:footer="708" w:gutter="0"/></w:sectPr>'
           '</w:body></w:document>')
    ct = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          '<Types xmlns="http://schemas.openxmlformats.org/package/2006/'
          'content-types"><Default Extension="rels" ContentType="application/'
          'vnd.openxmlformats-package.relationships+xml"/>'
          '<Default Extension="xml" ContentType="application/xml"/>'
          '<Override PartName="/word/document.xml" ContentType="application/'
          'vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
          '</Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/'
            '2006/relationships"><Relationship Id="rId1" Type="http://schemas.'
            'openxmlformats.org/officeDocument/2006/relationships/officeDocument"'
            ' Target="word/document.xml"/></Relationships>')
    tmp = os.path.join(OUT, "__tmp__.docx")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", xml)
    data = open(tmp, "rb").read()
    os.remove(tmp)
    return data


# 收集并排序全部件
all_files = []
for f in glob.glob(f"{ROOT}/data/smpa-files-*.json"):
    m = re.search(r"smpa-files-(\d+)\.json$", f)
    if m:
        all_files.append((int(m.group(1)), f))
all_files.sort()
if "--limit" in sys.argv:
    all_files = all_files[:int(sys.argv[sys.argv.index("--limit") + 1])]
    print(f"[冒烟] 仅处理前 {len(all_files)} 件")

# 按页数切成 ≤PAGES_PER_PART 的份
parts = []          # [(files列表, lo, hi, pages)]
cur, cur_pages = [], 0
for iid, path in all_files:
    try:
        d = json.load(open(path, encoding="utf-8"))
    except Exception:
        continue
    np_ = len(d.get("page_data", []))
    if cur and cur_pages + np_ > PAGES_PER_PART:
        lo = re.search(r"smpa-files-(\d+)", os.path.basename(cur[0][1])).group(1)
        hi = re.search(r"smpa-files-(\d+)", os.path.basename(cur[-1][1])).group(1)
        parts.append((cur, lo, hi, cur_pages))
        cur, cur_pages = [], 0
    cur.append((iid, path))
    cur_pages += np_
if cur:
    lo = re.search(r"smpa-files-(\d+)", os.path.basename(cur[0][1])).group(1)
    hi = re.search(r"smpa-files-(\d+)", os.path.basename(cur[-1][1])).group(1)
    parts.append((cur, lo, hi, cur_pages))

total_pages = 0
for idx, (files_, lo, hi, np_) in enumerate(parts, 1):
    out = os.path.join(OUT, f"smpa-files-{lo}-{hi}.docx")
    if os.path.exists(out) and "--limit" not in sys.argv:
        print(f"[{idx}/{len(parts)}] {os.path.basename(out)} 已存在，跳过",
              flush=True)
        total_pages += np_
        continue
    body = [para(run(f"档案 smpa-files-{lo} 至 {hi}", "36", "1")),
            para(run("说明：正文为中文译本；原文即中文的页直接录原文；"
                     "无译文的页以〔本页无译文〕标出。", "20", "0", "666666"),
                 space_after="120")]
    for iid, path in files_:
        d = json.load(open(path, encoding="utf-8"))
        body += file_body(iid, d.get("title") or "", d.get("page_data", []))
    data = make_docx(body)
    open(out, "wb").write(data)
    total_pages += np_
    print(f"[{idx}/{len(parts)}] {os.path.basename(out)}：{len(files_)} 件 / "
          f"{np_:,} 页 / {len(data) / 1e6:.1f} MB", flush=True)

print(f"完成：{len(parts)} 份 / {total_pages:,} 页", flush=True)

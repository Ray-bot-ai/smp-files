"""把 data/*.json 导出成 Obsidian 史料库里的伴生 md（英文转录 + 中文译文）。

替换的是库里那批**旧 OCR**（ABBYY FineReader 跑褪色缩微胶卷，基本不可读）。
新文本是 2026 年用 qwen3.7-plus 按原生分辨率逐页重做的转录，并逐页译成中文——
对用户最大的变化是**中文关键词从此能直接搜到**（旧库全英文且是乱码英文）。

三条不能破的规矩：

1. **只覆盖有新数据的件，其余一律不碰。** 库里 3,941 个文件中：
   3,866 件有新数据（替换）、12 件当初清单就失败从没做过新 OCR（保留旧文本）、
   63 件是 `smpa-N` 缩微整卷（与散件重复，保留）。删错了就是史料没了。
2. **文件名一个字都不改。** 文件名形如 `{卷宗号}_{标题}__{ia_id}.md`，
   Obsidian 里的链接、别人的引用都挂在这个名字上。改名 = 悄悄断链。
3. **逐页标注一起搬过来。** 站点上标了「不可用 / 影像差含推测 / 译文不完整 /
   原件即中文 / 译不出来」的页，库里也必须标。只搬正文不搬标注，
   等于把已知不可用的转录当好的端给读者——这个项目在「静默缺口」上栽过两次。

用法：
    .venv/bin/python export_vault.py --limit 5     # 先试 5 件，看格式
    .venv/bin/python export_vault.py --dry-run     # 只报会动哪些文件，不写
    .venv/bin/python export_vault.py               # 全量
"""
import argparse
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_site as B          # 复用站点那套逐页标注判据，两边口径必须一致

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
VAULT = ("/Users/yangrui/Library/Mobile Documents/iCloud~md~obsidian/Documents/"
         "史料及已有研究/原始文档.nosync/上海工部局警务处档案SMP/OCR全文")
SITE = "https://ray-bot-ai.github.io/smp-files/file.html?id="

HEAD_NOTE = """> [!info] 2026 年重做的转录 + 中文译文
> 本文不再是随影像附带的那份旧 OCR。旧 OCR（Virtual Shanghai / bnAsie 用 ABBYY
> FineReader 制作）在褪色缩微胶卷上基本失效，只能勉强撞词；下面是 2026 年用视觉
> 大模型 **qwen3.7-plus** 按**原生分辨率**逐页重新转录的英文全文，并逐页译成中文。
> **中文关键词现在可以直接检索。**
>
> ⚠️ **数字、日期、人名、地址仍不可直接引用**——模型在这几类上仍会出错，
> 引用前必须打开原件 PDF 核对。逐页标注了「不可用 / 含推测」的页尤其要看原件。"""


def vault_index():
    """库里 ia_id → 文件路径。文件名形如 …__smpa-files-123.md。"""
    out = {}
    for root, _, fs in os.walk(VAULT):
        for f in fs:
            if not f.endswith(".md"):
                continue
            m = re.search(r"__(smpa-files-\d+)\.md$", f)
            if m:
                out[m.group(1)] = os.path.join(root, f)
    return out


def old_frontmatter(path):
    """读旧文件的 frontmatter，逐行保留。

    这些字段（original_reg_no / nara_file_no / series_years / pdf_url…）是当初
    从 NARA 目录和 IA 元数据里整理出来的，**新数据里没有**，丢了要重做。
    所以这里按行保留，只替换 ocr_reliability 一项、再追加几项。
    """
    txt = open(path, encoding="utf-8", errors="replace").read(6000)
    m = re.match(r"---\n(.*?)\n---", txt, re.S)
    if not m:
        return []
    keep = []
    for line in m.group(1).split("\n"):
        k = line.split(":")[0].strip()
        if k in ("ocr_reliability", "ocr_available"):
            continue            # 由新字段取代
        keep.append(line)
    return keep


def page_block(q, iid, manual):
    """一页的 markdown。正文之外，把站点上的逐页标注一并带过来。"""
    en = (q.get("en") or "").strip()
    zh = (q.get("zh") or "").strip()
    n = q["n"]
    out = [f"## p{n + 1}"]

    bad = B.damage_flag(iid, n, en, manual)
    if bad:
        # 不要再自己加一句「请直接看原件影像」——人工写的 reason 里通常已经有了，
        # 加了就是重复两遍（实见）。
        out.append(f"> [!warning] 这一页的转录不可用\n> {bad}")
    guess = B.inference_note(en)
    if guess and not bad:
        out.append(f"> [!caution] 本页影像较差，转录含较多推测\n> "
                   f"{re.sub(r'[*]{2}(.+?)[*]{2}', r'\1', guess)}")

    if en:
        out.append("**〔英文转录〕**\n\n" + en)
    else:
        out.append("*（本页无文字或未转录）*")

    if zh:
        head = "**〔中文译文〕**"
        if q.get("zh_partial"):
            out.append("> [!warning] 这一页的译文不完整\n> "
                       "模型译到中途开始无意义地重复，下面只保留重复开始之前可读的部分。")
        out.append(head + "\n\n" + zh)
    elif q.get("zh_note"):
        out.append(f"*（{q['zh_note']}——原件本身就是中文，上面的转录即原文，无需翻译）*")
    elif q.get("zh_status") == "native_zh":
        # native_zh 是「原件本身就是中文」，不是失败。全库 758 页，其中 10 页没有
        # zh_note，若按下面的失败分支渲染，会写成「本页译不出来…模型退化」——
        # 纯属冤枉，而且是往史料里写假信息。站点 file.html 同样踩了这条，已一并修。
        out.append("*（原件本身就是中文，上面的转录即原文，无需翻译）*")
    elif q.get("zh_status"):
        out.append(f"*（本页译不出来：{q['zh_status']}。模型输出反复退化成无意义的重复，"
                   f"重试后确认，不是漏译。）*")
    elif en:
        out.append("*（本页无译文）*")
    return "\n\n".join(out)


def render(d, old_fm, manual):
    iid = d["ia_id"]
    short = iid.replace("smpa-files-", "")
    npages = d.get("pages", 0)
    nzh = sum(1 for q in d.get("page_data", []) if (q.get("zh") or "").strip())
    fm = list(old_fm) + [
        "ocr_source: qwen3.7-plus 视觉大模型逐页重做（2026-08）",
        "ocr_reliability: 中等——可读、可检索；数字/日期/人名/地址仍须核原件",
        f"pages: {npages}",
        f"zh_pages: {nzh}",
        "has_chinese_translation: true",
    ]
    parts = ["---\n" + "\n".join(fm) + "\n---", HEAD_NOTE]
    pdf = next((l.split(":", 1)[1].strip() for l in old_fm
                if l.startswith("pdf_url:")), "")
    links = []
    if pdf:
        links.append(f"- 原件 PDF：<{pdf}>")
    links.append(f"- 逐页三栏对照（原件影像｜英文｜中文）：<{SITE}{short}>")
    parts.append("\n".join(links))
    parts.append(f"共 {npages} 页，其中 {nzh} 页有中文译文。")
    parts.append("---")
    for q in sorted(d.get("page_data", []), key=lambda x: x["n"]):
        parts.append(page_block(q, iid, manual))
    return "\n\n".join(parts) + "\n"


def main(limit=0, dry=False):
    idx = vault_index()
    manual = B.load_manual_flags()
    files = sorted(glob.glob(os.path.join(DATA, "*.json")))
    if limit:
        files = files[:limit]
    wrote = skipped = missing = 0
    for f in files:
        d = json.load(open(f, encoding="utf-8"))
        iid = d["ia_id"]
        p = idx.get(iid)
        if not p:
            missing += 1
            continue
        if not d.get("page_data"):
            skipped += 1
            continue
        body = render(d, old_frontmatter(p), manual)
        if dry:
            print(f"  会写 {os.path.basename(p)}  ({len(body)/1024:.0f} KB)")
        else:
            # 先写临时文件再原子替换：中途断电也不会留下半截的史料文件
            tmp = p + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(body)
            os.replace(tmp, p)
        wrote += 1
    print(f"{'（试运行）' if dry else ''}写入 {wrote} 件；"
          f"库里找不到对应文件 {missing} 件；无页面数据跳过 {skipped} 件")
    print(f"库里未被触碰的文件：{len(idx) - wrote} 个（含 12 件从未做过新 OCR 的，"
          f"以及不在 data/ 里的缩微整卷）")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    main(a.limit, a.dry_run)

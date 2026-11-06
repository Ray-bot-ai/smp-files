"""第二轮：把第一轮（Vision 沿卷走）没覆盖到的帧交给 MinerU，补定位。

  python3 tools/fill_gaps.py prepare     # 算空当、下载空当帧、每卷打成 PDF（≤200 页一份）
  python3 tools/fill_gaps.py submit      # 交 MinerU（调 mineru1012 技能的 mineru_api.py），可重复跑
  python3 tools/fill_gaps.py merge       # 匹配空当帧、补定位，写 roll_walk/final.json

补定位的门槛比第一轮严：同一件要有两帧给出一致的起点；只有一页的件要高分（≥0.85）且放得进空当、
不与已定位的件重叠。仍然只信本站转录，不用 IA 自带 OCR。
"""
import collections, glob, json, os, re, subprocess, sys, time, urllib.request, concurrent.futures as cf

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "tools"))
OUTD = os.path.join(HERE, "roll_walk")
GAPD = os.path.join(HERE, ".roll_frames", "_gaps")
MINERU = os.path.expanduser("~/.claude/skills/mineru1012/scripts/mineru_api.py")


def rolls():
    for f in sorted(glob.glob(os.path.join(OUTD, "[0-9]*.json")), key=lambda x: int(os.path.basename(x)[:-5])):
        yield json.load(open(f))


def gaps_of(r):
    cov = set()
    for f, v in r["placed"].items():
        cov.update(range(v["start"], v["start"] + (v["pages"] or 1)))
    return [n for n in range(r["frames"]) if n not in cov]


def fetch(ia, n, fp):
    if os.path.exists(fp) and os.path.getsize(fp) > 1500:
        return True
    pdf = os.path.join(HERE, ".roll_frames", "_pdf", f"{ia}.pdf")
    if os.path.exists(pdf):                  # IA 上只有整本 PDF 的卷：本地渲染第 n 页
        import fitz
        doc = fitz.open(pdf)
        if n >= doc.page_count:
            return False
        pg = doc[n]
        pg.get_pixmap(matrix=fitz.Matrix(1200 / pg.rect.width, 1200 / pg.rect.width)).save(fp)
        return True
    for t in range(5):
        try:
            with urllib.request.urlopen(f"https://archive.org/download/{ia}/page/n{n}_w1200.jpg", timeout=90) as r:
                data = r.read()
            if len(data) > 1500:
                open(fp, "wb").write(data)
                return True
        except Exception:
            pass
        time.sleep(3 * (t + 1))
    return False


def prepare():
    import fitz
    os.makedirs(GAPD, exist_ok=True)
    plan = {}
    for r in rolls():
        g = gaps_of(r)
        d = os.path.join(GAPD, str(r["roll"]))
        os.makedirs(d, exist_ok=True)
        with cf.ThreadPoolExecutor(8) as ex:
            ok = list(ex.map(lambda n: (n, fetch(r["ia"], n, os.path.join(d, f"n{n:04d}.jpg"))), g))
        good = [n for n, o in ok if o]
        for i in range(0, len(good), 200):
            chunk = good[i:i + 200]
            name = f"roll{int(r['roll']):02d}_gap{i // 200:02d}"
            pdf = os.path.join(GAPD, name + ".pdf")
            if not os.path.exists(pdf):
                doc = fitz.open()
                for n in chunk:
                    im = fitz.open(os.path.join(d, f"n{n:04d}.jpg"))
                    doc.insert_pdf(fitz.open("pdf", im.convert_to_pdf()))
                doc.save(pdf, deflate=True)
            plan[name] = {"roll": r["roll"], "frames": chunk}
        print(f"roll {r['roll']}: gaps {len(g)} frames, fetched {len(good)}", flush=True)
    json.dump(plan, open(os.path.join(GAPD, "plan.json"), "w"))
    print("PDFs:", len(plan), "frames:", sum(len(v["frames"]) for v in plan.values()))


def submit():
    plan = json.load(open(os.path.join(GAPD, "plan.json")))
    out = os.path.join(GAPD, "mineru")
    done = {os.path.basename(p).split(".mineru")[0] for p in glob.glob(os.path.join(out, "*.mineru"))
            if glob.glob(os.path.join(p, "*_content_list.json"))}
    todo = [os.path.join(GAPD, n + ".pdf") for n in plan if n not in done]
    print("to submit:", len(todo), flush=True)
    for i in range(0, len(todo), 40):                  # 每批不超过 50 个文件
        subprocess.run([sys.executable, MINERU, "submit", *todo[i:i + 40], "-o", out], check=False)


def merge():
    from walk_roll import PageIndex
    idx = PageIndex(HERE, cache=os.path.join(OUTD, "page_index.pkl"))
    plan = json.load(open(os.path.join(GAPD, "plan.json")))
    text = collections.defaultdict(dict)
    for name, v in plan.items():
        cls = [p for p in glob.glob(os.path.join(GAPD, "mineru", name + "*.mineru", "*_content_list.json")) if "_v2" not in p]
        if not cls:
            continue
        per = collections.defaultdict(str)
        for b in json.load(open(cls[0])):
            per[b["page_idx"]] += " " + (b.get("text") or b.get("table_body") or "")
        for k, n in enumerate(v["frames"]):
            text[str(v["roll"])][n] = per.get(k, "")
    final, stat = {}, collections.Counter()
    for r in rolls():
        roll = str(r["roll"])
        placed = {f: dict(v, how="vision") for f, v in r["placed"].items()}
        occupied = set()
        for v in placed.values():
            occupied.update(range(v["start"], v["start"] + (v["pages"] or 1)))
        votes = collections.defaultdict(list)
        for n, t in sorted(text.get(roll, {}).items()):
            m = idx.match(t)
            if m and m[0][2] >= 0.55:
                f, k, s = m[0]
                votes[f].append((n - k, n, k, s))
        for f, vs in votes.items():
            if f in placed:
                continue
            starts = collections.Counter(st for st, *_ in vs)
            st, c = starts.most_common(1)[0]
            pages = idx.npages.get(f, 1)
            span = set(range(st, st + pages))
            best = max(s for _, _, _, s in vs)
            if span & occupied and len(span & occupied) > 1:
                stat["conflict"] += 1
                continue
            if c >= 2 or (pages == 1 and best >= 0.85) or (c == 1 and best >= 0.9 and pages <= 3):
                placed[f] = {"start": st, "pages": pages, "conf": c, "score": round(best, 3), "how": "mineru-gap"}
                occupied |= span
                stat["added"] += 1
            else:
                stat["weak"] += 1
        final[roll] = {"ia": r["ia"], "frames": r["frames"], "placed": placed}
    json.dump(final, open(os.path.join(OUTD, "final.json"), "w"), ensure_ascii=False)
    n = sum(len(v["placed"]) for v in final.values())
    print("placed total:", n, dict(stat))


if __name__ == "__main__":
    {"prepare": prepare, "submit": submit, "merge": merge}[sys.argv[1]]()

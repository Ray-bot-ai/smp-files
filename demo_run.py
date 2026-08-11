"""示范集：用实时调用（非 Batch）转录 + 翻译若干完整卷宗，产出站点用的 JSON。

为什么不等 Batch：实时与 Batch 的 response.body 结构完全一致，Batch 只是外面多包一层
custom_id/status_code。所以现在用实时定下来的数据结构，将来 Batch 出的结果直接能套。
实时贵一倍但秒级返回——几百页也就 ¥2，换来的是把「建站+检索」从关键路径上摘下来。

用法：.venv/bin/python demo_run.py [ocr|translate|all|stat]
"""
import concurrent.futures as cf
import base64, glob, json, os, re, sys, time, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import KEY, BASE, MODEL, PROMPT, CTX, HERE, IMG_DIR

DATA = os.path.join(HERE, "data")
MANIFEST = os.path.join(HERE, "manifest.jsonl")
SMP_OCR = os.path.expanduser("~/projects/smp-archive/ocr")

# 挑选依据：题材覆盖政治监视/工运/学潮/汪伪/日占，中英文都有，
# 且 689/3115/3150 我们已有实时转录可做质量基准。
DEMO = ["smpa-files-895", "smpa-files-1113", "smpa-files-1774", "smpa-files-751",
        "smpa-files-477", "smpa-files-1414", "smpa-files-3115",
        "smpa-files-689", "smpa-files-3150"]

TRANS_PROMPT = """你是历史档案的专业译者。下面是 1920–1940 年代上海公共租界工部局警务处
（Shanghai Municipal Police）的英文档案转录文本，请译成简体中文。

要求：
1. 逐段对应翻译，不得概括、省略或合并段落。
2. 保留 ⟦p数字⟧ 形式的页码标记，原样输出在对应位置。
3. 人名、地名、机构名：能确定的用当时通行的中文（Chiang Kai-shek=蒋介石、Chapei=闸北、
   Nantao=南市、Kiangsu=江苏、Fuhtan=复旦），不确定的保留原文并加括号注明「(音译)」。
4. 原文中残缺、拼写错误或明显是 OCR 遗留的乱码，照实译出或标「（原文不清）」，不要脑补。
5. 只输出译文本身，不要加说明、前言或代码围栏。"""


def manifest_map():
    by = {}
    for line in open(MANIFEST, encoding="utf-8"):
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r["ia_id"] not in by or (by[r["ia_id"]].get("error") and not r.get("error")):
            by[r["ia_id"]] = r
    return by


def call(messages, max_tokens=8000):
    body = {"model": MODEL, "enable_thinking": False, "temperature": 0,
            "max_tokens": max_tokens, "messages": messages}
    req = urllib.request.Request(f"{BASE}/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {KEY}",
                                          "Content-Type": "application/json"})
    for attempt in range(3):
        try:
            d = json.loads(urllib.request.urlopen(req, timeout=600, context=CTX).read())
        except urllib.error.HTTPError as e:
            d = json.loads(e.read() or b'{"error":{"message":"empty"}}')
        except Exception as e:
            d = {"error": {"message": f"{type(e).__name__}: {e}"}}
        # 百炼内容审查返回 HTTP 200，错误藏在 body 的 error 字段里，必须按这个判
        if not d.get("error"):
            return d, None
        time.sleep(2 + attempt * 3)
    return None, json.dumps(d.get("error"), ensure_ascii=False)[:200]


def ocr_page(args):
    iid, n = args
    p = os.path.join(IMG_DIR, iid, f"n{n}.jpg")
    if not os.path.exists(p):
        return n, None, "本地无图", {}
    b64 = base64.b64encode(open(p, "rb").read()).decode()
    d, err = call([{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
        {"type": "text", "text": PROMPT}]}])
    if err:
        return n, None, err, {}
    u = d["usage"]
    rt = (u.get("completion_tokens_details") or {}).get("reasoning_tokens", 0) or 0
    return n, d["choices"][0]["message"]["content"], None, {
        "in": u["prompt_tokens"], "out": u["completion_tokens"], "reasoning": rt}


def ia_ocr_text(iid):
    """本地已有的 IA 旧 OCR（件级，非逐页）。留作交叉比对——两份不一致处 = 该人工核对处。"""
    for f in glob.glob(os.path.join(SMP_OCR, "**", f"*__{iid}.md"), recursive=True):
        s = open(f, encoding="utf-8", errors="replace").read()
        return s.split("---", 2)[-1].strip()
    return ""


def run_ocr(workers=8):
    os.makedirs(DATA, exist_ok=True)
    mm = manifest_map()
    for iid in DEMO:
        out = os.path.join(DATA, f"{iid}.json")
        m = mm.get(iid)
        if not m or m.get("error"):
            print(f"{iid}: 清单里没有或有误，跳过"); continue
        doc = json.load(open(out, encoding="utf-8")) if os.path.exists(out) else {}
        if doc.get("pages_done") == m["pages"]:
            print(f"{iid}: 已完成 {m['pages']} 页，跳过"); continue
        t0 = time.time()
        got = {p["n"]: p for p in doc.get("page_data", [])}
        todo = [(iid, n) for n in range(m["pages"]) if n not in got or got[n].get("error")]
        tin = tout = trea = 0
        with cf.ThreadPoolExecutor(workers) as ex:
            for n, txt, err, u in ex.map(ocr_page, todo):
                got[n] = {"n": n, "en": txt, "error": err,
                          "image_url": f"https://archive.org/download/{iid}/page/n{n}_w2400.jpg"}
                tin += u.get("in", 0); tout += u.get("out", 0); trea += u.get("reasoning", 0)
        doc.update({
            "ia_id": iid, "title": m.get("title", ""), "series": m.get("series", ""),
            "nara_file_no": m.get("nara_file_no", ""), "pages": m["pages"],
            "ia_url": f"https://archive.org/details/{iid}",
            "page_data": [got[n] for n in sorted(got)],
            "pages_done": sum(1 for v in got.values() if v.get("en")),
            "ia_ocr": doc.get("ia_ocr") or ia_ocr_text(iid),
            "meta": {**doc.get("meta", {}), "model": MODEL, "mode": "realtime",
                     "ocr_in_tokens": doc.get("meta", {}).get("ocr_in_tokens", 0) + tin,
                     "ocr_out_tokens": doc.get("meta", {}).get("ocr_out_tokens", 0) + tout,
                     "reasoning_tokens": trea,
                     "ocr_at": time.strftime("%Y-%m-%d %H:%M:%S")}})
        json.dump(doc, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        bad = sum(1 for v in got.values() if v.get("error"))
        flag = "" if trea == 0 else f"  ⚠ reasoning={trea} 思考没关掉！"
        print(f"{iid}: {doc['pages_done']}/{m['pages']} 页，失败 {bad}，"
              f"{time.time()-t0:.0f}s，in={tin} out={tout}{flag}", flush=True)


def run_translate():
    for iid in DEMO:
        p = os.path.join(DATA, f"{iid}.json")
        if not os.path.exists(p):
            continue
        doc = json.load(open(p, encoding="utf-8"))
        if doc.get("zh_pages"):
            print(f"{iid}: 译文已存在，跳过"); continue
        # 分块译：整件一次译会超 max_tokens 被静默截断（本项目最大一件英文就有
        # 2.2 万 output token，中文只多不少）。按累计字符切块，块内保持上下文连续。
        CHUNK_CHARS = 12000                     # 约 3000 英文 token → 中文约 4500 token
        chunks, cur, cur_len = [], [], 0
        for q in doc["page_data"]:
            if not q.get("en"):
                continue
            seg = f"⟦p{q['n']}⟧\n{q['en']}"
            if cur and cur_len + len(seg) > CHUNK_CHARS:
                chunks.append("\n\n".join(cur)); cur, cur_len = [], 0
            cur.append(seg); cur_len += len(seg)
        if cur:
            chunks.append("\n\n".join(cur))
        if not chunks:
            continue
        t0 = time.time()
        zh_pages, zin, zout, bad = {}, 0, 0, 0
        for ci, ch in enumerate(chunks):
            d, err = call([{"role": "user", "content": TRANS_PROMPT + "\n\n---\n\n" + ch}],
                          max_tokens=16000)
            if err:
                print(f"{iid}: 第 {ci+1}/{len(chunks)} 块失败 {err}"); bad += 1; continue
            zh = d["choices"][0]["message"]["content"]
            zin += d["usage"]["prompt_tokens"]; zout += d["usage"]["completion_tokens"]
            if d["choices"][0].get("finish_reason") == "length":
                print(f"{iid}: ⚠ 第 {ci+1} 块仍被 max_tokens 截断，需减小 CHUNK_CHARS")
            parts = re.split(r"⟦p(\d+)⟧", zh)
            for i in range(1, len(parts) - 1, 2):
                zh_pages[int(parts[i])] = parts[i + 1].strip()
        err = f"{bad} 块失败" if bad else None
        if bad and not zh_pages:
            print(f"{iid}: 翻译全失败"); continue
        for q in doc["page_data"]:
            q["zh"] = zh_pages.get(q["n"], "")
        doc["zh_pages"] = len(zh_pages)
        doc["meta"]["zh_in_tokens"] = zin
        doc["meta"]["zh_out_tokens"] = zout
        doc["meta"]["zh_chunks"] = len(chunks)
        json.dump(doc, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        miss = doc["pages_done"] - len(zh_pages)
        print(f"{iid}: 译文 {len(zh_pages)}/{doc['pages_done']} 页对齐"
              f"（{len(chunks)} 块）{'' if not miss else f' ⚠ 缺 {miss} 页'}"
              f"{'' if not err else ' ' + err}，{time.time()-t0:.0f}s", flush=True)


def stat():
    fs = sorted(glob.glob(os.path.join(DATA, "*.json")))
    tp = tin = tout = zin = zout = 0
    print(f"{'卷宗':<20}{'页':>5}{'转录':>6}{'译文':>6}  标题")
    for f in fs:
        d = json.load(open(f, encoding="utf-8"))
        tp += d.get("pages_done", 0)
        m = d.get("meta", {})
        tin += m.get("ocr_in_tokens", 0); tout += m.get("ocr_out_tokens", 0)
        zin += m.get("zh_in_tokens", 0); zout += m.get("zh_out_tokens", 0)
        print(f"{d['ia_id']:<20}{d['pages']:>5}{d.get('pages_done',0):>6}"
              f"{d.get('zh_pages',0):>6}  {d['title'][:44]}")
    cost = (tin * 1.6 + tout * 6.4 + zin * 1.6 + zout * 6.4) / 1e6
    print(f"\n共 {len(fs)} 件 {tp} 页；token 转录 {tin:,}/{tout:,}，翻译 {zin:,}/{zout:,}")
    print(f"实时调用花费 ¥{cost:.2f}（走 Batch 只需一半）")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    if cmd in ("ocr", "all"):
        run_ocr()
    if cmd in ("translate", "all"):
        run_translate()
    stat()

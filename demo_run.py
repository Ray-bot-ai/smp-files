"""示范集：用实时调用（非 Batch）转录 + 翻译若干完整卷宗，产出站点用的 JSON。

为什么不等 Batch：实时与 Batch 的 response.body 结构完全一致，Batch 只是外面多包一层
custom_id/status_code。所以现在用实时定下来的数据结构，将来 Batch 出的结果直接能套。
实时贵一倍但秒级返回——几百页也就 ¥2，换来的是把「建站+检索」从关键路径上摘下来。

用法：.venv/bin/python demo_run.py [ocr|translate|all|stat]
"""
import concurrent.futures as cf
import base64, glob, json, os, re, sys, time, urllib.request
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (KEY, BASE, MODEL, PROMPT, CTX, HERE, IMG_DIR,
                    FB_KEY, FB_BASE, FB_MODEL, HAS_FALLBACK)

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


def is_chinese(text):
    """这一页本身就是中文原件吗？SMP 档案里夹带大量中文公函、传单、笔录，
    把中文「翻译」成中文既无意义又要花钱，而且实测会触发退化循环
    （源文满是 OCR 阶段标的 □，模型就跟着 □ 循环，一页吐出 1,349 次）。"""
    t = re.sub(r"\s", "", text or "")
    if len(t) < 30:
        return False
    return sum(1 for c in t if "\u4e00" <= c <= "\u9fff") / len(t) > 0.5


def degenerate(text, src_len):
    """检测模型退化输出。实测踩过：翻译一页中共传单时模型吐了 1,349 次重复的「□」，
    把 2,327 字的源文变成 14,726 字，撞 max_tokens 截断，导致后一页的页码标记丢失、
    整页译文消失。**这类失败不报错**，只表现为「某页特别长、某页没有」。

    两道判据：①同一字符连续重复 ≥30 次；②产出长度超过源文 3.5 倍。
    """
    if not text:
        return None
    # 先抹掉表格填空用的点线/下划线/破折号——SMP 报告表满纸都是
    # 「File No............」「Date....................19」这类。
    # 踩过：原本 (.)\1{29,} 把它们当成退化循环，误杀了正常表格页的译文。
    probe = re.sub(r"[.\u00b7\u2026\-_—–\s]{6,}", " ", text)
    if re.search(r"([^\s])\1{25,}", probe):
        return "字符重复循环"
    # 任意 30 字窗口重复出现过多
    if len(probe) > 500:
        c = Counter(probe[i:i + 30] for i in range(0, len(probe) - 30, 10))
        if c and c.most_common(1)[0][1] > 20:
            return "片段重复循环"
    if src_len and len(probe) > src_len * 3.5:
        return f"长度异常（{len(text)} vs 源 {src_len}）"
    return None


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


def call(messages, max_tokens=8000, deadline=240, base=None, key=None, model=None):
    """deadline：整体墙钟上限（秒）。

    踩过：只设 urlopen(timeout=600) 是 **socket 超时**——只要服务端每 600 秒内
    还吐出一个字节就永不触发。实测翻译卡在一次请求上 2.5 小时，连接开着、
    CPU 只用了 3 秒，整条流水线静默停摆。必须另加整体墙钟上限。

    base/key/model：留给备用端点用（内容审查拒稿时换一家重试），不传就走百炼。
    """
    body = {"model": model or MODEL, "enable_thinking": False, "temperature": 0,
            "max_tokens": max_tokens, "messages": messages}
    req = urllib.request.Request(f"{base or BASE}/chat/completions",
                                 data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {key or KEY}",
                                          "Content-Type": "application/json"})
    def _once():
        try:
            return json.loads(urllib.request.urlopen(req, timeout=deadline, context=CTX).read())
        except urllib.error.HTTPError as e:
            return json.loads(e.read() or b'{"error":{"message":"empty"}}')
        except Exception as e:
            return {"error": {"message": f"{type(e).__name__}: {e}"}}

    for attempt in range(3):
        with cf.ThreadPoolExecutor(1) as ex:
            fut = ex.submit(_once)
            try:
                d = fut.result(timeout=deadline)
            except cf.TimeoutError:
                d = {"error": {"message": f"wall-clock timeout {deadline}s"}}
                ex.shutdown(wait=False, cancel_futures=True)
        # 百炼内容审查返回 HTTP 200，错误藏在 body 的 error 字段里，必须按这个判
        if not d.get("error"):
            return d, None
        time.sleep(2 + attempt * 3)
    return None, json.dumps(d.get("error"), ensure_ascii=False)[:200]


def _blocked(err):
    """百炼内容审查拒稿。注意它是 HTTP 200 + body 里带 error，不是 4xx。"""
    return bool(err) and ("DataInspection" in err or "data_inspection" in err.lower())


def _text_of(d):
    return ((d or {}).get("choices", [{}])[0].get("message", {}).get("content") or "").strip()


def _usage_of(d, via):
    u = (d or {}).get("usage") or {}
    return {"in": u.get("prompt_tokens", 0), "out": u.get("completion_tokens", 0),
            "reasoning": (u.get("completion_tokens_details") or {}).get("reasoning_tokens", 0) or 0,
            "via": via}


def ocr_page(args):
    """转录一页。

    两种「静默失败」必须在这里挡住，否则这一页会永久缺失且没人知道：
    ① 模型返回 200 但正文是空字符串。原先直接把 "" 当结果存下，而续传判据只看
       error 字段，于是这页再也不会被重试。实看影像确认过：这些**不是空白页**
       （674/n6 是满页中文传单，2061/n25 是清晰打字稿），是真丢内容。
    ② 内容审查拒稿。这批政治监视档案里最该保留的那些页最容易被拒。
    两种情况都先在主端点重试一次，再换备用端点；仍不行就**记成 error**，
    绝不返回空字符串冒充成功。
    """
    iid, n = args
    p = os.path.join(IMG_DIR, iid, f"n{n}.jpg")
    if not os.path.exists(p):
        return n, None, "本地无图", {}
    b64 = base64.b64encode(open(p, "rb").read()).decode()
    msgs = [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
        {"type": "text", "text": PROMPT}]}]

    d, err = call(msgs)
    txt = _text_of(d) if not err else ""
    if txt:
        return n, txt, None, _usage_of(d, "dashscope")

    # 主端点再试一次（空转录有时是偶发的）。被审查拒的就别浪费这一次了。
    if not _blocked(err):
        d, err2 = call(msgs)
        txt = _text_of(d) if not err2 else ""
        if txt:
            return n, txt, None, _usage_of(d, "dashscope")
        err = err2 or err

    # 换备用端点（llm-ocr 插件的「自定义端点2」），主要为绕开内容审查
    if HAS_FALLBACK:
        d, err3 = call(msgs, base=FB_BASE, key=FB_KEY, model=FB_MODEL)
        txt = _text_of(d) if not err3 else ""
        if txt:
            return n, txt, None, _usage_of(d, "fallback")
        err = err or err3 or "空转录"

    return n, None, (err or "空转录（主端点两次 + 备用端点均无输出）"), {}


def ia_ocr_text(iid):
    """本地已有的 IA 旧 OCR（件级，非逐页）。留作交叉比对——两份不一致处 = 该人工核对处。"""
    for f in glob.glob(os.path.join(SMP_OCR, "**", f"*__{iid}.md"), recursive=True):
        s = open(f, encoding="utf-8", errors="replace").read()
        return s.split("---", 2)[-1].strip()
    return ""


MAX_TRIES = 3


def _needs_ocr(rec):
    """这一页要不要（重）跑转录。

    原判据是「没记录 或 有 error」，漏掉了第三种：**有记录、无 error、但正文是空的**。
    实测有 10 页正好落在这个缝里，于是永远不会被重试，静默缺失。
    现在按「有没有正文」判，并用 tries 计次兜底，免得真正救不回来的页每次都重烧一遍钱。
    """
    if rec is None:
        return True
    if (rec.get("en") or "").strip():
        return False
    return rec.get("tries", 0) < MAX_TRIES


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
        todo = [(iid, n) for n in range(m["pages"]) if _needs_ocr(got.get(n))]
        if not todo:
            continue          # 没活干就别重写文件——空转一遍会把整份 JSON 无谓地改一次
        tin = tout = trea = 0
        with cf.ThreadPoolExecutor(workers) as ex:
            for n, txt, err, u in ex.map(ocr_page, todo):
                # 合并而不是整条替换：这一页可能已经有译文(zh)了，
                # 整条替换会把译文一起抹掉，而且同样不报错。
                rec = dict(got.get(n) or {})
                rec.update({"n": n, "en": txt, "error": err,
                            "tries": rec.get("tries", 0) + 1,
                            "image_url": f"https://archive.org/download/{iid}/page/n{n}_w2400.jpg"})
                if u.get("via"):
                    rec["via"] = u["via"]          # 记下这页是哪个端点转出来的
                got[n] = rec
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


def _translate_one(iid):
    p = os.path.join(DATA, f"{iid}.json")
    if not os.path.exists(p):
        return
    doc = json.load(open(p, encoding="utf-8"))
    if doc.get("zh_pages") or doc.get("native_zh_pages"):
        return
    # 分块译：整件一次译会超 max_tokens 被静默截断（本项目最大一件英文就有
    # 2.2 万 output token，中文只多不少）。按累计字符切块，块内保持上下文连续。
    CHUNK_CHARS = 12000                     # 约 3000 英文 token → 中文约 4500 token
    chunks, cur, cur_len = [], [], 0
    native_zh = set()
    for q in doc["page_data"]:
        if not q.get("en"):
            continue
        if is_chinese(q["en"]):
            native_zh.add(q["n"])           # 原文即中文，不进翻译流程
            continue
        seg = f"⟦p{q['n']}⟧\n{q['en']}"
        if cur and cur_len + len(seg) > CHUNK_CHARS:
            chunks.append("\n\n".join(cur)); cur, cur_len = [], 0
        cur.append(seg); cur_len += len(seg)
    if cur:
        chunks.append("\n\n".join(cur))
    if not chunks:
        return
    t0 = time.time()
    zh_pages, zin, zout, bad = {}, 0, 0, 0
    for ci, ch in enumerate(chunks):
        d, err = call([{"role": "user", "content": TRANS_PROMPT + "\n\n---\n\n" + ch}],
                      max_tokens=16000)
        if err:
            print(f"{iid}: 第 {ci+1}/{len(chunks)} 块失败 {err}"); bad += 1; continue
        zh = d["choices"][0]["message"]["content"]
        zin += d["usage"]["prompt_tokens"]; zout += d["usage"]["completion_tokens"]
        trunc = d["choices"][0].get("finish_reason") == "length"
        why = degenerate(zh, len(ch))
        if trunc or why:
            print(f"{iid}: ⚠ 第 {ci+1} 块{'截断' if trunc else ''}{why or ''}，拆半重试")
            half = len(ch) // 2
            cut = ch.rfind("\n\n⟦", 0, half)
            subs = [ch[:cut], ch[cut:]] if cut > 0 else [ch]
            zh = ""
            for sub in subs:
                d2, e2 = call([{"role": "user",
                                "content": TRANS_PROMPT + "\n\n---\n\n" + sub}],
                              max_tokens=16000)
                if e2:
                    bad += 1; continue
                z2 = d2["choices"][0]["message"]["content"]
                zin += d2["usage"]["prompt_tokens"]; zout += d2["usage"]["completion_tokens"]
                if degenerate(z2, len(sub)):
                    # 拆半仍退化 → 逐页单独译，避免一页的问题牵连同块的邻页。
                    # 实测踩过：一页中文传单退化，把同块的 p5、p7 两页英文译文一起葬送。
                    print(f"{iid}: ⚠ 拆半后仍退化，改为逐页单译")
                    for m in re.finditer(r"⟦p(\d+)⟧\n(.*?)(?=\n\n⟦p|\Z)", sub, re.S):
                        pn, body = int(m.group(1)), m.group(2)
                        d3, e3 = call([{"role": "user", "content":
                                        TRANS_PROMPT + "\n\n---\n\n⟦p%d⟧\n%s" % (pn, body)}],
                                      max_tokens=16000)
                        if e3:
                            bad += 1; continue
                        z3 = d3["choices"][0]["message"]["content"]
                        zin += d3["usage"]["prompt_tokens"]
                        zout += d3["usage"]["completion_tokens"]
                        if degenerate(z3, len(body)):
                            print(f"{iid}:   p{pn} 单页仍退化，留空"); bad += 1; continue
                        zh += ("\n\n" if zh else "") + z3
                    continue
                zh += ("\n\n" if zh else "") + z2
        parts = re.split(r"⟦p(\d+)⟧", zh)
        for i in range(1, len(parts) - 1, 2):
            seg = parts[i + 1].strip()
            if degenerate(seg, None):     # 单页级再兜一道
                seg = re.sub(r"(.)\1{29,}", r"\1\1\1…", seg)
            zh_pages[int(parts[i])] = seg
    err = f"{bad} 块失败" if bad else None
    if bad and not zh_pages:
        print(f"{iid}: 翻译全失败"); return
    for q in doc["page_data"]:
        q["zh"] = zh_pages.get(q["n"], "")
    for q in doc["page_data"]:
        if q["n"] in native_zh:
            q["zh"] = ""
            q["zh_note"] = "原文即中文，无需翻译"
    doc["native_zh_pages"] = len(native_zh)
    doc["zh_pages"] = len(zh_pages)
    doc["meta"]["zh_in_tokens"] = zin
    doc["meta"]["zh_out_tokens"] = zout
    doc["meta"]["zh_chunks"] = len(chunks)
    json.dump(doc, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    miss = doc["pages_done"] - len(zh_pages) - len(native_zh)
    print(f"{iid}: 译文 {len(zh_pages)}/{doc['pages_done'] - len(native_zh)} 页对齐"
          f"{f'（另 {len(native_zh)} 页原文即中文）' if native_zh else ''}"
          f"（{len(chunks)} 块）{'' if not miss else f' ⚠ 缺 {miss} 页'}"
          f"{'' if not err else ' ' + err}，{time.time()-t0:.0f}s", flush=True)




def run_translate(workers=6):
    """并发翻译。单线程跑 451 件要一整天，且一次卡死就全线停摆。
    件与件之间独立，可以并发；件内分块保持顺序以维持上下文。"""
    todo = [i for i in DEMO if os.path.exists(os.path.join(DATA, f"{i}.json"))]
    print(f"待翻译 {len(todo)} 件，并发 {workers}")
    done = [0]
    def wrap(iid):
        try:
            _translate_one(iid)
        except Exception as e:
            print(f"{iid}: 异常 {type(e).__name__}: {e}"[:160], flush=True)
        done[0] += 1
        if done[0] % 25 == 0:
            print(f"  …{done[0]}/{len(todo)}", flush=True)
    with cf.ThreadPoolExecutor(workers) as ex:
        list(ex.map(wrap, todo))

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

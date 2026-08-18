"""示范集：用实时调用（非 Batch）转录 + 翻译若干完整卷宗，产出站点用的 JSON。

为什么不等 Batch：实时与 Batch 的 response.body 结构完全一致，Batch 只是外面多包一层
custom_id/status_code。所以现在用实时定下来的数据结构，将来 Batch 出的结果直接能套。
实时贵一倍但秒级返回——几百页也就 ¥2，换来的是把「建站+检索」从关键路径上摘下来。

用法：.venv/bin/python demo_run.py [ocr|translate|all|stat]
"""
import concurrent.futures as cf
import base64, glob, hashlib, json, os, re, sys, time, urllib.request
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


def _rep_score(probe):
    """30 字窗口的最高重复次数。用来比较译文与源文的重复程度。"""
    if len(probe) <= 500:
        return 0
    c = Counter(probe[i:i + 30] for i in range(0, len(probe) - 30, 10))
    return c.most_common(1)[0][1] if c else 0


def salvage(text):
    """退化输出通常不是整页坏掉：前面一段是好的，到某处才开始无限重复。
    整页丢掉等于把可读的部分一起扔了——同一页往往部分可读、部分不可读。
    所以这里把重复段折叠掉、保留可读内容，再由站点标明「已清理，仅存可读片段」。
    """
    if not text:
        return ""
    # 用 lambda 而不是替换模板：模板里写 \u2026 会被 re 当成非法转义。
    s = re.sub(r"(.)\1{9,}", lambda m: m.group(1) * 3 + "…", text)
    out, prev, run = [], None, 0
    for ln in s.split("\n"):
        k = ln.strip()
        if k and k == prev:                              # 整行连续重复，最多留两遍
            run += 1
            if run >= 2:
                continue
        else:
            prev, run = k, 0
        out.append(ln)
    return "\n".join(out).strip()


def degenerate(text, src_len, src=None):
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
    # 又踩第二次：只补了点线、没补等号线。SHANGHAI MUNICIPAL POLICE 表头带
    # 32 个连续「=」，照样被判成字符重复循环，害 14 页表格页的**正确译文**
    # 被整页丢掉（实测 1370/p34 译文 638 字完全正确）。这次把分隔线字符一次收全。
    probe = re.sub(r"[.\u00b7\u2026\-_—–=*~#+　\s]{6,}", " ", text)
    if re.search(r"([^\s])\1{25,}", probe):
        return "字符重复循环"
    # 任意 30 字窗口重复出现过多。
    # 但**表单页本身就是重复的**——CRIME DIARY 满页是「姓名：____ 住址：____」这种
    # 成排空栏，忠实译出来自然也重复，并不是模型退化。踩过：4 页表单的正确译文
    # 被这条判据丢掉，还被永久标成「译不出来」，等于往站点上写假信息。
    # 所以这里改成**比较**：只有当译文比源文明显更重复时才算退化。
    if len(probe) > 500:
        rep = _rep_score(probe)
        base = _rep_score(re.sub(r"[.\u00b7\u2026\-_—–=*~#+　\s]{6,}", " ", src)) if src else 0
        if rep > 20 and rep > base * 2:
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


def call(messages, max_tokens=8000, deadline=600, base=None, key=None, model=None):
    """deadline：整体墙钟上限（秒）。

    踩过：只设 urlopen(timeout=600) 是 **socket 超时**——只要服务端每 600 秒内
    还吐出一个字节就永不触发。实测翻译卡在一次请求上 2.5 小时，连接开着、
    CPU 只用了 3 秒，整条流水线静默停摆。必须另加整体墙钟上限。

    又踩（2026-08-16）：墙钟守卫超时后，`with ThreadPoolExecutor` 退出时
    shutdown(wait=True) **死等卡死线程**——慢速流式响应下 socket 永不超时，
    重试循环永远走不到下一步，翻译阶段整体卡死 75 分钟。现在两层超时叠加：
    ① socket 超时 min(deadline,300) 秒；② 墙钟 deadline 秒（默认 600）。
    2026-08-18 再修：socket 90 秒太短，把正常的大块翻译全掐断了。
    线程池退出改为 shutdown(wait=False)——不等待卡死线程，最多泄漏 90 秒。

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
            # socket 读超时：非流式请求要等服务端把整段算完才发第一个字节，
            # 大块翻译常要 100–300 秒。设 90 秒必然把正常请求掐断——
            # 实见报错全是 "The read operation timed out"，而短请求 3 秒就回。
            # 卡死线程由外层 shutdown(wait=False) 兜底，这里不必靠短超时保护。
            return json.loads(urllib.request.urlopen(
                req, timeout=min(deadline, 300), context=CTX).read())
        except urllib.error.HTTPError as e:
            return json.loads(e.read() or b'{"error":{"message":"empty"}}')
        except Exception as e:
            return {"error": {"message": f"{type(e).__name__}: {e}"}}

    for attempt in range(3):
        ex = cf.ThreadPoolExecutor(1)
        fut = ex.submit(_once)
        try:
            d = fut.result(timeout=deadline)
        except cf.TimeoutError:
            d = {"error": {"message": f"wall-clock timeout {deadline}s"}}
        ex.shutdown(wait=False, cancel_futures=True)   # 见 docstring：绝不等卡死线程
        # 百炼内容审查返回 HTTP 200，错误藏在 body 的 error 字段里，必须按这个判
        if not d.get("error"):
            return d, None
        time.sleep(2 + attempt * 3)
    return None, json.dumps(d.get("error"), ensure_ascii=False)[:200]


def _blocked(err):
    """百炼内容审查拒稿。注意它是 HTTP 200 + body 里带 error，不是 4xx。

    两侧都会拦：Input（送进去的图）和 Output（吐出来的译文）。
    实测被拦的既有风化案卷（色情），也有中共活动卷宗（政治），别只防一头。
    """
    return bool(err) and ("DataInspection" in err or "data_inspection" in err.lower())


def call_fb(messages, max_tokens=8000, deadline=600):
    """主端点调用；被内容审查拦下就换备用端点重来。

    翻译一样需要这条退路：百炼的 **Output** 审查会拦掉整块译文
    （实测 smpa-files-1762「Communist Activities」整件被拦，7 页全无译文）。
    返回 (d, err, via)。
    """
    d, err = call(messages, max_tokens=max_tokens, deadline=deadline)
    if not err:
        return d, None, "dashscope"
    if _blocked(err) and HAS_FALLBACK:
        d2, err2 = call(messages, max_tokens=max_tokens, deadline=deadline,
                        base=FB_BASE, key=FB_KEY, model=FB_MODEL)
        if not err2:
            return d2, None, "fallback"
        return None, err2, None
    return None, err, None


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
    """本地已有的原有 OCR（Virtual Shanghai / bnAsie 项目用 ABBYY FineReader 制作，
    件级非逐页）。留作交叉比对——两份不一致处 = 该人工核对处。
    注意：这份 OCR 不是 Internet Archive 做的，IA 只是托管方，别再写错。"""
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
        tin = tout = trea = stale = 0
        with cf.ThreadPoolExecutor(workers) as ex:
            for n, txt, err, u in ex.map(ocr_page, todo):
                # 合并而不是整条替换：这一页可能已经有译文(zh)了，
                # 整条替换会把译文一起抹掉，而且同样不报错。
                rec = dict(got.get(n) or {})
                old_en = (rec.get("en") or "").strip()
                rec.update({"n": n, "en": txt, "error": err,
                            "tries": rec.get("tries", 0) + 1,
                            "image_url": f"https://archive.org/download/{iid}/page/n{n}_w2400.jpg"})
                if u.get("via"):
                    rec["via"] = u["via"]          # 记下这页是哪个端点转出来的
                # 这一页的英文变了 → 旧译文是照旧英文译的，必然也不对，作废重译。
                # 译文是转录的下游，转录错了译文不可能对。
                if (txt or "").strip() != old_en and (rec.get("zh") or "").strip():
                    rec["zh"] = ""
                    rec.pop("zh_src", None)
                    rec.pop("zh_tries", None)      # 允许重新翻译
                    stale += 1
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
              f"{time.time()-t0:.0f}s，in={tin} out={tout}"
              f"{f'，作废旧译文 {stale} 页（英文已变）' if stale else ''}{flag}", flush=True)


MAX_ZH_TRIES = 2
BOX_RATIO = 0.30           # 与 build_site.py 的判据一致，改一处要改两处


def unusable(en):
    """严重残破页：模型自己把大部分字标成了 □，说明这页它读不出来。
    这类页不翻译（只会触发退化循环），站点上单独标注、内容仍保留。"""
    en = (en or "").strip()
    return bool(en) and en.count("□") / len(en) >= BOX_RATIO


def _fp(text):
    """英文转录的指纹。译文是照着某一版英文译出来的，
    存下当时那一版的指纹，日后就能判断译文有没有过期。"""
    return hashlib.sha256((text or "").strip().encode()).hexdigest()[:16]


_ABANDONED = None


def abandoned():
    """人工在工作台上判「放弃处理」的页（unusable_pages.json）。

    为什么流水线也要认这份名单：那些注定失败的页每轮都要重走一遍完整的退化链
    ——拆半重试、逐页单译、最后仍留空——实见单件为此空烧 900 秒。
    人已经看过原图判定不可用了，就不该再让机器反复试。
    站点那边同样读这份名单，标注「不可用」但内容照旧保留。
    """
    global _ABANDONED
    if _ABANDONED is None:
        p = os.path.join(HERE, "unusable_pages.json")
        try:
            _ABANDONED = {(r["ia_id"], r["n"]) for r in json.load(open(p, encoding="utf-8"))}
        except Exception:
            _ABANDONED = set()
    return _ABANDONED


def _missing_zh(doc):
    """需要（重新）翻译的页。两种：

    ① **缺口**：已转录、非中文原件、却没有译文。
       成因：整块译时模型偶尔吞掉 ⟦p数字⟧ 页码标记，拆块回填时那一页就对不上，
       于是 zh 被写成空字符串。原代码**发现了**（会打印「⚠ 缺 N 页」）却不补，
       而续传判据看的是 `zh_pages` 有没有值——有值就整件跳过，缺口于是永久留下。
       这是「按有记录跳过、而非按成功跳过」这个坑在本项目第三次复发。

    ② **过期**：这一页的英文转录后来变了（补跑 OCR 救回来的页就是这种），
       旧译文是照**错的/空的**英文译的，必然也是错的，必须作废重译。
       用 zh_src 指纹判断。没有 zh_src 的是改造之前留下的老数据，
       视为「与当前英文一致」，不重译——否则会把全库白译一遍。
    """
    out = []
    ab = abandoned()
    for q in doc.get("page_data", []):
        en = (q.get("en") or "").strip()
        if not en or is_chinese(en):
            continue
        if (doc.get("ia_id"), q["n"]) in ab:
            continue        # 人工已判放弃，不再当缺口反复重试
        if unusable(en):
            continue        # 满页 □ 的残破页，没有值得翻译的内容，翻了也只会退化
        if q.get("zh_status") and not (q.get("zh") or "").strip():
            # 已经判定为「译不出来」的终态（退化/模型无输出）。同一页再译一遍
            # 还是同样的结果，把它当缺口反复重试纯属白烧钱。站点上照实说明即可。
            continue
        if not (q.get("zh") or "").strip():
            out.append(q["n"])
        elif q.get("zh_src") and q["zh_src"] != _fp(en):
            out.append(q["n"])
    return out


def _fill_missing_zh(path, doc, missing):
    """只补缺口，逐页单译，不整件重译（重译要花同样的钱换同一批译文）。"""
    by = {q["n"]: q for q in doc["page_data"]}
    filled = zin = zout = 0
    for n in missing:
        q = by.get(n)
        if q is None or q.get("zh_tries", 0) >= MAX_ZH_TRIES:
            continue
        d, err, via = call_fb([{"role": "user", "content":
                                TRANS_PROMPT + "\n\n---\n\n⟦p%d⟧\n%s" % (n, q["en"])}],
                              max_tokens=16000)
        if err:
            # 接口/网络错误**不计入重试次数**：tries 是用来限制「模型给了输出但没法用」
            # 的页，不该被超时消耗掉。踩过：22 页因超时把 tries 烧到上限，
            # 之后既算作缺口（没有终态标记）、又被跳过（次数已满），永远补不上。
            continue
        q["zh_tries"] = q.get("zh_tries", 0) + 1
        if via == "fallback":
            q["zh_via"] = via
        z = d["choices"][0]["message"]["content"]
        zin += d["usage"]["prompt_tokens"]; zout += d["usage"]["completion_tokens"]
        z = re.sub(r"⟦p\d+⟧", "", z).strip()
        why = degenerate(z, len(q["en"]), q["en"]) if z else "模型无输出"
        if why:
            # 退化是**终态**，不是缺口：同一页再译一遍还是同样的结果，
            # 丢回缺口池反复重试纯属白烧钱，而且掩盖了真实情况。
            # 但**不要整页丢掉**——同一页往往部分可读、部分不可读，
            # 退化多半是译到一半才开始重复。折叠掉重复段、保留可读部分，
            # 再由站点标明这页出过什么问题，读者自己对着影像判断。
            keep = salvage(z)
            q["zh_status"] = why
            if len(keep) >= 40:
                q["zh"] = keep
                q["zh_src"] = _fp(q["en"])
                q["zh_partial"] = True      # 站点据此显示「已清理，仅存可读片段」
                filled += 1
            continue
        q["zh"] = z
        q["zh_src"] = _fp(q["en"])      # 记下这份译文是照哪一版英文译的
        q.pop("zh_status", None)        # 这次成功了，清掉旧的终态标记
        filled += 1
    # 即使一页都没补成也要落盘：zh_tries 是记在页上的，不写回去就永远是 0，
    # 那 MAX_ZH_TRIES 形同虚设，每次运行都会把这些补不成的页重烧一遍。
    doc["zh_pages"] = sum(1 for q in doc["page_data"] if (q.get("zh") or "").strip())
    doc.setdefault("meta", {})
    doc["meta"]["zh_in_tokens"] = doc["meta"].get("zh_in_tokens", 0) + zin
    doc["meta"]["zh_out_tokens"] = doc["meta"].get("zh_out_tokens", 0) + zout
    json.dump(doc, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"{doc['ia_id']}: 补译缺口 {filled}/{len(missing)} 页", flush=True)
    return filled


def _translate_one(iid):
    p = os.path.join(DATA, f"{iid}.json")
    if not os.path.exists(p):
        return
    doc = json.load(open(p, encoding="utf-8"))
    if doc.get("zh_pages") or doc.get("native_zh_pages"):
        # 译过了：只看还有没有缺口，有就补，没有才真跳过。
        miss = _missing_zh(doc)
        if miss:
            _fill_missing_zh(p, doc, miss)
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
        # 单页本身就超长的，切成多段，每段都带同一个 ⟦p⟧ 标记，回填时按序拼接。
        # 实测全库 13 页超过 12,000 字（最长一页 42,090 字），整页塞进一个请求
        # 必然超时——而超时是「一个字都拿不到」，不是「拿到一半」。
        body = q["en"]
        parts_txt = ([body] if len(body) <= CHUNK_CHARS
                     else [body[i:i + CHUNK_CHARS] for i in range(0, len(body), CHUNK_CHARS)])
        for part in parts_txt:
            seg = f"⟦p{q['n']}⟧\n{part}"
            if cur and cur_len + len(seg) > CHUNK_CHARS:
                chunks.append("\n\n".join(cur)); cur, cur_len = [], 0
            cur.append(seg); cur_len += len(seg)
        continue
        # （下面两行对普通页不再执行，保留结构以便对照）
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
        d, err, _via = call_fb([{"role": "user", "content": TRANS_PROMPT + "\n\n---\n\n" + ch}],
                               max_tokens=16000)
        if err:
            print(f"{iid}: 第 {ci+1}/{len(chunks)} 块失败 {err}"); bad += 1; continue
        zh = d["choices"][0]["message"]["content"]
        zin += d["usage"]["prompt_tokens"]; zout += d["usage"]["completion_tokens"]
        trunc = d["choices"][0].get("finish_reason") == "length"
        why = degenerate(zh, len(ch), ch)
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
                if degenerate(z2, len(sub), sub):
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
                        if degenerate(z3, len(body), body):
                            print(f"{iid}:   p{pn} 单页仍退化，留空"); bad += 1; continue
                        zh += ("\n\n" if zh else "") + z3
                    continue
                zh += ("\n\n" if zh else "") + z2
        parts = re.split(r"⟦p(\d+)⟧", zh)
        for i in range(1, len(parts) - 1, 2):
            seg = parts[i + 1].strip()
            if degenerate(seg, None):     # 单页级再兜一道
                seg = re.sub(r"(.)\1{29,}", r"\1\1\1…", seg)
            pn = int(parts[i])
            # 超长页会被切成多段、每段都带同一个页码标记，所以这里必须**追加**。
            # 覆盖的话，一页只会留下最后一段译文，前面的全部丢掉且不报错。
            zh_pages[pn] = (zh_pages[pn] + "\n" + seg) if pn in zh_pages else seg
    err = f"{bad} 块失败" if bad else None
    if bad and not zh_pages:
        print(f"{iid}: 翻译全失败"); return
    for q in doc["page_data"]:
        q["zh"] = zh_pages.get(q["n"], "")
        if q["zh"]:
            q["zh_src"] = _fp(q.get("en"))    # 译文对应的英文版本指纹
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
    # 缺口当场补掉。原先只打印「⚠ 缺 N 页」就收工，而下次运行会因为
    # zh_pages 已有值整件跳过——那 N 页于是永远没有译文，且没有任何报错。
    gap = _missing_zh(doc)
    if gap:
        _fill_missing_zh(p, doc, gap)




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

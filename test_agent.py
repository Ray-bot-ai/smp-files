"""在本地复现 docs/assets/agent.js 的工具调用循环，检验代理是否真的能：
① 把现代中文提问翻译成档案的语言  ② 从命中结果里发现新词再搜  ③ 判断相关性并总结

**测试用例刻意避开系统提示词里写过的例子**（闸北/南市/虹口/吴淞/agitator/seditious/
Special Branch 都写过）——拿写过的去测等于测一张替换表，证明不了联想能力。
"""
import json, os, re, sys, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import KEY, BASE, MODEL, CTX
from build_site import tokens, normalize

HERE = os.path.dirname(os.path.abspath(__file__))
DOC = os.path.join(HERE, "docs", "data", "doc")
IDX = os.path.join(HERE, "docs", "data", "idx")
SYS = open(os.path.join(HERE, "docs", "assets", "agent.js"), encoding="utf-8") \
    .read().split("const SYS_PROMPT = `")[1].split("`;")[0]

import hashlib
_shard = {}
_doc = {}


def _load_shard(i):
    if i not in _shard:
        p = os.path.join(IDX, f"{i}.json")
        _shard[i] = json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}
    return _shard[i]


def local_search(q, limit=8):
    toks = sorted(tokens(q))
    if not toks:
        return {"query": q, "total_pages": 0, "total_files": 0, "samples": []}
    inter = None
    for t in toks:
        sh = _load_shard(int(hashlib.sha256(t.encode()).hexdigest()[:4], 16) % 64)
        posts = sh.get(t)
        if not posts:
            return {"query": q, "tokenised_as": toks, "total_pages": 0, "total_files": 0, "samples": []}
        s = {f"{d}#{p}" for d, p in posts}
        inter = s if inter is None else (inter & s)
        if not inter:
            break
    bydoc = {}
    for k in (inter or []):
        d, p = k.split("#")
        bydoc.setdefault(d, []).append(int(p))
    docs = sorted(bydoc.items(), key=lambda x: -len(x[1]))
    out = {"query": q, "tokenised_as": toks,
           "total_pages": sum(len(v) for v in bydoc.values()), "total_files": len(bydoc),
           "samples": []}
    for d, ps in docs[:limit]:
        if d not in _doc:
            f = os.path.join(DOC, f"{d}.json")
            if not os.path.exists(f):
                continue
            _doc[d] = json.load(open(f, encoding="utf-8"))
        dd = _doc[d]
        n = sorted(ps)[0]
        pg = next((x for x in dd["pages"] if x["n"] == n), {})
        txt = pg.get("en") or pg.get("zh") or ""
        nq = normalize(q.lower())
        i = normalize(txt.lower()).find(nq)
        i = max(0, (i if i >= 0 else 0) - 80)
        out["samples"].append({"file": dd["t"], "doc_id": d, "pages_matched": len(ps),
                               "first_page": n + 1, "excerpt": txt[i:i + 260]})
    return out


TOOLS = [{
    "type": "function",
    "function": {
        "name": "search_archive",
        "description": "在已转录的工部局警务处档案全文中检索。",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
            "required": ["query"],
        },
    },
}]


def call(messages, think, tools=True):
    body = {"model": MODEL, "messages": messages,
            **({"tools": TOOLS, "tool_choice": "auto"} if tools else {}),
            "temperature": 0.3, "max_tokens": 8000 if think != "off" else 2000,
            **({"enable_thinking": True, "thinking_budget": 4000} if think == "deep"
               else {"enable_thinking": False})}
    r = urllib.request.Request(f"{BASE}/chat/completions", data=json.dumps(body).encode(),
                               headers={"Authorization": f"Bearer {KEY}",
                                        "Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(r, timeout=300, context=CTX).read())


def run(question, think="deep", max_rounds=10):
    msgs = [{"role": "system", "content": SYS}, {"role": "user", "content": question}]
    queries = []
    for rd in range(1, max_rounds + 1):
        finishing = rd > max_rounds - 2
        if rd == max_rounds - 1:
            msgs.append({"role": "user", "content":
                         "检索够了。现在停止调用工具，按系统提示要求的格式给出最终汇总。"})
        d = call(msgs, think, tools=not finishing)
        m = d["choices"][0]["message"]
        tcs = m.get("tool_calls") or []
        if not tcs:
            return queries, m.get("content") or ""
        msgs.append({"role": "assistant", "content": m.get("content"), "tool_calls": tcs})
        for tc in tcs:
            a = json.loads(tc["function"]["arguments"] or "{}")
            res = local_search(a.get("query", ""), a.get("limit", 8))
            queries.append((rd, a.get("query", ""), res["total_pages"], res["total_files"]))
            msgs.append({"role": "tool", "tool_call_id": tc["id"],
                         "content": json.dumps(res, ensure_ascii=False)[:6000]})
    return queries, "(达最大轮数)"


if __name__ == "__main__":
    # 全部避开提示词中出现过的地名/术语
    cases = sys.argv[1:] or ["外国人和中国人在电车上的冲突"]
    for q in cases:
        print(f"\n{'='*72}\n【{q}】\n{'='*72}")
        qs, ans = run(q)
        print("检索轨迹（轮次 · 检索词 · 命中页/件）：")
        for rd, t, np_, nf in qs:
            print(f"  r{rd}  {t:<38} {np_:>4}页 {nf:>3}件")
        print("\n--- 最终输出 ---")
        print(ans[:2200])

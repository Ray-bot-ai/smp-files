"""建全量页面清单：3,878 件逐件查 IA 的真实页数，产出 manifest.jsonl。
这是所有批次的前置，也是「约9万页」这个外推数字的落实。
只读、可断点续传（重跑自动跳过已完成的件）。

用法：python3 manifest.py build | stat
"""
import concurrent.futures as cf
import json, os, re, subprocess, sys, time, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
CATALOG = os.path.expanduser("~/projects/smp-archive/catalog.json")
OUT = os.path.join(HERE, "manifest.jsonl")
# archive.org/metadata 是服务端限速（实测 P=8 约 0.88s/件，加并发无效），
# 文件下载则并发有效。两者混在一起，取折中。
WORKERS = 10


def curl(url, timeout=40):
    r = subprocess.run(["curl", "-sL", "--max-time", str(timeout), url],
                       capture_output=True, text=True)
    return r.stdout


def probe(item):
    """返回一件的清单记录；失败返回带 error 的记录，不静默丢。"""
    iid = item["ia_id"]
    rec = {"ia_id": iid, "title": item.get("title", ""),
           "series": item.get("series", ""), "nara_file_no": item.get("nara_file_no", "")}
    try:
        m = json.loads(curl(f"https://archive.org/metadata/{iid}"))
    except Exception as e:
        return {**rec, "error": f"metadata: {type(e).__name__}"}
    files = {f.get("name"): f for f in m.get("files", [])}
    if not files:
        return {**rec, "error": "no files (IA 侧空条目)"}
    pn = [n for n in files if n.endswith("_page_numbers.json")]
    pdf = [n for n in files if n.endswith(".pdf") and not n.endswith("_text.pdf")]
    rec["pdf"] = pdf[0] if pdf else None
    if not pn:
        # 没有 BookReader 派生物 → 取不到单页图，要单独处理
        return {**rec, "pages": 0, "error": "no _page_numbers.json（无单页图接口）"}
    try:
        d = json.loads(curl(f"https://archive.org/download/{iid}/" + urllib.parse.quote(pn[0])))
        rec["pages"] = len(d.get("pages", []))
    except Exception as e:
        return {**rec, "error": f"page_numbers: {type(e).__name__}"}
    return rec


def build(retry_errors=False):
    """retry_errors=True 时，把上次记成 error 的件也重新查一遍。

    坑：早先版本按 ia_id 跳过已有记录，导致网络抖动造成的 JSONDecodeError
    在重跑时被当成「已完成」永久跳过——这些件会静默地从清单里消失。
    现在默认只跳过成功的记录；`python3 manifest.py retry` 专门重试失败的。
    """
    cat = [x for x in json.load(open(CATALOG))
           if re.fullmatch(r"smpa-files-\d+", x["ia_id"])]
    ok_ids, err_ids = set(), set()
    if os.path.exists(OUT):
        for line in open(OUT, encoding="utf-8"):
            try:
                r = json.loads(line)
            except Exception:
                continue
            (err_ids if r.get("error") else ok_ids).add(r["ia_id"])
    err_ids -= ok_ids                      # 已被成功记录覆盖的不算失败
    skip = ok_ids | (set() if retry_errors else err_ids)
    todo = [x for x in cat if x["ia_id"] not in skip]
    print(f"目录 {len(cat)} 件｜成功 {len(ok_ids)}｜失败 {len(err_ids)}｜本次待办 {len(todo)}"
          + ("（含重试失败件）" if retry_errors else ""))
    if not todo:
        return stat()
    t0 = time.time()
    n = 0
    with open(OUT, "a", encoding="utf-8") as fh, \
            cf.ThreadPoolExecutor(WORKERS) as ex:
        for rec in ex.map(probe, todo):
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
            n += 1
            if n % 100 == 0:
                el = time.time() - t0
                eta = el / n * (len(todo) - n)
                print(f"  {n}/{len(todo)}  {el/60:.1f}min 已用，预计还需 {eta/60:.1f}min",
                      flush=True)
    print(f"完成，用时 {(time.time()-t0)/60:.1f} 分钟")
    stat()


def stat():
    if not os.path.exists(OUT):
        return print("还没有 manifest.jsonl")
    by_id = {}
    for l in open(OUT, encoding="utf-8"):
        try:
            r = json.loads(l)
        except Exception:
            continue
        # 成功记录优先，重试成功后覆盖旧的失败记录
        if r["ia_id"] not in by_id or (by_id[r["ia_id"]].get("error") and not r.get("error")):
            by_id[r["ia_id"]] = r
    recs = list(by_id.values())
    ok = [r for r in recs if not r.get("error")]
    bad = [r for r in recs if r.get("error")]
    pages = sum(r.get("pages", 0) for r in ok)
    print(f"\n=== 清单统计 ===")
    print(f"件数 {len(recs)}  可用 {len(ok)}  有问题 {len(bad)}")
    print(f"**总页数 {pages:,}**  （均 {pages/max(len(ok),1):.1f} 页/件）")
    if ok:
        ps = sorted(r.get("pages", 0) for r in ok)
        print(f"中位 {ps[len(ps)//2]}  p90 {ps[int(len(ps)*.9)]}  最大 {ps[-1]}")
    # 成本换算：实测 2730 输入 / 470 输出 tok/页，Batch 半价 ¥0.8 / ¥3.2 每百万
    per = (2730 * 0.8 + 470 * 3.2) / 1e6
    print(f"→ OCR 成本 ¥{per*pages:,.0f}（qwen3.7-plus，Batch 半价）")
    print(f"→ 取图约 {pages*0.35/1024:.1f} GB")
    if bad:
        from collections import Counter
        print("\n有问题的件：")
        for k, v in Counter(r["error"] for r in bad).most_common():
            print(f"  {v:>4}  {k}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    if cmd == "stat":
        stat()
    elif cmd == "retry":
        build(retry_errors=True)
    else:
        build()

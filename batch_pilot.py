"""Batch 试点：10 页走完整流程，验证四件事——
1) 多模态 batch 能不能提交成功  2) enable_thinking:false 在 body 顶层是否真的关掉思考
3) 实际计费 token 数  4) 转录质量是否与实时调用一致
用法：python3 batch_pilot.py submit | status <batch_id> | fetch <batch_id>
"""
import base64, json, os, sys, time, uuid
from common import api, fetch_page, MODEL, PROMPT, BATCH_DIR, KEY, BASE, CTX
import urllib.request

PAGES = [("smpa-files-2481", 1), ("smpa-files-2481", 2), ("smpa-files-2481", 4),
         ("smpa-files-689", 1), ("smpa-files-689", 2), ("smpa-files-3493", 3),
         ("smpa-files-3115", 1), ("smpa-files-3115", 2), ("smpa-files-705", 12),
         ("smpa-files-3150", 1)]


def build_jsonl():
    os.makedirs(BATCH_DIR, exist_ok=True)
    path = os.path.join(BATCH_DIR, "pilot.jsonl")
    n_ok = 0
    with open(path, "w", encoding="utf-8") as fh:
        for iid, n in PAGES:
            f = fetch_page(iid, n)
            if not f:
                print(f"  取图失败 {iid} n{n}"); continue
            b64 = base64.b64encode(open(f, "rb").read()).decode()
            line = {"custom_id": f"{iid}__n{n}", "method": "POST",
                    "url": "/v1/chat/completions",
                    "body": {"model": MODEL,
                             # 关键：enable_thinking 必须在 body 顶层，与 model 同级
                             "enable_thinking": False,
                             "temperature": 0, "max_tokens": 8000,
                             "messages": [{"role": "user", "content": [
                                 {"type": "image_url",
                                  "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                                 {"type": "text", "text": PROMPT}]}]}}
            s = json.dumps(line, ensure_ascii=False)
            assert len(s.encode()) < 1_000_000, f"单行超 1MB：{iid} n{n}"
            fh.write(s + "\n"); n_ok += 1
    mb = os.path.getsize(path) / 1048576
    print(f"JSONL 就绪：{n_ok} 条，{mb:.1f} MB（单文件上限 500MB / 5万条）")
    print(f"  → 按此密度，500MB 只能装约 {int(500 / (mb / max(n_ok,1)))} 条 = 走 base64 的每批上限")
    return path


def upload(path):
    """/v1/files 走 multipart/form-data。"""
    b = uuid.uuid4().hex
    body = b"".join([
        f'--{b}\r\nContent-Disposition: form-data; name="purpose"\r\n\r\nbatch\r\n'.encode(),
        f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="pilot.jsonl"\r\n'
        f'Content-Type: application/json\r\n\r\n'.encode(),
        open(path, "rb").read(), f"\r\n--{b}--\r\n".encode()])
    r = api("/files", raw_body=body,
            headers={"Content-Type": f"multipart/form-data; boundary={b}"}, timeout=900)
    print("上传:", json.dumps(r, ensure_ascii=False)[:300])
    return r.get("id")


def submit():
    path = build_jsonl()
    fid = upload(path)
    if not fid:
        print("✗ 上传失败，停"); return
    # 窗口一律给足。踩过：第一次试点用 24h，排了整整 24 小时**一条都没被处理**，
    # 到点全部 CanceledBatchByExpire——不是失败，是根本没排到就被作废了。
    # 千问 Batch 排队以小时甚至天计是常态，最长可设 14 天，没有理由省这个。
    r = api("/batches", data={"input_file_id": fid, "endpoint": "/v1/chat/completions",
                              "completion_window": "14d",
                              "metadata": {"ds_name": "SMP-OCR-pilot-10p"}})
    print("建任务:", json.dumps(r, ensure_ascii=False)[:400])
    if r.get("id"):
        open(os.path.join(BATCH_DIR, "pilot_batch_id.txt"), "w").write(r["id"])
        print(f"\n✓ batch_id = {r['id']}")


def status(bid):
    r = api(f"/batches/{bid}", method="GET")
    print(json.dumps({k: r.get(k) for k in
                      ("id", "status", "request_counts", "output_file_id",
                       "error_file_id", "errors")}, ensure_ascii=False, indent=1))
    return r


def fetch(bid):
    r = status(bid)
    for kind in ("output_file_id", "error_file_id"):
        fid = r.get(kind)
        if not fid:
            continue
        req = urllib.request.Request(f"{BASE}/files/{fid}/content",
                                     headers={"Authorization": f"Bearer {KEY}"})
        txt = urllib.request.urlopen(req, timeout=600, context=CTX).read().decode()
        out = os.path.join(BATCH_DIR, f"pilot_{kind}.jsonl")
        open(out, "w", encoding="utf-8").write(txt)
        print(f"\n=== {kind} → {out} ===")
        if kind == "output_file_id":
            tin = tout = treason = 0
            for line in txt.splitlines():
                d = json.loads(line)
                u = d["response"]["body"]["usage"]
                tin += u["prompt_tokens"]; tout += u["completion_tokens"]
                treason += (u.get("completion_tokens_details") or {}).get("reasoning_tokens", 0) or 0
            n = len(txt.splitlines())
            print(f"{n} 条：输入 {tin} tok（{tin//n}/页），输出 {tout} tok（{tout//n}/页）")
            print(f"思考 token = {treason}  →  " +
                  ("✓ 思考已关闭" if treason == 0 else "✗ 思考没关掉！成本会暴涨"))
            per = (tin * 0.8 + tout * 3.2) / 1e6 / n       # Batch 半价：1.6/2, 6.4/2
            print(f"实测 Batch 单价 ¥{per:.5f}/页 → 9 万页 ¥{per*90000:,.0f}")
        else:
            print(txt[:600])


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "submit"
    if cmd == "submit":
        submit()
    else:
        bid = sys.argv[2] if len(sys.argv) > 2 else \
            open(os.path.join(BATCH_DIR, "pilot_batch_id.txt")).read().strip()
        (status if cmd == "status" else fetch)(bid)

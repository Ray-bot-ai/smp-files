"""用官方免费测试模型 batch-test-model 验证 Batch 全链路（上传→建任务→轮询→回收）。

为什么单独做这个：测试模型跳过推理、直接返回固定成功响应，**不产生任何推理费用**，
用来验证「代码写对了没」。质量和 token 用量得用真模型验，但那是第二步。
本项目踩过：一上来就提交真实批次，排队 95 分钟才知道链路对不对。

注意 endpoint 与 JSONL 里的 url 必须一致：测试模型是 /v1/chat/ds-test，
真实模型才是 /v1/chat/completions。

用法：.venv/bin/python batch_chain_test.py
"""
import json, os, sys, time, urllib.request, uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import api, KEY, BASE, CTX, BATCH_DIR

N = 20
TEST_MODEL = "batch-test-model"
TEST_URL = "/v1/chat/ds-test"


def build():
    os.makedirs(BATCH_DIR, exist_ok=True)
    p = os.path.join(BATCH_DIR, "chain_test.jsonl")
    with open(p, "w", encoding="utf-8") as fh:
        for i in range(N):
            # custom_id 用真实格式（iid__nN），验证它能原样回传
            fh.write(json.dumps({
                "custom_id": f"smpa-files-{2481+i}__n{i}",
                "method": "POST", "url": TEST_URL,
                "body": {"model": TEST_MODEL, "enable_thinking": False,
                         "messages": [{"role": "user", "content": f"第{i}条"}]}},
                ensure_ascii=False) + "\n")
    kb = os.path.getsize(p) / 1024
    print(f"JSONL {N} 行 {kb:.1f} KB（测试模型限制：≤1MB、≤100行）")
    return p


def upload(path):
    b = uuid.uuid4().hex
    body = b"".join([
        f'--{b}\r\nContent-Disposition: form-data; name="purpose"\r\n\r\nbatch\r\n'.encode(),
        f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="chain_test.jsonl"\r\n'
        f'Content-Type: application/json\r\n\r\n'.encode(),
        open(path, "rb").read(), f"\r\n--{b}--\r\n".encode()])
    r = api("/files", raw_body=body,
            headers={"Content-Type": f"multipart/form-data; boundary={b}"}, timeout=300)
    return r.get("id"), r


def main():
    print("=== 1. 造 JSONL ===")
    p = build()

    print("\n=== 2. 上传 ===")
    fid, raw = upload(p)
    if not fid:
        sys.exit(f"  ✗ {json.dumps(raw, ensure_ascii=False)[:300]}")
    print(f"  file_id = {fid}")

    print("\n=== 3. 建任务（endpoint 必须与 JSONL 的 url 一致）===")
    r = api("/batches", data={"input_file_id": fid, "endpoint": TEST_URL,
                              "completion_window": "24h",
                              "metadata": {"ds_name": "chain-test"}})
    bid = r.get("id")
    if not bid:
        sys.exit(f"  ✗ {json.dumps(r, ensure_ascii=False)[:300]}")
    print(f"  batch_id = {bid}  status={r.get('status')}")

    print("\n=== 4. 轮询 ===")
    t0 = time.time()
    while True:
        st = api(f"/batches/{bid}", method="GET")
        s = st.get("status")
        el = time.time() - t0
        print(f"  {el:5.0f}s  {s}  {st.get('request_counts')}", flush=True)
        if s in ("completed", "failed", "expired", "cancelled"):
            break
        if el > 900:
            sys.exit("  ⚠ 15 分钟未完成，测试模型本应很快，链路可能有问题")
        time.sleep(15)
    if s != "completed":
        sys.exit(f"  ✗ 任务 {s}: {json.dumps(st.get('errors'), ensure_ascii=False)[:400]}")
    print(f"  ✓ 完成，耗时 {time.time()-t0:.0f}s")

    print("\n=== 5. 回收并核对 custom_id 往返 ===")
    fid_out = st.get("output_file_id")
    req = urllib.request.Request(f"{BASE}/files/{fid_out}/content",
                                 headers={"Authorization": f"Bearer {KEY}"})
    txt = urllib.request.urlopen(req, timeout=300, context=CTX).read().decode()
    lines = [json.loads(l) for l in txt.splitlines() if l.strip()]
    got = {d["custom_id"] for d in lines}
    want = {f"smpa-files-{2481+i}__n{i}" for i in range(N)}
    print(f"  回收 {len(lines)}/{N} 条")
    print(f"  custom_id 完全一致：{'✓' if got == want else '✗ 缺 ' + str(want - got)}")
    d0 = lines[0]
    print(f"  结构：response.status_code={d0['response'].get('status_code')}  "
          f"body.usage={('有' if d0['response']['body'].get('usage') else '无')}")
    print(f"  示例内容：{json.dumps(d0['response']['body'].get('choices',[{}])[0], ensure_ascii=False)[:120]}")
    if st.get("error_file_id"):
        print(f"  ⚠ 有错误文件 {st['error_file_id']}")

    print("\n结论：Batch 全链路（上传→建任务→轮询→回收→custom_id 往返）验证通过，零费用")


if __name__ == "__main__":
    main()

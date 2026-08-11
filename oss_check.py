"""OSS 端到端验证：连通性 → 地域/权限 → 上传 → 签名URL → 百炼能否读到。
密钥从 ~/.oss_env 读，不落仓库、不打印。
用法：.venv/bin/python oss_check.py
"""
import base64, json, os, ssl, subprocess, sys, time, urllib.request

import oss2

HERE = os.path.dirname(os.path.abspath(__file__))


def load_env(path=os.path.expanduser("~/.oss_env")):
    for line in open(path):
        line = line.strip()
        if line.startswith("export "):
            k, _, v = line[7:].partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"\''))


load_env()
AK = os.environ["OSS_ACCESS_KEY_ID"]
SK = os.environ["OSS_ACCESS_KEY_SECRET"]
BUCKET = os.environ["OSS_BUCKET"]
ENDPOINT = os.environ["OSS_ENDPOINT"]

bucket = oss2.Bucket(oss2.Auth(AK, SK), f"https://{ENDPOINT}", BUCKET)


def main():
    print("=== 1. 连通性与配置 ===")
    try:
        info = bucket.get_bucket_info()
        print(f"  bucket      {info.name}")
        print(f"  地域        {info.location}   {'✓ 与百炼同地域' if info.location=='oss-cn-beijing' else '✗ 不是 cn-beijing！跨地域会又慢又贵'}")
        print(f"  存储类型    {info.storage_class}")
        acl = bucket.get_bucket_acl().acl
        print(f"  读写权限    {acl}   {'✓ 私有，走签名URL' if acl=='private' else '⚠ 非私有：整桶可被任何人读取'}")
    except Exception as e:
        sys.exit(f"  ✗ 连不上：{type(e).__name__}: {e}")

    print("\n=== 2. 上传一张真实档案页 ===")
    src = os.path.join(HERE, "images", "smpa-files-2481_n2_w2400.jpg")
    if not os.path.exists(src):
        src = os.path.join(HERE, "images", "smpa-files-689_n1_w2400.jpg")
    key = "probe/smpa-files-2481_n2.jpg"
    t0 = time.time()
    bucket.put_object_from_file(key, src)
    sz = os.path.getsize(src)
    dt = time.time() - t0
    print(f"  上传 {sz//1024} KB 用时 {dt:.1f}s  →  {sz/1024/dt:.0f} KB/s")
    print(f"  9 万张 ≈ {sz*90000/2**30:.1f} GB，单线程约 {sz*90000/1024/(sz/1024/dt)/3600:.1f} 小时（并发可大幅缩短）")

    print("\n=== 3. 生成 7 天有效的签名 URL ===")
    url = bucket.sign_url("GET", key, 7 * 24 * 3600, slash_safe=True)
    print(f"  长度 {len(url)} 字符（JSONL 每行的开销）")
    h = subprocess.run(["curl", "-sIL", "--max-time", "30", url], capture_output=True, text=True).stdout
    code = [l for l in h.splitlines() if l.startswith("HTTP")]
    cl = [l for l in h.splitlines() if l.lower().startswith("content-length")]
    ct = [l for l in h.splitlines() if l.lower().startswith("content-type")]
    print(f"  {code[-1].strip() if code else '无响应'}")
    print(f"  {cl[-1].strip() if cl else '✗ 缺 Content-Length'}   {ct[-1].strip() if ct else '✗ 缺 Content-Type'}")
    print("  ↑ 这两个头是百炼取图的硬性要求，当初 IA 直传失败就栽在这一步")

    print("\n=== 4. 百炼能不能用这个 URL 读图（决定性测试）===")
    sys.path.insert(0, HERE)
    os.environ.setdefault("LLM_OCR_VAULT",
                          "/Users/yangrui/Library/Mobile Documents/iCloud~md~obsidian/Documents/史料及已有研究")
    from common import KEY, BASE, MODEL, PROMPT, CTX

    body = {"model": MODEL, "enable_thinking": False, "temperature": 0, "max_tokens": 8000,
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": url}},
                {"type": "text", "text": PROMPT}]}]}
    req = urllib.request.Request(f"{BASE}/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {KEY}",
                                          "Content-Type": "application/json"})
    t0 = time.time()
    try:
        d = json.loads(urllib.request.urlopen(req, timeout=300, context=CTX).read())
    except urllib.error.HTTPError as e:
        d = json.loads(e.read())
    dt = time.time() - t0
    if d.get("error"):
        print(f"  ✗ {dt:.1f}s  {json.dumps(d['error'], ensure_ascii=False)[:250]}")
        sys.exit(1)
    u = d["usage"]
    txt = d["choices"][0]["message"]["content"]
    rt = (u.get("completion_tokens_details") or {}).get("reasoning_tokens", 0) or 0
    print(f"  ✓ {dt:.1f}s  in={u['prompt_tokens']} out={u['completion_tokens']} reasoning={rt}")
    print(f"  转录抽查：{'✓ 关键事实正确' if ('1143/106' in txt and 'Nickel' in txt) else '⚠ 与基准不符，需人工看'}")
    print(f"  首行：{txt.strip().splitlines()[0][:60]}")

    print("\n=== 5. 清理探针 ===")
    bucket.delete_object(key)
    print("  已删除测试对象")
    print("\n结论：OSS → 签名URL → 百炼 全链路打通，可以按 URL 方式切批（9万页仅需 2 个批次）")


if __name__ == "__main__":
    main()

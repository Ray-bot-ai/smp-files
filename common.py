"""公共配置：从 Obsidian llm-ocr 插件的 data.json 读百炼 API key，不在本仓库存任何密钥。"""
import json, os, ssl, subprocess, urllib.request

VAULT = ("/Users/yangrui/Library/Mobile Documents/iCloud~md~obsidian/"
         "Documents/史料及已有研究")
_cfg = json.load(open(f"{VAULT}/.obsidian/plugins/llm-ocr/data.json"))

KEY = _cfg["keys"]["Custom"]          # 只在内存里，不落盘
BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"
MODEL = "qwen3.7-plus"
PROMPT = next(t["body"] for t in _cfg["templates"] if "档案" in t["name"])

try:
    import certifi
    CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    CTX = ssl.create_default_context()

HERE = os.path.dirname(os.path.abspath(__file__))
IMG_DIR = os.path.join(HERE, "images")
BATCH_DIR = os.path.join(HERE, "batches")


def api(path, data=None, headers=None, method=None, raw_body=None, timeout=300):
    """打百炼 OpenAI 兼容接口。data=dict 走 JSON；raw_body=bytes 走 multipart。"""
    h = {"Authorization": f"Bearer {KEY}"}
    h.update(headers or {})
    body = raw_body
    if data is not None:
        body = json.dumps(data).encode()
        h.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(f"{BASE}{path}", data=body, headers=h, method=method)
    try:
        return json.loads(urllib.request.urlopen(req, timeout=timeout, context=CTX).read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read() or b'{"error":"empty"}')


def page_url(iid, n, w=2400):
    return f"https://archive.org/download/{iid}/page/n{n}_w{w}.jpg"


def fetch_page(iid, n, w=2400):
    """curl 下载单页图。urllib 在 IA 上会 IncompleteRead，别换。"""
    os.makedirs(IMG_DIR, exist_ok=True)
    f = os.path.join(IMG_DIR, f"{iid}_n{n}_w{w}.jpg")
    if os.path.exists(f) and os.path.getsize(f) > 5000:
        return f
    subprocess.run(["curl", "-sL", "--max-time", "120", "-o", f, page_url(iid, n, w)])
    return f if os.path.exists(f) and os.path.getsize(f) > 5000 else None

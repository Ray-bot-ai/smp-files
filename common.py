"""公共配置：从 Obsidian llm-ocr 插件的 data.json 读百炼 API key，不在本仓库存任何密钥。"""
import json, os, ssl, subprocess, urllib.request

# API key 来源，按优先级：
#   1. 环境变量 DASHSCOPE_API_KEY
#   2. 环境变量 LLM_OCR_VAULT 指向的 Obsidian 库里 llm-ocr 插件的 data.json
# 本仓库不存任何密钥。
KEY = os.environ.get("DASHSCOPE_API_KEY")
if not KEY:
    vault = os.environ.get("LLM_OCR_VAULT")
    if not vault:
        raise SystemExit(
            "需要 API key：设 DASHSCOPE_API_KEY，或设 LLM_OCR_VAULT 指向 Obsidian 库根目录")
    _cfg = json.load(open(f"{vault}/.obsidian/plugins/llm-ocr/data.json"))
    KEY = _cfg["keys"]["Custom"]
BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"
MODEL = "qwen3.7-plus"
# 转录提示词：与 Obsidian llm-ocr 插件「默认（古籍/档案·一字不差）」模板一致
PROMPT = """你是精准的古籍/档案 OCR 引擎。请把图片中的文字【一字不差】地转录出来。

要求：
1. 完整转录，不得概括、省略、跳过或改写；不要翻译、不要解释、不要评论，只输出文字本身。
2. 保持原有的段落与换行；竖排文本按【从右到左、从上到下】的阅读顺序转为横排输出。
3. 难以辨认的字用「□」占位，一个字一个□；切勿臆造，也不要用意思相近的字替代。
4. 繁体字、异体字、俗字保持原样，不要转成简体，也不要"规范化"。
5. 表格尽量用 Markdown 表格还原；印章、批注、页边小字可用「（印：…）」「（批：…）」标注。
6. 不要输出"以下是识别结果"之类的话，也不要加代码围栏(```)。"""

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

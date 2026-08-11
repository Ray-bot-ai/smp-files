# SMP 档案 · 大模型重做 OCR + 中文翻译

把上海公共租界工部局警务处档案（NARA M1750）的 **约 9 万页**影像，
用 **qwen3.7-plus** 重新转录成可用的英文全文，并生成中文对照，
最后发成一个可检索的 GitHub Pages 站点，每页链回 Internet Archive 原件。

配套语料与检索现状见 Obsidian 记忆 `smp-police-archive-corpus`、
本项目的实测数据见 `smp-vlm-ocr-plan`。抓取脚本在 `~/projects/smp-archive/`。

## 这是干嘛的（一句话）

Internet Archive 那份自动 OCR 在这种褪色缩微胶卷上基本失效
（实见 `Sar6aer enguirics revesleu` = `Further enquiries revealed`），
只能勉强撞词，不能读。重做一遍让它真正可检索。

## ⚠️ 三条必须记住的

### 1. 分辨率决定模型编不编造 —— 这是本项目最重要的一条

同一页、同模型、同提示词，只差图片分辨率：

| | 1000px | 2004px 原生 | 原件实际 |
|---|---|---|---|
| 工厂 | **Opium and Illegal Lottery Factory** | Cron and Nickel Plating Factory | Chrom and Nickel Plating Factory ✅ |
| 地址 | **1148/100 Yuhong Rd** | 1143/106 Yuhang Rd | 1143/106 Yuhang Rd ✅ |
| 父母姓名 | **整句删掉** | named Herman and Anna | named Hermann and Anna ✅ |
| 日期 | **13.1.44** | 13.12.44 | 13.12.44 ✅ |

低分辨率下模型不是认错，是**编**——通顺、像样、看不出来。
代价只有 +87% 输入 token、+18% 耗时。**一律走 `_w2400`（原生约 2004×2545），永远不要为省钱降。**

### 2. 提示词里的「难辨认用 □」模型不听

实测 1937 字输出里零个 □。只能靠后处理和新旧 OCR 交叉比对补，别指望模型自觉。

### 3. 引用规则

新 OCR 让检索从「能撞上」变成「基本可读」，但**数字、日期、人名、地址仍不可直接引用**，
必须回原件 PDF。新旧 OCR 双轨并存，不一致处 = 要人工核对处。

## 模型选型（实测，别再重推）

| 模型 | 关键事实命中 | 6页盲测被判异类 | ¥/页 | 9万页 | Batch半价 |
|---|---|---|---|---|---|
| qwen3.6-plus | 6/6 | 0/6 | 0.00870 | ¥783 | 不支持 |
| **qwen3.7-plus** | **6/6** | 2/6（伪信号） | 0.00616 | ¥554 | **¥277** ← 选它 |
| qwen3.5-plus | 5/6 | — | 0.00442 | ¥398 | 不支持 |
| qwen-vl-ocr | 4/6 | 4/6 | 0.00186 | ¥168 | ¥84 |
| qwen3.5-ocr | **0/6** | — | 0.00352 | ¥317 | 不支持 |

**反直觉：专用 OCR 模型 `qwen3.5-ocr` 全场最差**，把 dwelling house 读成
"dvening, where two men sit beside"，还凭空加出 "her mother-in-law and nephew"。
**型号数字 ≠ 该任务能力。**

## Batch 的坑

- **`enable_thinking:false` 必须放 `body` 顶层**（与 `model` 同级）。放 `extra_body` 无效——
  那是 OpenAI SDK 的透传机制，JSONL 里不吃。qwen3.5/3.6/3.7 **默认开思考**。

  **这是全项目唯一一个「不报错但账单翻倍」的坑。实测同一页对照：**

  | | in | out | reasoning_tokens | 耗时 | 9万页(Batch) |
  |---|---|---|---|---|---|
  | `enable_thinking:false` | 2722 | 508 | **0** | 17.7s | **¥342** |
  | 不设该参数（默认开） | 2720 | 2560 | **2042** | 48.3s | **¥933** |

  漏写 = 成本和时间都 ×2.7，**全程零报错**，只是"有点慢"，月底才见分晓。
  所以脚本必须在回收结果时主动核对 `usage.completion_tokens_details.reasoning_tokens == 0`，
  别只看"跑通了"。
- **单行 ≤1MB**：一张 400KB 图 base64 后约 530KB，塞得下但紧。
- **单文件 ≤5万条且 ≤500MB**：走 base64 实测每批只装得下约 **1,191 条** → 9 万页要 **76 个批次**。
  走图片 URL 则每行约 600 字节 → 只需 **2 个批次**。
- **IA 图片 URL 不能直传**：实测报 `<400> Download multimodal file timed out`（64.6 秒）。
  要走 URL 就得先把图传到**同地域 cn-beijing 的 OSS**（27GB，约 ¥3–4/月，跑完可删）。
- Batch **不支持免费额度、不支持上下文缓存**，必须开付费。
- 结果 30 天后自动删除，要及时下载。

## 其他实测

- **内容审查**：专挑中共传单、东区宣传品、王承祥共党活动案、南市暗杀案 4 件最敏感的，**4/4 通过**。
  但 `data_inspection_failed` 会以 **HTTP 200 + body 里藏 error** 的形式返回，脚本必须按这个来判失败。
- **中文件是净增量**：SMP 里夹的公安局公函、中共传单，IA 那边是纯乱码（它只跑英文 OCR），千问能正确转出繁体。
- **取图不要用 urllib**：IA 上会 `IncompleteRead`，一律 curl。
- 页数不要按 3,941 算：其中 60 件 `smpa-N` 是缩微整卷，内容与散件**重复**。唯一件是 **3,878**。

## 成本与时间

| | |
|---|---|
| OCR（qwen3.7-plus，Batch） | ¥277 |
| 全库英译中（qwen3.7-plus，Batch） | ¥145 |
| OSS 存 27GB | ¥4/月 |
| **合计** | **约 ¥426** |

下图 5–7 小时 + 2 个批次异步，全程 3–7 天。

## 怎么跑

```bash
python3 batch_pilot.py submit          # 10 页试点，验证流程
python3 batch_pilot.py status          # 查状态
python3 batch_pilot.py fetch           # 下结果，核对思考是否真关闭、实测单价
```

## 密钥

**本仓库不存任何密钥。** 百炼 key 按优先级从 `DASHSCOPE_API_KEY` 环境变量、
或 `LLM_OCR_VAULT` 指向的 Obsidian 库中 llm-ocr 插件的 `data.json` 读取（见 `common.py`）。
GitHub 发布用 `gh auth login`（凭据进系统钥匙串），不要在仓库里写 token——
这个仓库将来是公开的。

## 目录

```
common.py        配置 + API 封装 + 取图
batch_pilot.py   Batch 试点全流程
docs/index.html  GitHub Pages 预告页（未发布）
images/          页面影像（.gitignore）
batches/         JSONL 与结果（.gitignore）
```

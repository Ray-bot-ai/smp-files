# SMP 档案 · 大模型重做 OCR + 中文翻译

把上海公共租界工部局警务处档案（NARA M1750）的 **3,866 件 / 92,769 页**影像，
用 **qwen3.7-plus** 逐页重新转录成可用的英文全文，并生成中文对照，
发成一个可检索的 GitHub Pages 站点，每页链回 Internet Archive 原件。

**站点已上线**：https://ray-bot-ai.github.io/smp-files/

配套语料与检索现状见 Obsidian 记忆 `smp-police-archive-corpus`、
`smp-vlm-ocr-plan`。抓取脚本在 `~/projects/smp-archive/`。
**怎么干、干到哪、下一步** 见 [HANDOVER.md](HANDOVER.md)。

## 现状（2026-08-19 13:00 实测，不是估算）

| | |
|---|---|
| 转录 | **82,131 / 92,769 页（88.5%）** |
| 译文 | 77,061 页 |
| 剩余 | 858 件 / 11,592 页（剩下的都是小件） |
| 已花 | **¥788.60**（实时调用实账，非估算） |
| 预计总计 | **约 ¥900** |

查当前进度：`.venv/bin/python monitor.py`

## 这是干嘛的（一句话）

随影像附带的那份 OCR（Virtual Shanghai / bnAsie 项目用 ABBYY FineReader 制作）
在这种褪色缩微胶卷上基本失效
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

### 2. 提示词里的「难辨认用 □」模型不听，而且逼它听会更糟

早期提示词要求逐字用 □ 标出不可辨字符。实测两个后果，一个比一个坏：

- 模型基本不标（1,937 字输出里零个 □）；
- 真按「一个字一个□」写进提示词，会**逼出重复循环**——吐上千个 □ 撑爆
  max_tokens，页码标记丢失、整页正文消失，**全程不报错**。

现在的写法是「一片模糊只整段标一次 `〔此处转录不清〕`，然后继续往下转录」，
并明确告诉模型「一处看不清绝不能中断整页」。提示词在 `common.py` 的 `PROMPT`。

### 3. 引用规则

新 OCR 让检索从「能撞上」变成「基本可读」，但**数字、日期、人名、地址仍不可直接引用**，
必须回原件 PDF。新旧 OCR 双轨并存，不一致处 = 要人工核对处。

## 实际走的路线

**不是 Batch，是实时调用分批跑。** `run_chunked.py` 每批 40 件循环：

```
取图（archive.org，并发 8）→ 转录（qwen3.7-plus，并发 12）→ 翻译（并发 6）
  → 记影像指标 imgqual → 验证通过才删本地影像 → 下一批
```

同时后台预取下一批的图——取图打的是 archive.org、转录翻译打的是百炼，
两边资源独立，串行等于各自空等一半时间。

**为什么删影像是安全的**：影像可从 Internet Archive 再取（每页 URL 记在
`data/*.json` 的 `image_url` 里），转录产出才是不可替换的。但**必须先验证再删**，
且**影像指标必须在删之前算**。

**为什么没走 Batch**：试点排队 13 小时仍 0/10，进度完全不可控；
实时调用虽然贵一倍，但能看见进度、能随时中断续传、出事能当场发现。
对一个需要边跑边调提示词的项目，这个可控性值那一倍钱。
Batch 的实测结论仍有效，存档在文末，将来若要重做整库可以再考虑。

## 模型选型（实测，别再重推）

| 模型 | 关键事实命中 | 6页盲测被判异类 | ¥/页 | 9万页 |
|---|---|---|---|---|
| qwen3.6-plus | 6/6 | 0/6 | 0.00870 | ¥783 |
| **qwen3.7-plus** | **6/6** | 2/6（伪信号） | 0.00616 | ¥554 ← 选它 |
| qwen3.5-plus | 5/6 | — | 0.00442 | ¥398 |
| qwen-vl-ocr | 4/6 | 4/6 | 0.00186 | ¥168 |
| qwen3.5-ocr | **0/6** | — | 0.00352 | ¥317 |

**反直觉：专用 OCR 模型 `qwen3.5-ocr` 全场最差**，把 dwelling house 读成
"dvening, where two men sit beside"，还凭空加出 "her mother-in-law and nephew"。
**型号数字 ≠ 该任务能力。**

## 两个端点

- **主端点**：百炼 `qwen3.7-plus`，`enable_thinking:false` 一直有效，reasoning=0。
- **备用端点**：百炼内容审查会拒掉一部分页（返回 **HTTP 200，错误藏在 body 的
  `error` 字段里**，脚本必须按这个判失败）。换一个不走百炼审查的端点重试即可救回。
  被拒是**确定性的**，重试主端点无用。
  全库实测被拒极少，且**拒的原因是色情不是政治**——4 件最敏感的政治监视卷宗 4/4 通过。

**备用端点关不掉思考**：日志里的 `⚠ reasoning=N 思考没关掉！` 全部来自它。
逐件统计确认：走过备用端点的件才有 reasoning，没走过的 **0 件**有。
八种写法（`enable_thinking` / `reasoning_effort` / `thinking.type` /
`thinking_budget` / `generationConfig.thinkingConfig` / `extra_body.google.*`）
全部无效——不是参数写错，是这个端点没提供开关。**不是故障，别再查。**

## 成本

| | 预算（原 Batch 方案） | 实际（实时调用） |
|---|---|---|
| OCR + 翻译 | ¥426 | **¥788.60**（至 82,131 页） |
| 预计总计 | — | **约 ¥900** |

单价约 **¥0.0096/页**。走 Batch 可省一半，但见上文「为什么没走 Batch」。

## 怎么跑

```bash
bash scripts/relaunch.sh            # 启动跑批（nohup + caffeinate，日志 /tmp/smp-run.log）
.venv/bin/python monitor.py         # 看进度与异常信号（只读）
.venv/bin/python monitor.py --watch # 每 5 分钟刷一次
```

细节、收尾步骤、踩坑清单见 [HANDOVER.md](HANDOVER.md)。

## 密钥

**本仓库不存任何密钥。** 百炼 key 按优先级从 `DASHSCOPE_API_KEY` 环境变量、
或 `LLM_OCR_VAULT` 指向的 Obsidian 库中 llm-ocr 插件的 `data.json` 读取（见 `common.py`）。
GitHub 发布用 `gh auth login`（凭据进系统钥匙串），不要在仓库里写 token——
这个仓库将来是公开的。

## 目录

```
common.py         配置 + API 封装 + 取图 + 转录提示词（改提示词改这里）
demo_run.py       ★ 核心：转录 run_ocr / 翻译 run_translate / 退化检测 / 缺口补齐
run_chunked.py    ★ 分批跑批主入口（取图→转录→翻译→验证→删图）
fill_zh.py        只补译文缺口（转录已完成的件，run_chunked 不会碰）
monitor.py        只读监控：在跑吗、推进了吗、有没有出事
build_site.py     建站：从 data/*.json 生成 docs/data/ 全部索引与正文
make_review.py    生成人工复核队列 docs/data/review.json
run_all.sh        补译文缺口 + 全量跑批（串行，避免两边同时写 data/）
scripts/relaunch.sh   跑批启动器（处理 PYTHONPATH 与 SIGHUP，用这个启动）
scripts/stage_safe.sh 安全暂存：只提交「15 分钟前完成且校验通过」的数据文件
manifest.jsonl    全量清单，一行一件：ia_id/title/series/pages/pdf/error
data/*.json       ★ 核心产物，一件一个：每页 en/zh/error/tries/image_url
unusable_pages.json 人工判定放弃的页（站点标注、流水线跳过，两头都认这一份）
docs/             GitHub Pages 站点（已上线）
images/           页面影像（.gitignore，跑完即删）
```

---

## 附录：Batch 路线实测存档（最终没走，但结论有效）

将来若要重做整库、且能接受进度不可控，Batch 可省一半钱。以下是当时摸清的坑：

- **`enable_thinking:false` 必须放 `body` 顶层**（与 `model` 同级）。放 `extra_body` 无效——
  那是 OpenAI SDK 的透传机制，JSONL 里不吃。qwen3.5/3.6/3.7 **默认开思考**。
  实测同一页：关思考 in2722/out508/reasoning **0**/17.7s；不设该参数
  in2720/out2560/reasoning **2042**/48.3s。**漏写 = 成本和时间都 ×2.7，全程零报错。**
  回收结果时必须主动核对 `usage.completion_tokens_details.reasoning_tokens == 0`。
- **排队慢是常态，不是故障**。10 条的试点排了 13 小时仍 0/10。
  `completion_window` 最长可设 **14 天**，就是为这个准备的——**别用 24h**
  （繁忙期会直接 expired，白排），也别因为半天没动静就重提（重提=重新排队）。
- **单行 ≤1MB**；单文件 ≤5 万条且 ≤500MB。走 base64 每批只装得下约 1,191 条
  → 9 万页要 76 个批次；走图片 URL 每行约 600 字节 → 只需 2 个批次。
- **IA 图片 URL 不能直传**：报 `<400> Download multimodal file timed out`（64.6 秒）。
  要走 URL 就得先把图传到**同地域 cn-beijing 的 OSS**（27GB，约 ¥3–4/月，跑完可删）。
  OSS 私有桶 + 签名 URL 实测百炼能读、reasoning=0、转录正确。
- **百炼免费临时存储不能用于 Batch**：它要求 HTTP header，而 JSONL 没地方放 header。
- Batch **不支持免费额度、不支持上下文缓存**，必须开付费；结果 30 天后自动删除。
- **改流水线后先用免费测试模型验链路**：`batch_chain_test.py`，53 秒零费用。
  endpoint 用 `/v1/chat/ds-test`（**必须与 JSONL 里的 url 一致**），限 ≤1MB / ≤100 行 / 2 并行。
  踩过：一上来就提交真实批次，排队 95 分钟才知道链路对不对。
- 轮询多批次用 `retrieve(batch_id)`（1000 次/分钟），**别用 list 接口**（100 次/分钟）。
- 所有批次**一次性并行提交**，别串行——排队与提交量无关。

相关脚本仍在仓库里：`batch_pilot.py`、`batch_chain_test.py`、`oss_check.py`、`oss_bench.py`、`pipeline.py`。

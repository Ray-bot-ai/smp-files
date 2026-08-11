# 交接指南 · SMP 档案重做 OCR

写给**接手这个项目的人或 AI 智能体**。读完这份应该能独立continue，不需要回看聊天记录。

> 项目背景、模型选型实测、成本核算见 [README.md](README.md)。本文只讲**怎么干、干到哪了、下一步干什么**。

---

## 0. 一句话现状

3,878 件、约 **9.85 万页**上海工部局警务处档案，要用 `qwen3.7-plus` 逐页重做 OCR
并生成中文对照，最后发成可检索的 GitHub Pages 站点。
**当前处于「准备阶段」：清单在建、Batch 试点在排队、流水线已验证待全量启动。**

---

## 1. 环境与凭据

```bash
cd ~/projects/smp-vlm-ocr
uv venv && uv pip install oss2        # 已建好，直接用 .venv/bin/python
```

**两套凭据，都不在仓库里：**

| 用途 | 从哪来 | 怎么用 |
|---|---|---|
| 百炼 API | `DASHSCOPE_API_KEY` 环境变量；没有则从 `LLM_OCR_VAULT` 指向的 Obsidian 库读 llm-ocr 插件配置 | `common.py` 自动处理 |
| 阿里云 OSS | `~/.oss_env`（权限 600） | `oss_check.py` 的 `load_env()` 自动读 |

仓库根目录的 `.env`（已 gitignore）会被 `common.py` 自动读取，**不用每次 export**。
若没有该文件，新建：

```bash
echo 'LLM_OCR_VAULT=/path/to/your/obsidian/vault' > .env   # 或直接写 DASHSCOPE_API_KEY=sk-xxx
chmod 600 .env
```

**绝对不要**把 key 写进仓库、聊天或提交信息。GitHub 推送用 `gh auth login` 的 keyring 凭据。

---

## 2. 已经验证过的事实（别再重测，浪费钱和时间）

| 结论 | 证据 |
|---|---|
| **必须用原生分辨率 `_w2400`** | 降到 1000px 会让模型从「认错」变成「编造」，见 README 对照表 |
| `qwen3.7-plus` 是最优选择 | 6 页盲测 + 关键事实命中，与 3.6-plus 打平但支持 Batch 半价 |
| `qwen3.5-ocr` **不能用** | 专用 OCR 模型反而全场最差，0/6 |
| `enable_thinking:false` 必须在 `body` 顶层 | 漏写 = 成本时间都 ×2.7 且零报错 |
| **OSS 私有桶 + 签名 URL 可用** | 百炼实测能读，`reasoning=0`，转录正确 |
| IA 图片 URL **不能**直传百炼 | `<400> Download multimodal file timed out` |
| 百炼免费临时存储**不能**用于 Batch | 它要求 HTTP header，而 JSONL 没有放 header 的地方 |
| Batch 排队与提交量无关 | 10 条排 75 分钟仍 0 完成 |
| 内容审查不拦这批档案 | 4 件最敏感的政治监视卷宗 4/4 通过 |
| Batch 全链路代码正确 | 免费测试模型 53 秒跑完，20/20 回收，custom_id 往返一致 |
| 下载+上传流水线 3.6 页/秒 | 500 页实测零失败，全库约 7.5 小时 |
| 单页均 423 KB、中位宽 2047px | 500 页实测，全库约 40 GB |

---

## 3. 文件地图

```
common.py         百炼 API 封装 + 取图 + 提示词（改提示词改这里）
manifest.py       建全量页面清单 → manifest.jsonl
fetch_images.py   按清单下载单页图 → images/<ia_id>/n<N>.jpg
oss_check.py      OSS 端到端验证（连通/地域/权限/签名URL/百炼可读）
oss_bench.py      OSS 上传吞吐测速
pipeline.py       ★ 下载+上传流水线（正式取图用这个，不是 fetch_images.py）
batch_chain_test.py ★ 免费链路验证（改流水线后先跑这个）
batch_pilot.py    10 页真实 Batch 试点（验质量与计费）
docs/index.html   GitHub Pages 预告页（已上线）
manifest.jsonl    ← 核心产物，一行一件：ia_id/title/series/pages/pdf/error
state.jsonl       ← 流水线进度，一行一页：iid/n/dl/up/error
.env              本地配置（gitignore，不含密钥也别提交）
images/           页面影像（.gitignore，约 40 GB）
batches/          JSONL 与结果（.gitignore）
```

**外部依赖**：`~/projects/smp-archive/catalog.json`（3,941 件目录，别删）。

---

## 4. 当前进度与下一步

### ✅ 已完成
- 模型选型、成本核算、Batch 机制摸清
- OSS 桶 `smp-files-ocr`（cn-beijing，私有）建好并验通全链路
- 预告页上线 https://ray-bot-ai.github.io/smp-files/

### 🔄 进行中
- **清单**：`python3 manifest.py build` 跑着，约 1 小时。查进度 `python3 manifest.py stat`
- **Batch 试点**：`batch_8234f09d-f1da-4953-ae0e-8ff0dde9e221`，查 `python3 batch_pilot.py status`

### ⬜ 下一步（按顺序）

**⓪ 改动流水线后，先用免费测试模型验链路**
```bash
.venv/bin/python batch_chain_test.py     # 53 秒，零费用
```
> `batch-test-model` 跳过推理直接返回固定成功响应，专验「代码写对没」。
> endpoint 用 `/v1/chat/ds-test`（**必须与 JSONL 里的 url 一致**），限 ≤1MB / ≤100 行 / 2 并行。
> **本项目踩过**：一上来就提交真实批次，排队 95 分钟才知道链路对不对。
> 正确顺序是「先免费验链路，再花钱验质量」。

**① 补齐清单失败件**（清单跑完后立刻做）
```bash
python3 manifest.py retry     # 重试网络抖动导致的 JSONDecodeError
python3 manifest.py stat      # 确认「有问题」的件降到接近 0
```
> 剩下的 `no _page_numbers.json` 是真没有单页图接口，要单独走 PDF 拆页，件数很少。

**② 下载 + 上传流水线**（关键路径，**约 7.5 小时**，挂夜里跑）
```bash
.venv/bin/python pipeline.py run --limit 500   # 先试跑 500 页看速率
.venv/bin/python pipeline.py run               # 全量，可随时中断续传
.venv/bin/python pipeline.py stat              # 查进度
.venv/bin/python pipeline.py retry             # 只重试失败页
```
> 500 页实测 **3.6 页/秒、1.35 MB/s、零失败** → 98,500 页约 7.5 小时。
> 下行上行重叠执行；串行要 16.5 小时。默认 `--dl 24 --up 32`，可调。
> 进度记在 `state.jsonl` 而非靠文件是否存在——0 字节空壳会骗过后者。
> `fetch_images.py` 是只下载不上传的旧版，保留备用。

**③ 全量 Batch**（等 ① ② 完成 + 试点验证通过）
- 用签名 URL（7 天有效）而非 base64 → 每行约 180 字节 → **9.85 万页只需 2 个批次**
- JSONL 约 50 MB，**可以直接放 OSS 引用，不必走 files.create 上传**
  （`input_file_id` 可填 OSS 文件路径）
- 并行上限是 **1000 个任务**（「最多 2 个」只针对测试模型），2 个批次绰绰有余
- 轮询多批次要用 `retrieve(batch_id)`（1000 次/分钟），**别用 list 接口**（只有 100 次/分钟）
- `file_id` 可复用：批次要重提时不必重新上传，`client.files.list(purpose="batch")` 可查
- `completion_window` 设 **7 天**（不是 24h），防止赶上平台繁忙期任务 expired
- **所有批次一次性并行提交**，别串行——排队与提交量无关，串行纯浪费
- 回收后**必须核对 `reasoning_tokens == 0`**

**④ 翻译**（等 ③ 出英文全文）
- 纯文本，同样走 Batch 半价
- 建议整件一起译而不是逐页，上下文更完整、调用次数更少

**⑤ 建站**（等 ③ ④ 有真实数据）

---

## 5. 几个还没定的设计决策

接手的人需要拍板，**建议如下**：

**输出格式**：一件一个 JSON，含每页的 `{page_no, en_text, zh_text, ia_ocr_text, ia_page_url}`。
保留 IA 旧 OCR 是为了**交叉比对**——两份不一致处正是要人工核对处，这是免费的质检信号，别丢。

**站内检索方案**：9.85 万页 × 约 1.4 KB ≈ **130 MB 文本**，客户端一次性加载索引不现实。
建议用 **Pagefind**（专为静态站设计，索引分片按需加载），别用 Lunr/FlexSearch 那类要全量下载索引的。
这个决定影响站点目录结构，**动手建站前先定**。

**抽样校验**：建议随机抽 30–50 页，人工比对原件影像，统计错误率并按字段分类
（数字/日期/人名/地名/普通词），把结果写进站点的免责声明。现在页面上的说法是基于个位数样本的。

---

## 6. 踩过的坑（重复踩会浪费很多时间）

1. **取图用 curl，别用 urllib** —— IA 上 urllib 会 `IncompleteRead`。
2. **别给 curl 加 `--retry-all-errors`** —— 它会让 404 也重试，吞吐从 3.5 件/秒掉到 0.22。
3. **`archive.org/metadata` 是服务端限速**，加并发无效（P=8 与 P=20 一样）；
   **文件下载则并发有效**（P=24 约 3.5 件/秒）。两条路径的结论不能混用。
4. **断点续传要按「成功」而非「有记录」跳过** —— 否则网络抖动造成的失败会被永久跳过，
   数据静默缺失。`manifest.py` 踩过，已修（`retry` 子命令）。
5. **下载失败要删掉空壳文件** —— 否则续传会把 0 字节残留当成已完成。
6. **OSS 签名 URL 绑死 HTTP 方法** —— `sign_url("GET",...)` 生成的 URL 用 `curl -I`(HEAD) 测会 403，
   那是测试写错了不是 OSS 有问题。要测就用 GET。
7. **百炼内容审查返回 HTTP 200，错误藏在 body 的 `error` 字段里** —— 脚本必须按这个判失败，
   不能只看状态码。
8. **改了公共配置会静默打断正在跑的后台任务** —— 本项目把 `common.py` 改成必须读环境变量后，
   正在跑的轮询循环读不到 key，空跑一小时才发现。现在 `common.py` 会读同目录 `.env`（已 gitignore）。
9. **页数别按 3,941 算** —— 其中 60 件 `smpa-N` 是缩微整卷，与散件内容重复。唯一件是 **3,878**。

---

## 7. 给 AI 智能体的额外提醒

- **这批史料不可替换**。`~/projects/smp-archive/` 和用户的 Obsidian 库属于「丢了就没了」的数据，
  任何情况下不要原地修改或删除，要动先复制。代码可以随便改，有 git。
- **不要为省钱降图片分辨率**。这是本项目最重要的一条，理由见 README。
- **验证再汇报**。这个项目里已经出现过多次「参数传了但没生效」「测试写错导致假失败」，
  凡是说「搞定了」之前先跑一遍看输出。
- 用户是历史学研究者、不是程序员。解释用人话，给推荐项而不是罗列选项。

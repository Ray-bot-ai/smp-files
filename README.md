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
build_roll_map.py 卷宗 → 所在缩微胶卷（M1750 roll）对照 → docs/data/rolls.json
tools/build_appendix_a.py  从 MinerU 结果重建说明册附录 A → m1750_appendix_a.tsv
m1750_appendix_a.tsv  NARA 说明册附录 A（逐件 Box/Roll/File，3,708 行）
m1750_rolls.tsv   说明册卷次总表（67 卷各自的号码起讫，用户整理）
ref/m1750_pamphlet/   说明册的 MinerU 解析结果与页码对照（重建附录 A 用）
tools/walk_roll.py    沿卷定位的核心：全库页倒排索引 + 沿卷走算法
tools/locate_frames.py  第一轮：本机 Apple Vision 沿 60 卷走一遍（可续跑，帧缓存 .roll_frames/）
tools/fill_gaps.py    第二轮：第一轮没覆盖的帧交 MinerU，补定位（prepare / submit / merge）
tools/gap_fetch.py    稳妥取回 MinerU 结果（只下缺的、验 zip、单包重试），取齐自动 merge
roll_walk/            定位结果：<卷>.json（第一轮）、final.json（合并后）、rolls_frames.json（各卷帧数）
```

## 卷宗所在胶卷（roll）与卷内起始帧

卷宗页标题下写着这件卷宗的 SMP 号、所在的 M1750 roll，**定位到的直接链到卷内起始帧**
（`archive.org/details/smpa-23/page/n428/mode/2up`；IA 上只有整本 PDF 的 6、12、44、45 卷链到 PDF 页码）。
Roll 1 是 `smpa-1-3`；IA 上缺 29、39、64–67 卷，54 卷既无逐页图也无 PDF。重新生成：

```bash
python3 tools/build_appendix_a.py      # 只在改了附录 A 的解析时才需要
.venv/bin/python build_roll_map.py      # 每次站点数据更新后跑（读 roll_walk/final.json）
```

**卷内定位（2026-11-06，全量跑过一次，约 4 小时）**
- 原理：IA 上的单件与整卷是**两套扫描**（单件裁得紧、整卷带片边，2026-11-07 比图确认），但内容顺序一致：
  一卷 = 片头 + 各件首尾相接（偶有多出的重拍/空白帧）。
  所以沿卷走：识别一帧 → 与全库本站转录容错匹配，认出「某件第 k 页」→ 下一帧核对 → 跳过该件页数看下一件。
  每件只识别 2–3 帧，不必整卷识别，也不做影像全对全比对。
- 第一轮本机 Vision（免费、即时）；**Vision 读旧复写件明显弱于 MinerU**（Roll 23 试点稳定匹配 111 帧对 177），
  所以只作第一轮，没覆盖到的 6,496 帧（约 8%）交 MinerU 补。
- **实测位置优先于附录 A 推出的卷号**：108 件两者不同，抽读的都是本站转录把号码数字认错、附录 A 一路推错
  （如 1440 号胶卷登记章是 D.3054、转录读成 D.9054）。
- 抽查：第二轮补上的 599 件随机抽 15 件，胶卷帧文字与本站转录逐句一致，15/15 正确。
- 局限：只有一页的件只能凭一帧认（374 件），若同一份报告的复写副本归在两个卷宗里，文字完全相同，分不出来；
  第二轮有 79 件与已定位卷宗冲突、326 件证据不够，按宁缺毋滥不收。Roll 3 有 2 帧取不到。

**卷宗页的原件影像栏（2026-11-07）**
- 每页左栏嵌 IA 影像（热链，不存本地）：胶卷上**首尾相接**的卷宗（同卷下一件实测起点 = 本件起点 + 页数，
  1,908 件、26,738 页）显示整卷扫描第「起始帧 + n」帧；其余卷宗显示 IA 单件第 n 页。图下注明出处；
  胶卷帧载入失败（IA 偶尔回 502）自动换成单件。规则写在 `build_roll_map.py` 的 `page_frames()`。
- 为什么只用首尾相接的：抽 50 件 94 帧核对，首尾相接的全部对上（仅 2656 号一例单件重复扫一页、
  又缺胶卷上一页，两相抵消，中间错配一页）；下一件起点多出几帧的约三成是多出的帧夹在本件中间，
  从那里起整页错开一帧（7、312、2801 号）；单件页数多于胶卷帧数的、跨卷续接的同理
  （3278 号单件第 1、2 页重复，胶卷第 2 帧起就错一页）。这些从数据上分不出哪件错，故一律用单件影像。

**怎么来的（2026-11-06）**
- 依据是 NARA 说明册附录 A 的逐件表。**IA 给这本说明册配的 OCR 不能用**（大量漏 roll 号），
  所以剥掉原文字层、把跨页切成单页，用 MinerU 重做；原图逐行核对 8 页、两次独立识别 290 行全同，
  漏行的 1 页（单页 42）照原图补。
- **汇总表的号码区间不能单独拿来推**：D-755/1131/1190 实际在 Roll 3，D-4810 起在 Roll 15，
  带 (C) 的卷宗另摄在 Roll 59（D）和 Roll 60（N）。附录 A 本身**没有列 D-7734–8102**（Roll 33–40），
  只有这一段退回汇总表。
- 每件卷宗「自己的号」取自本站转录：先看封面标签页，再看登记章（取出现页数最多的），最后看 IA 标题。
- 结果分三级，页面上照实标明：exact（附录 A 精确命中）/ nearby（附录 A 前后条目同卷，推定）/
  range（附录 A 空当，按汇总表推定）。跨 4 卷以上、或查不到的，不显示。

**现状**：3,866 件中 3,552 件给出 roll：**3,005 件实测定位到卷内起始帧**（其中 45 件跨卷，各卷都给链接）；
其余 547 件只有附录 A 推出的卷号（exact 459 / nearby 67 / range 21），链到整卷并标明推定；314 件不给。
附录 A 这一路的残余错误来源是本站转录把号码数字认错——例如 754 号卷宗是 1932 年的报告，
登记章却读作 D-9563（D-95xx 是 1940 年前后的号，疑为 D-3563，此为推断、未核原件）——规则查不出来，
实测定位能纠正它（见上）。

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

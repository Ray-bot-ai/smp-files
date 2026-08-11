/* AI 检索代理：工具调用循环，跑在浏览器里。
   站点是 GitHub Pages 静态文件，没有服务器可以代调模型——所以循环在客户端跑，
   密钥全程不离开用户的浏览器，这比服务端代理更安全。

   与「一次性扩展检索词」的区别（这是本次改造的要点）：
   模型不是一口气想好所有词，而是搜一轮 → 看回来的题名与片段 → 从中发现新词
   （人名、机构、案名、当时行话）→ 再搜，迭代数轮；最后判断哪些真正相关并总结。
*/

const SYS_PROMPT = `你是上海公共租界工部局警务处档案（Shanghai Municipal Police Files, 1894–1949）的检索助手。
这是**英美殖民警察视角**的政治监视与刑事调查报告，原件为英文打字稿，夹杂中文原件。
本站已把其中一部分重新转录为可检索全文（英文转录 + 中文译文并存）。

## 检索工具的特性
- **简繁异体通搜**：输简体或繁体都能命中，无需手动转换。
- **中文按二字滑窗匹配**：查询需 ≥2 字。英文按词匹配、忽略大小写。
- **多词 = 并且(AND)**：一次 search 里给多个词会取交集，命中会很少。
  **想扩大召回请一次只给一个词/短语，分多次调用。**
- 检索范围仅限**已转录**部分，未转录的卷宗搜不到。
- 返回值里 \`samples\` 是**带原文摘录**的少数几件（判断相关性靠它），
  \`files\` 是**命中的全部卷宗题名**（只有题名与命中页数，条数多得多）。
  最终列结果时**以 \`files\` 为准**，不要只列 \`samples\` 那几件；
  没有摘录的那些，若题名本身足以判断相关，照样列出并说明依据是题名。

## 检索流程（务必遵循）

### 1. 视角转换 —— 最容易被忽略、最关键
档案是巡捕房写的，用他们的词、他们的拼法。用户的现代中文提问必须先"翻译"过去：
- **人名地名用当时的方言拼法，不是拼音**。拼音在档案里根本不存在。
  例：闸北=Chapei、南市=Nantao、虹口=Hongkew、吴淞=Woosung。
- **中共视角的说法要换成殖民警察的说法**：
  "共产党活动" → agitator / seditious / subversive / Bolshevik / Red / propaganda
  "工人运动" → labour unrest / strike / agitation / labour trouble
  "游行示威" → demonstration / procession / disturbance / affray
  "逮捕" → arrest / detention / raid / round-up
  "租界当局" → Municipal Council / Special Branch / ratepayers
- 巡捕房自己的建制词：Special Branch、Intelligence Office、Divisional Station、
  Detective Sub-Inspector、Registry。

### 2. 先宽后窄，逐个词试
一次 search 只给一个词或短语。先试最可能命中的，看回来多少。
命中 0 就换写法；命中过多就再加一个词收窄（多词是 AND）。

### 3. 滚雪球 —— 从命中结果里找新词（务必做）
读回来的题名和正文片段，从中提取**新的检索词**再搜：
- 出现的人名（尤其是罗马字拼法，那往往是档案里唯一的写法）
- 机构名、社团名、报刊名、街道名
- 当时的行话（如 affray、ratepayers、Red Terror、过激党）
- 案件编号前缀、卷宗系列
迭代 **2–4 轮**，直到新词不再带来新结果。

### 4. 判断相关性，去噪
逐条对照用户真实意图：剔除同名异人、字面沾边但实质无关的。
把握不准的保留并标注「待确认」+ 理由。

## 最终输出（用 Markdown，务必遵守）
- \`## 检索思路\` —— 你怎么把提问翻译成档案的语言，用了哪几类词，命中概况。
- \`## 结果\` —— 逐条列出，一条一行，格式固定：
  \`- **卷宗标题** ｜ 命中第 N 页 ｜ 为什么相关（一句话）\`
  卷宗标题必须与工具返回的完全一致，**绝不编造**。
- \`## 判断\` —— 从这些档案里能看出什么、有什么局限、还能往哪里查。
- 若命中很少，直接说明并给出建议的替代检索词，不要硬凑。

注意：本站转录由大模型生成，**人名、数字、日期不可靠**。你在总结里引用具体人名或数字时，
必须提醒用户回原件影像核对。`;

const TOOL_DEF = [{
  type: "function",
  function: {
    name: "search_archive",
    description: "在已转录的工部局警务处档案全文中检索。返回：total_pages/total_files 命中总量；" +
      "files 全部命中卷宗的题名与命中页数；samples 其中若干件的原文摘录。",
    parameters: {
      type: "object",
      properties: {
        query: { type: "string", description: "检索词。一次只给一个词或短语；给多个词是 AND 取交集，会大幅缩小结果。" },
        limit: { type: "integer", description: "返回多少条片段，默认 12，最大 30" }
      },
      required: ["query"]
    }
  }
}];

/* 给模型看的卷宗名单上限。题名平均约 55 字，全库将来有 3,866 件，
   全塞进去会撑爆上下文——所以「模型看到的」封顶，「界面显示的」不封顶。 */
const MODEL_FILE_CAP = 80;

let catIndex = null;
async function catMeta(id) {
  if (!catIndex) {
    const c = await loadCatalog();
    catIndex = {};
    for (const it of (c.items || [])) catIndex[it.i] = it;
  }
  return catIndex[id] || {};
}

/* 工具实现：调用本站已有的检索索引。
   返回 {res, all}：res 给模型（摘录限量），all 给界面（命中卷宗一件不漏）。
   题名取自 catalog.json，所以列全部不需要逐件 fetch 详情。 */
async function toolSearch(args) {
  const q = (args.query || "").trim();
  const limit = Math.min(args.limit || 12, 30);
  if (!q) return { res: { error: "query 为空" }, all: [] };
  const { toks, hits } = await search(q);
  const nPages = hits.reduce((a, h) => a + h.pages.length, 0);

  // 全部命中卷宗：只有题名与命中页数，不含摘录
  const all = [];
  for (const h of hits) {
    const m = await catMeta(h.doc);
    all.push({
      file: m.t || h.doc, doc_id: h.doc, series: m.s || "",
      pages_matched: h.pages.length, first_page: h.pages[0] + 1
    });
  }

  // 带摘录的样本：要逐件取全文，成本高，限量
  const samples = [];
  for (const h of hits.slice(0, limit)) {
    const d = await getDoc(h.doc);
    const byN = Object.fromEntries(d.pages.map(p => [p.n, p]));
    const n = h.pages[0];
    const pg = byN[n] || {};
    const src = hasMatch(pg.en, q) ? pg.en : (pg.zh || pg.en);
    samples.push({
      file: d.t, doc_id: h.doc, series: d.s,
      pages_matched: h.pages.length,
      first_page: n + 1,
      excerpt: (snippet(src, q, 260) || "").replace(/<\/?mark>/g, "")
    });
  }

  const res = {
    query: q, tokenised_as: toks, total_pages: nPages, total_files: hits.length,
    samples,
    // 名单比摘录便宜得多，所以模型能看到的卷宗数远多于摘录数
    files: all.slice(0, MODEL_FILE_CAP).map(f => ({ file: f.file, pages_matched: f.pages_matched }))
  };
  if (all.length > MODEL_FILE_CAP) {
    res.files_note = `命中 ${all.length} 件，此处只列前 ${MODEL_FILE_CAP} 件（按命中页数降序）；` +
      `如需收窄请换更具体的检索词。`;
  }
  return { res, all };
}

/* 主循环。onEvent 收到 {type, ...}：status / think / answer / tool / done / error */
async function runAgent(question, cfg, onEvent) {
  const base = cfg.base || 'https://dashscope.aliyuncs.com/compatible-mode/v1';
  const messages = [
    { role: "system", content: SYS_PROMPT },
    { role: "user", content: question }
  ];
  const MAX_ROUNDS = cfg.maxRounds || 12;

  for (let round = 1; round <= MAX_ROUNDS; round++) {
    onEvent({ type: "status", text: `第 ${round} 轮 · round ${round}` });
    // 最后两轮不再给工具，逼它收尾——否则会一直搜到轮数用尽而不出总结。踩过。
    const finishing = round > MAX_ROUNDS - 2;
    if (finishing && round === MAX_ROUNDS - 1) {
      messages.push({ role: "user", content:
        "检索够了。现在停止调用工具，按系统提示要求的格式给出最终汇总（## 检索思路 / ## 结果 / ## 判断）。" });
    }
    const body = {
      model: cfg.model, messages,
      ...(finishing ? {} : { tools: TOOL_DEF, tool_choice: "auto" }),
      temperature: 0.3, stream: true, stream_options: { include_usage: true },
      max_tokens: cfg.think === 'off' ? 2000 : 8000,
      ...(cfg.think === 'deep' ? { enable_thinking: true, thinking_budget: 4000, reasoning_effort: 'high' }
        : cfg.think === 'short' ? { enable_thinking: true, thinking_budget: 1000, reasoning_effort: 'low' }
        : { enable_thinking: false })
    };
    const resp = await fetch(base + '/chat/completions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + cfg.key },
      body: JSON.stringify(body)
    });
    if (!resp.ok) {
      const t = await resp.text();
      onEvent({ type: "error", text: `模型接口 ${resp.status}: ${t.slice(0, 300)}` });
      return;
    }

    // 流式解析
    const reader = resp.body.getReader(), dec = new TextDecoder();
    let buf = "", content = "", toolCalls = [];
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const lines = buf.split('\n'); buf = lines.pop();
      for (const ln of lines) {
        if (!ln.startsWith('data:')) continue;
        const raw = ln.slice(5).trim();
        if (raw === '[DONE]') continue;
        let j; try { j = JSON.parse(raw); } catch { continue; }
        if (j.error) { onEvent({ type: "error", text: JSON.stringify(j.error).slice(0, 300) }); return; }
        const d = j.choices?.[0]?.delta; if (!d) continue;
        if (d.reasoning_content) onEvent({ type: "think", text: d.reasoning_content });
        if (d.content) { content += d.content; onEvent({ type: "answer", text: d.content }); }
        for (const tc of (d.tool_calls || [])) {
          const i = tc.index ?? 0;
          toolCalls[i] ||= { id: tc.id || ('call_' + i), type: 'function', function: { name: '', arguments: '' } };
          if (tc.id) toolCalls[i].id = tc.id;
          if (tc.function?.name) toolCalls[i].function.name += tc.function.name;
          if (tc.function?.arguments) toolCalls[i].function.arguments += tc.function.arguments;
        }
      }
    }

    if (!toolCalls.length) { onEvent({ type: "done", rounds: round }); return; }

    messages.push({ role: "assistant", content: content || null, tool_calls: toolCalls });
    for (const tc of toolCalls) {
      let args = {}; try { args = JSON.parse(tc.function.arguments || '{}'); } catch {}
      const { res, all } = await toolSearch(args);
      onEvent({ type: "tool", query: args.query || '', result: res, all });
      messages.push({ role: "tool", tool_call_id: tc.id, content: JSON.stringify(res) });
    }
  }
  onEvent({ type: "answer", text: "\n\n（已达最大检索轮数。）" });
  onEvent({ type: "done", rounds: MAX_ROUNDS, capped: true });
}

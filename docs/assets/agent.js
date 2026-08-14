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
- **布尔检索**：\`all\`(每项都要有) / \`any\`(至少一项) / \`none\`(一项都不能有)，
  三者都在**同一页**上判定。同义词、异写、同一概念的不同说法**放进同一个 \`any\`
  一次搜完**，不要一个个单独搜再靠脑子拼——那样既漏得多，又容易把不相干的混进来。
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

### 2. 先把问题拆成「概念」，每个概念一个 any 集合
用户问的往往是**画像**（若干条件同时成立），不是某个词。
先把问题拆成 2–3 个概念；每个概念**穷举**档案里可能的英文说法（同义词、上下位词、
当时的行话、机构名、职业名），塞进一个 \`any\`；再用 \`all\` 把概念组合起来。

写法示意（概念 A ∩ 概念 B）：
\`\`\`
{ any: ["A 的说法1","A 的说法2","A 的说法3", …], all: ["B 的说法1"] }
\`\`\`
然后把 \`all\` 里的词换成 B 的其他说法各跑一次，把几轮结果合起来看。

**穷举同义词是这一步的关键。** 档案是巡捕房随手写的，同一件事有十几种说法：
职业既可能写成具体工种，也可能写成 worker / employed / working at；
学历既可能写 educated，也可能只是一句 attended … school。
只想到两三个词就开搜，必然大面积漏掉。宁可 \`any\` 里放十几个词——**这不花额外代价**。

**⚠ 多词短语是「词的 AND」，不是短语匹配。**
\`"A B"\` 的意思是「同一页里既有 A 又有 B」，因此复合词会**排除**只写了其中一个词的页。
每个概念都要同时放入**裸词**和更具体的复合词，否则会大面积漏掉写法。

命中 0 就换写法或放宽（去掉 all）；命中过多就往 \`all\` 里再加一个概念收窄。

### 3. 关键词只负责「捞进来」，算不算数要靠**读**
很多条件在档案里没有固定说法，靠列同义词是列不完的——
一个人的身份可能写成职务、写成受雇于某处、写成一句转述，
**这类表述的变体是无穷的，关键词天然抓不全，也判不准**。
所以正确做法是：

1. **先撒大网**：用宽松的 \`any\`（宁可多而杂）把可能相关的页尽量捞进候选，
   此时不要急着用 \`all\` 收窄，也不要看命中多就换更窄的词——**召回优先**。
2. **再逐页读**：对候选页调用 \`read_pages\` 读完整原文，**由你自己判断**
   这一页写的人和事是否真的符合用户要的画像。
3. 判断依据写进结果里（哪句话对应哪个条件）。

摘录（samples）只是一小段上下文，**不足以判断**；拿不准就 \`read_pages\`，别猜。

### 4. 滚雪球 —— 从命中结果里找新词（务必做）
读回来的题名和正文片段，从中提取**新的检索词**再搜：
- 出现的人名（尤其是罗马字拼法，那往往是档案里唯一的写法）
- 机构名、社团名、报刊名、街道名
- 当时的行话（如 affray、ratepayers、Red Terror、过激党）
- 案件编号前缀、卷宗系列
迭代 **2–4 轮**，直到新词不再带来新结果。

### 5. 判断相关性，去噪 —— 宁缺毋滥
逐条对照用户真实意图：剔除同名异人、字面沾边但实质无关的。
- **不要用"相邻画像"凑数**。问的是「工厂工人且受过教育」，那么"教师"（不是工人）、
  "党的组织原则"（不是个人）、"知识青年失业"（宏观背景）**都不算命中**，
  不要列进 \`## 结果\`。真觉得有用就单独放一句在 \`## 判断\` 里，并说明它只是背景。
- **不要因为某个机构反复出现就把它当成结论**。它可能只是转录较多、或该词恰好高频。
- 宁可给 5 条真正对得上的，也不要 12 条里有一半要读者自己筛。
把握不准的保留并标注「待确认」+ 理由。

## 最终输出（用 Markdown，务必遵守）
- \`## 检索思路\` —— 你怎么把提问翻译成档案的语言，用了哪几类词，命中概况。
- \`## 结果\` —— 只列**直接命中**的，逐条一行，格式固定：
  \`- **卷宗标题** ｜ 命中第 N 页 ｜ 为什么相关（一句话）\`
  卷宗标题必须与工具返回的完全一致，**绝不编造**。
  「为什么相关」必须指出**用户要的那几个条件各自落在哪里**
  （如"织工=工人条件，供词自述读到中学=教育条件"）；说不出来的就别列。
- \`## 判断\` —— 从这些档案里能看出什么、有什么局限、还能往哪里查。
- 若命中很少，直接说明并给出建议的替代检索词，不要硬凑。

注意：本站转录由大模型生成，**人名、数字、日期不可靠**。你在总结里引用具体人名或数字时，
必须提醒用户回原件影像核对。`;

const TOOL_DEF = [{
  type: "function",
  function: {
    name: "search_archive",
    description:
      "在已转录的工部局警务处档案全文中做**布尔检索**，三个条件都在同一页上判定。" +
      "返回：total_pages/total_files 命中总量；files 全部命中卷宗；samples 若干件的原文摘录。",
    parameters: {
      type: "object",
      properties: {
        query: { type: "string", description: "单个词或短语。与 all/any/none 二选一用。" },
        all: { type: "array", items: { type: "string" },
               description: "AND：每一项都必须出现在同一页。" },
        any: { type: "array", items: { type: "string" },
               description: "OR：至少出现一项。把同义词/异写放在这里一次搜完。" +
                 "注意：多词短语是**词的 AND**不是短语匹配——\"middle school\" 要求同页既有 middle 又有 school，" +
                 "因此匹配不到 \"private school\"。所以务必同时放入裸词（school / worker / mill）。" },
        none: { type: "array", items: { type: "string" },
                description: "NOT：出现任一项就排除该页。用来滤掉已知的干扰义。" },
        limit: { type: "integer", description: "返回多少条摘录。证据不够就调大，没有硬上限。" }
      }
    }
  }
}, {
  type: "function",
  function: {
    name: "read_pages",
    description:
      "读取指定卷宗指定页的**完整转录原文**（英文 + 中文译文）。" +
      "关键词检索只能保证把页捞进候选；**判断一页算不算数必须靠读原文**。" +
      "命中多、或摘录看不出所以然时，用它逐页读。",
    parameters: {
      type: "object",
      properties: {
        doc_id: { type: "string", description: "卷宗号，来自检索结果的 doc_id" },
        pages: { type: "array", items: { type: "integer" },
                 description: "要读的页码（从 1 开始）。省略则读该件全部已转录页。" }
      },
      required: ["doc_id"]
    }
  }
}];

/* 这些只是**默认值**，不是上限。用户自带 API key，想给模型看多少是他的事——
   他花的是自己的钱，我们只负责说清楚哪一项会更贵。
   设 0 表示不限。**不要在这里夹取用户给的数值。** */
const CAP = {
  files: 400,      // 名单（只有题名 + 命中页数，很便宜）
  samples: 40      // 摘录（要逐件取全文，较贵）
};

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
  args = (args && typeof args === "object") ? args : {};
  const wantSamples = Number(args.limit) > 0 ? Number(args.limit) : CAP.samples;  // 0 = 不限
  /* 参数一律按「可能是任何形状」处理。模型经常不照签名传：
     query 给成数组、all 给成字符串、数字当字符串……
     以前 query 直接当字符串 .trim()，模型传个数组就整轮崩掉
     （实见 "(args.query || "").trim is not a function"）。
     宁可宽进，也不要因为一次参数写歪就把整次检索毁掉。 */
  const A = (x) => (Array.isArray(x) ? x.flat(3) : x === null || x === undefined ? [] : [x])
    .map(v => (typeof v === "object" ? JSON.stringify(v) : String(v)).trim())
    .filter(Boolean);
  const all = A(args.all), any = A(args.any), none = A(args.none);
  const qs = A(args.query);          // query 也可能是数组，多项按 AND 处理（与文档一致）
  if (!qs.length && !all.length && !any.length) {
    return { res: { error: "没有给检索条件：请给 query，或给 all / any / none。" }, all: [] };
  }

  let hits, toks, label;
  const useBool = all.length || any.length || none.length || qs.length > 1;
  if (useBool) {
    const allTerms = all.concat(qs);
    const r = await searchBool({ all: allTerms, any, none });
    hits = r.hits;
    toks = null;
    label = [allTerms.map(t => `+${t}`).join(' '),
             any.length ? `(${any.join(' | ')})` : '',
             none.map(t => `-${t}`).join(' ')].filter(Boolean).join(' ');
  } else {
    const r = await search(qs[0]);
    hits = r.hits; toks = r.toks; label = qs[0];
  }
  const nPages = hits.reduce((a, h) => a + h.pages.length, 0);

  // 摘要高亮用哪个词：布尔检索时 query 可能为空，取条件里第一个实词
  const hlq = qs[0] || all[0] || any[0] || "";

  // 全部命中卷宗：只有题名与命中页数，不含摘录
  const allFiles = [];
  for (const h of hits) {
    const m = await catMeta(h.doc);
    allFiles.push({
      file: m.t || h.doc, doc_id: h.doc, series: m.s || "",
      pages_matched: h.pages.length, first_page: h.pages[0] + 1
    });
  }

  // 带摘录的样本：要逐件取全文，成本高，限量
  const samples = [];
  for (const h of (wantSamples > 0 ? hits.slice(0, wantSamples) : hits)) {
    const d = await getDoc(h.doc);
    const byN = Object.fromEntries(d.pages.map(p => [p.n, p]));
    const n = h.pages[0];
    const pg = byN[n] || {};
    const src = hasMatch(pg.en, hlq) ? pg.en : (pg.zh || pg.en);
    samples.push({
      file: d.t, doc_id: h.doc, series: d.s,
      pages_matched: h.pages.length,
      first_page: n + 1,
      excerpt: (snippet(src, hlq, 260) || "").replace(/<\/?mark>/g, "")
    });
  }

  const res = {
    query: label, tokenised_as: toks, total_pages: nPages, total_files: hits.length,
    samples,
    // 名单比摘录便宜得多，所以模型能看到的卷宗数远多于摘录数
    files: (CAP.files > 0 ? allFiles.slice(0, CAP.files) : allFiles)
             .map(f => ({ file: f.file, pages_matched: f.pages_matched }))
  };
  if (CAP.files > 0 && allFiles.length > CAP.files) {
    res.files_note = `命中 ${allFiles.length} 件，此处只列前 ${CAP.files} 件（按命中页数降序）。`;
  }
  return { res, all: allFiles };
}

/* 读整页原文。关键词负责召回，语义判断靠模型自己读——
   「在某私立学校任职」这种说法，任何同义词表都枚举不出来，只能读了才知道。 */
async function toolReadPages(args) {
  args = (args && typeof args === "object") ? args : {};
  const id = String(args.doc_id ?? "").trim().replace(/^smpa-files-/, "");
  if (!id) return { res: { error: "缺 doc_id" }, all: [] };
  let d;
  try { d = await getDoc(id); } catch { return { res: { error: `读不到卷宗 ${id}` }, all: [] }; }
  const want = Array.isArray(args.pages) ? args.pages.flat(3).map(Number).filter(n => n > 0) : null;
  const out = [];
  for (const p of d.pages) {
    if (want && !want.includes(p.n + 1)) continue;
    if (!(p.en || p.zh)) continue;
    out.push({ page: p.n + 1, en: p.en || "", zh: p.zh || "",
               ...(p.bad ? { warning: "本页转录被标为不可用：" + p.bad } : {}) });
  }
  return { res: { doc_id: id, file: d.t, pages_returned: out.length, pages: out },
           all: [{ file: d.t, doc_id: id, pages_matched: out.length, first_page: out[0]?.page || 1 }] };
}

/* 关思考的参数**各家不一样**，传错了不报错、只是不生效。
   实测：DeepSeek 完全无视 qwen 的 enable_thinking:false（照样吐 349 字思考），
   要用 thinking:{type:"disabled"} 才真关掉（0 字）。
   这类「参数传了但没生效」的坑本项目已经踩过一次（当初 qwen Batch 的
   enable_thinking 放错层级，账单翻 2.7 倍且全程零报错），不要再踩第二次。 */
function thinkParams(base, mode) {
  const deepseek = /deepseek/i.test(base || '');
  if (deepseek) {
    if (mode === 'off') return { thinking: { type: 'disabled' } };
    return { thinking: { type: 'enabled' },
             reasoning_effort: mode === 'deep' ? 'high' : 'low' };
  }
  if (mode === 'deep') return { enable_thinking: true, thinking_budget: 4000, reasoning_effort: 'high' };
  if (mode === 'short') return { enable_thinking: true, thinking_budget: 1000, reasoning_effort: 'low' };
  return { enable_thinking: false };
}

/* 主循环。onEvent 收到 {type, ...}：status / think / answer / tool / done / error */
async function runAgent(question, cfg, onEvent) {
  const base = cfg.base || 'https://dashscope.aliyuncs.com/compatible-mode/v1';
  const messages = [
    { role: "system", content: SYS_PROMPT },
    { role: "user", content: question }
  ];
  const MAX_ROUNDS = cfg.maxRounds || 12;
  if (cfg.capFiles !== undefined && cfg.capFiles !== '') CAP.files = +cfg.capFiles;
  if (cfg.capSamples !== undefined && cfg.capSamples !== '') CAP.samples = +cfg.capSamples;

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
      // max_tokens 是**思考与正文共用**的。给小了，模型把额度花在思考上，
      // 最后一轮就吐不出总结——实见 DeepSeek 跑满 11 轮、检索 48 次，
      // 最终答案完全空白。所以这里给足。
      max_tokens: cfg.maxTokens || (cfg.think === 'off' ? 8000 : 16000),
      ...thinkParams(base, cfg.think)
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

    if (!toolCalls.length) {
      if (!content.trim()) {
        // 一个字都没吐出来 = 额度被思考吃光，或模型直接收工。不能静默当成功。
        onEvent({ type: "error", text:
          "模型这一轮没有输出正文。max_tokens 是思考与正文**共用**的额度，" +
          "开启思考时很容易被思考吃光。可在设置里调大 max_tokens（更花钱、质量通常更好），" +
          "或改用较低的思考档位——两条路都行，看你要什么。" });
      }
      onEvent({ type: "done", rounds: round });
      return;
    }

    messages.push({ role: "assistant", content: content || null, tool_calls: toolCalls });
    for (const tc of toolCalls) {
      let args = {}; try { args = JSON.parse(tc.function.arguments || '{}'); } catch {}
      // 工具出错不能把整轮检索带崩：把错误当成工具结果回给模型，让它自己改参数重试。
      let res, all;
      try {
        ({ res, all } = tc.function.name === "read_pages"
          ? await toolReadPages(args)
          : await toolSearch(args));
      } catch (e) {
        res = { error: `检索调用失败：${e && e.message ? e.message : e}。` +
                       `请检查参数：query 是字符串，all/any/none 是字符串数组。` };
        all = [];
      }
      onEvent({ type: "tool", query: (res && res.query) || '', result: res, all });
      messages.push({ role: "tool", tool_call_id: tc.id, content: JSON.stringify(res) });
    }
  }
  onEvent({ type: "answer", text: "\n\n（已达最大检索轮数。）" });
  onEvent({ type: "done", rounds: MAX_ROUNDS, capped: true });
}

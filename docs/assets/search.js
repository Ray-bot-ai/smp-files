/* 全文检索：中文走「归一化 bigram」，与 build_site.py 的分词必须完全一致。
   变体表在 variants.min.json（字符 → 归一字形），由构建脚本从简繁异体通搜插件的数据生成。 */
const SHARDS = 512;
let NORM = null, shardCache = {}, catalog = null;

async function loadNorm() {
  if (NORM) return NORM;
  const groups = await (await fetch('data/variants.min.json')).json();
  NORM = {};
  for (const g of groups) for (const c of g) NORM[c] = g[0];
  return NORM;
}
/* 高频停用词表。build_site.py 把出现在 >40% 页面上的 token 裁出索引以控体积，
   前端若把「索引里没有」一律当成零命中，正常查询就会**静默返回空**——
   这批档案每页都印着「上海公共租界工部局警务处 / SHANGHAI MUNICIPAL POLICE」抬头，
   于是 上海/租界/工部/警务/police/shanghai/municipal 全被裁掉，
   搜「法租界」「公共租界」直接零结果且不给解释。实测抓出。
   有了这份名单就能区分两种「索引里没有」：
     · 在名单里 = 高频停用词 → **跳过这一项**，用其余词判定，并在页面上明说
     · 不在名单里 = 这个词全库真的没有 → 零命中（正确） */
let STOP = null;
async function loadStop() {
  if (STOP) return STOP;
  try { STOP = await (await fetch('data/stop.json')).json(); }
  catch { STOP = { cutoff: 0, pages: 0, tokens: {} }; }
  return STOP;
}
// 本轮检索里因高频而被跳过的词，供页面提示用
let SKIPPED = [];

async function loadCatalog() {
  if (!catalog) catalog = await (await fetch('data/catalog.json')).json();
  return catalog;
}
const normalize = s => [...s].map(c => (NORM && NORM[c]) || c).join('');

// 与 Python 端 shard_of 一致：md5 前 4 位十六进制 % SHARDS
async function md5hex(str) {
  const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(str));
  return [...new Uint8Array(buf)].map(b => b.toString(16).padStart(2, '0')).join('');
}

function tokenize(q) {
  const t = new Set();
  const low = q.toLowerCase();
  for (const m of low.matchAll(/[a-z][a-z'\-]{1,}/g)) t.add(m[0]);
  for (const m of q.matchAll(/[㐀-鿿]+/g)) {
    const n = normalize(m[0]);
    if (n.length === 1) t.add(n);
    for (let i = 0; i < n.length - 1; i++) t.add(n.slice(i, i + 2));
  }
  return [...t];
}

async function getShard(i) {
  if (!(i in shardCache)) shardCache[i] = fetch(`data/idx/${i}.json`).then(r => r.json());
  return shardCache[i];
}

/* 一个检索词命中的「页」集合，元素是 "doc#page"。词内部多 token 仍是 AND。
   这是布尔检索的原子操作：有了它，AND/OR/NOT 都只是集合运算。 */
/* 中文三字以上的词需要**逐页核对原文**，不能只信索引。
   索引是二字滑窗（bigram）：「巡捕房」被拆成 巡捕 + 捕房 再取交集，
   于是「巡捕」和「捕房」分别出现在同一页的不同地方也会命中，页面里其实没有这个词。
   实测假命中率：巡捕房 5.8%、共产党 0.1%。二字词不受影响——
   它的唯一 bigram 就是它自己，命中即等于原样出现。
   所以只对 ≥3 字的中文词做核对：取回该页正文，确认归一化后确实含有这个子串。 */
function needsVerify(q) {
  const cjk = (q.match(/[㐀-鿿]/g) || []).length;
  return cjk >= 3;
}

async function verifyPages(term, pages) {
  const nt = normalize(term.toLowerCase());
  const byDoc = {};
  for (const k of pages) { const [d, p] = k.split('#'); (byDoc[d] ||= []).push(+p); }
  const keep = new Set();
  await Promise.all(Object.entries(byDoc).map(async ([doc, ps]) => {
    let d; try { d = await getDoc(doc); } catch { ps.forEach(p => keep.add(doc + '#' + p)); return; }
    const byN = Object.fromEntries(d.pages.map(x => [x.n, x]));
    for (const p of ps) {
      const pg = byN[p] || {};
      const txt = normalize(((pg.en || '') + '\n' + (pg.zh || '')).toLowerCase());
      if (txt.includes(nt)) keep.add(doc + '#' + p);
    }
  }));
  return keep;
}

async function pageSet(q) {
  await loadNorm();
  await loadStop();
  const toks = tokenize(q);
  if (!toks.length) return new Set();
  const shardIdx = {};
  for (const t of toks) shardIdx[t] = parseInt((await md5hex(t)).slice(0, 4), 16) % SHARDS;
  const need = [...new Set(Object.values(shardIdx))];
  const loaded = {};
  await Promise.all(need.map(async i => { loaded[i] = await getShard(i); }));
  let inter = null;
  for (const t of toks) {
    const posts = loaded[shardIdx[t]][t];
    // 高频停用词：跳过这一项而不是判零命中（见 STOP 处注释）
    if (!posts && STOP.tokens && (t in STOP.tokens)) {
      if (!SKIPPED.some(x => x.t === t)) SKIPPED.push({ t, n: STOP.tokens[t] });
      continue;
    }
    if (!posts) return new Set();
    // postings 是 {doc: [pages]}（按卷宗归并，省掉重复的 doc 号）。
    // 这一段与 build_site.py 写索引处是一对，改一边必须改另一边——
    // 两边不一致会「永远零命中且不报任何错」，本项目踩过。
    const set = new Set();
    for (const d in posts) for (const pg of posts[d]) set.add(d + '#' + pg);
    inter = inter ? new Set([...inter].filter(x => set.has(x))) : set;
    if (!inter.size) return new Set();
  }
  // 全部词都是高频停用词时 inter 仍是 null（比如只搜「police」）。
  // 这时无法用索引筛选，返回空集，由页面明确告诉用户「这个词太常见，
  // 请加一个别的词一起搜」——而不是假装没命中。
  if (!inter) return new Set();
  return needsVerify(q) ? verifyPages(q, inter) : inter;
}

function groupByDoc(pages) {
  const byDoc = {};
  for (const k of pages) {
    const [d, p] = k.split('#');
    (byDoc[d] ||= []).push(+p);
  }
  return Object.entries(byDoc)
    .map(([d, ps]) => ({ doc: d, pages: ps.sort((a, b) => a - b) }))
    .sort((a, b) => b.pages.length - a.pages.length);
}

/* 布尔检索，三个条件都在**同一页**上判定：
     all  每一项都要出现（AND）
     any  至少出现一项（OR）
     none 一项都不能出现（NOT）
   为什么需要它：很多真实问题是「A 类词 之一 且 B 类词 之一」，
   例如「(受过教育 或 当过教师) 且 (工厂 或 学徒)」。
   只有 AND 的话，模型只能把同义词一个个单独搜、再靠脑子拼，
   既漏得多又容易把「相邻但不相干」的东西混进结果。 */
/* 整项都是高频停用词吗？（如「police」这一项）

   必须单独判：布尔检索是**逐项**调 pageSet 的，而这样一项的 pageSet 是空集，
   拿空集去做 AND 会把整条查询清零——实测搜「police opium」得 0 件，
   而把同一串当单项搜是 878 件。用户看到的就是「加了个词反而没结果」。
   所以这类项要**整项跳过、不参与布尔运算**，并记进 SKIPPED 让页面明说。 */
async function allStop(term) {
  await loadStop();
  const ts = tokenize(term);
  if (!ts.length || !STOP.tokens) return false;
  if (!ts.every(t => t in STOP.tokens)) return false;
  for (const t of ts) if (!SKIPPED.some(x => x.t === t)) SKIPPED.push({ t, n: STOP.tokens[t] });
  return true;
}

async function searchBool({ all = [], any = [], none = [] }) {
  let cur = null;
  for (const t of all) {
    if (await allStop(t)) continue;          // 整项高频：不参与 AND
    const s = await pageSet(t);
    cur = cur ? new Set([...cur].filter(x => s.has(x))) : s;
    if (!cur.size) return { hits: [], used: { all, any, none } };
  }
  if (any.length) {
    const u = new Set();
    let usable = 0;
    for (const t of any) {
      if (await allStop(t)) continue;        // OR 里的高频项等于「全部命中」，跳过
      usable++;
      for (const x of await pageSet(t)) u.add(x);
    }
    if (usable) cur = cur ? new Set([...cur].filter(x => u.has(x))) : u;
  }
  if (!cur) return { hits: [], used: { all, any, none } };
  for (const t of none) {
    if (await allStop(t)) continue;          // NOT 里的高频项会排除掉全库，跳过
    const s = await pageSet(t);
    cur = new Set([...cur].filter(x => !s.has(x)));
  }
  return { hits: groupByDoc(cur), used: { all, any, none } };
}

async function search(q) {
  const toks = tokenize(q);
  if (!toks.length) return { toks: [], hits: [] };
  return { toks, hits: groupByDoc(await pageSet(q)) };
}

const docCache = {};
async function getDoc(id) {
  if (!(id in docCache)) docCache[id] = fetch(`data/doc/${id}.json`).then(r => r.json());
  return docCache[id];
}

/* 摘要：在归一化后的文本上定位，再映射回原文切片——
   这样搜「国民党」能在繁体原文「國民黨」上正确高亮。 */
function snippet(text, q, len = 190) {
  if (!text) return '';
  const nt = normalize(text.toLowerCase()), nq = normalize(q.toLowerCase().trim());
  let i = nq ? nt.indexOf(nq) : -1;
  if (i < 0) {
    for (const t of tokenize(q)) { i = nt.indexOf(t); if (i >= 0) { break; } }
  }
  if (i < 0) i = 0;
  const s = Math.max(0, i - Math.floor(len / 3));
  let out = text.slice(s, s + len);
  const esc = x => x.replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
  out = esc(out);
  // 高亮：对每个 token 在原文切片的归一化投影上找位置
  const nout = normalize(out.toLowerCase());
  const marks = [];
  for (const t of tokenize(q)) {
    let p = nout.indexOf(t);
    while (p >= 0) { marks.push([p, p + t.length]); p = nout.indexOf(t, p + 1); }
  }
  marks.sort((a, b) => a[0] - b[0]);
  let res = '', last = 0;
  for (const [a, b] of marks) {
    if (a < last) continue;
    res += out.slice(last, a) + '<mark>' + out.slice(a, b) + '</mark>';
    last = b;
  }
  res += out.slice(last);
  return (s > 0 ? '…' : '') + res + (s + len < text.length ? '…' : '');
}

/* 该字段里到底有没有命中？用于决定摘要先显示英文还是中文——
   命中在译文里却只显示英文开头，会让中文检索看起来像没生效。 */
function hasMatch(text, q) {
  if (!text) return false;
  const n = normalize(text.toLowerCase());
  return tokenize(q).some(t => n.includes(t));
}

const fmt = n => n.toLocaleString('en-US');

/* 把一行查询解析成 AND / OR / NOT 三组。
   规则刻意简单，够用且好解释：
     空格分隔   → all（都要有）
     | 或 OR    → any（任一即可），可写成 a | b | c
     -前缀      → none（排除）
     "引号"     → 整体当一个短语
   全是 all 且只有一项时走原来的单词检索，行为不变。 */
function parseQuery(raw) {
  /* 中文不写空格，所以运算符必须在**没有空格**时也能认出来，否则
     「工人|学校」会被当成一整个词，中文分词把 | 丢掉、两个词变成 AND——
     不报错、结果却完全相反。全角符号同理（｜－），中文输入法下常打出全角。 */
  raw = String(raw || '')
    .replace(/[｜]/g, '|').replace(/[－]/g, '-').replace(/[　]/g, ' ')
    .replace(/\s*\|\s*/g, ' | ')        // | 前后补空格
    .replace(/(^|[\s|])-(?=\S)/g, '$1 -'); // 词首的 - 独立出来
  const toksRaw = raw.match(/"[^"]+"|\S+/g) || [];
  const all = [], any = [], none = [];
  let orMode = false;
  for (let t of toksRaw) {
    if (t === '|' || t.toUpperCase() === 'OR') { orMode = true; continue; }
    let neg = false;
    if (t.startsWith('-') && t.length > 1) { neg = true; t = t.slice(1); }
    t = t.replace(/^"|"$/g, '').trim();
    if (!t) continue;
    if (neg) none.push(t);
    else if (orMode) { any.push(t); orMode = false; if (all.length) any.push(all.pop()); }
    else all.push(t);
  }
  return { all, any, none };
}

async function parseAndSearch(raw) {
  const { all, any, none } = parseQuery(raw);
  if (!any.length && !none.length && all.length <= 1) {
    const r = await search(raw);
    return { toks: r.toks, hits: r.hits, label: raw };
  }
  const r = await searchBool({ all, any, none });
  const label = [all.map(t=>'+'+t).join(' '),
                 any.length ? '(' + any.join(' | ') + ')' : '',
                 none.map(t=>'-'+t).join(' ')].filter(Boolean).join(' ');
  return { toks: [...all, ...any], hits: r.hits, label };
}

/* ── 标题检索 ──────────────────────────────────────────────
   正文索引只覆盖已转录部分，其余约六成卷宗在检索页完全隐形。
   标题索引（docs/data/titleidx.json，单文件）覆盖全库 3,866 件标题：
   token 语义与正文完全一致（英文整词、中文归一化 bigram），
   只是所有 token 都要落在**同一个标题**上。三字以上中文仍是
   bigram 交集、可能假命中——好在标题都在内存里，直接核对子串。 */

let titleIdx = null;
async function loadTitleIdx() {
  if (!titleIdx) titleIdx = fetch('data/titleidx.json').then(r => r.json());
  return titleIdx;
}

async function titleSet(term) {
  await loadNorm();
  const idx = await loadTitleIdx();
  const toks = tokenize(term);
  if (!toks.length) return new Set();
  let inter = null;
  for (const t of toks) {
    const posts = idx[t];
    if (!posts) return new Set();
    const s = new Set(posts);
    inter = inter ? new Set([...inter].filter(x => s.has(x))) : s;
    if (!inter.size) return new Set();
  }
  if (needsVerify(term)) {
    const nt = normalize(term.toLowerCase());
    const cat = await loadCatalog();
    const keep = new Set();
    for (const id of inter) {
      const it = cat.items.find(x => x.i === id);
      if (it && normalize(it.t.toLowerCase()).includes(nt)) keep.add(id);
    }
    return keep;
  }
  return inter;
}

/* 标题检索，与 searchBool 同语义（all AND / any OR / none NOT），
   作用在标题上。返回 [{i,t,s,n,p,d}]，含未转录卷宗（d=0）。 */
async function titleSearch({ all = [], any = [], none = [] }) {
  let cur = null;
  for (const t of all) {
    const s = await titleSet(t);
    cur = cur ? new Set([...cur].filter(x => s.has(x))) : s;
    if (!cur.size) return [];
  }
  if (any.length) {
    const u = new Set();
    for (const t of any) for (const x of await titleSet(t)) u.add(x);
    cur = cur ? new Set([...cur].filter(x => u.has(x))) : u;
  }
  if (!cur) return [];
  for (const t of none) {
    const s = await titleSet(t);
    cur = new Set([...cur].filter(x => !s.has(x)));
  }
  const cat = await loadCatalog();
  const byId = new Map(cat.items.map(x => [x.i, x]));
  return [...cur].map(id => byId.get(id)).filter(Boolean)
    .sort((a, b) => (b.d - a.d) || (b.p - a.p));
}

/* 全文检索：中文走「归一化 bigram」，与 build_site.py 的分词必须完全一致。
   变体表在 variants.min.json（字符 → 归一字形），由构建脚本从简繁异体通搜插件的数据生成。 */
const SHARDS = 64;
let NORM = null, shardCache = {}, catalog = null;

async function loadNorm() {
  if (NORM) return NORM;
  const groups = await (await fetch('data/variants.min.json')).json();
  NORM = {};
  for (const g of groups) for (const c of g) NORM[c] = g[0];
  return NORM;
}
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
async function pageSet(q) {
  await loadNorm();
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
    if (!posts) return new Set();
    const set = new Set(posts.map(p => p[0] + '#' + p[1]));
    inter = inter ? new Set([...inter].filter(x => set.has(x))) : set;
    if (!inter.size) return new Set();
  }
  return inter;
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
async function searchBool({ all = [], any = [], none = [] }) {
  let cur = null;
  for (const t of all) {
    const s = await pageSet(t);
    cur = cur ? new Set([...cur].filter(x => s.has(x))) : s;
    if (!cur.size) return { hits: [], used: { all, any, none } };
  }
  if (any.length) {
    const u = new Set();
    for (const t of any) for (const x of await pageSet(t)) u.add(x);
    cur = cur ? new Set([...cur].filter(x => u.has(x))) : u;
  }
  if (!cur) return { hits: [], used: { all, any, none } };
  for (const t of none) {
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

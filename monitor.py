"""流水线监控：一眼看清「在跑吗、推进了吗、有没有出事」。

只读，不改任何东西。用法：
    .venv/bin/python monitor.py            # 看一次
    .venv/bin/python monitor.py --watch    # 每 5 分钟刷一次，一直盯着
    .venv/bin/python monitor.py --watch --every 600 --log /tmp/smp-monitor.log

为什么要有它：这个项目最贵的几次教训都不是「程序崩了」，而是
**程序活得好好的、进度数字也在涨，实际上在批量制造垃圾**——
限流时把整批标成失败照样「推进」，两条流水线并行照样「在跑」。
所以这里查的不是「活着没有」，而是下面这几条会出事的信号。

**2026-08-19 修：这个监控自己有过一次盲区，值得记下来。**
08-19 09:28–10:20 机器断网（DNS 解析失败），后果是：下一批预取图 0/457、
4 件共 347 页转录全失败、391 次翻译分块 URLError。而 monitor 当时报的是
**「无异常信号」**。原因是它的正则只认 `取图 N/M` 这一种写法，而日志里
实际写的是 `↓取图结束 0/457`；`URLError`、「翻译全失败」则根本没在查。
教训与项目里其他坑同源：**没被 grep 到 ≠ 没发生**。所以现在的规矩是
——正则必须照着日志里真实出现过的行式样写，别照着记忆写。
"""
import argparse, glob, json, os, re, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))
import demo_run as D

TOTAL_PAGES = 92769
LOG = "/tmp/smp-run.log"


def procs():
    out = subprocess.run(["ps", "-eo", "pid,ppid,etime,command"],
                         capture_output=True, text=True).stdout.splitlines()
    keep = [l for l in out if re.search(r"(run_chunked|fill_zh)\.py", l) and "grep" not in l]
    return keep


def scan():
    files = glob.glob("data/smpa-files-*.json")
    tot = en = zh = gap = unread = 0
    for f in files:
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            unread += 1           # 多半是正被写入，不是坏了
            continue
        for q in d.get("page_data", []):
            tot += 1
            if (q.get("en") or "").strip():
                en += 1
            if (q.get("zh") or "").strip():
                zh += 1
        gap += len(D._missing_zh(d))
    return dict(files=len(files), pages=tot, en=en, zh=zh, gap=gap, unread=unread)


def recent_writes(minutes=10):
    now = time.time()
    return sum(1 for f in glob.glob("data/*.json") if now - os.path.getmtime(f) < minutes * 60)


def read_log():
    if not os.path.exists(LOG):
        return None, None
    txt = open(LOG, encoding="utf-8", errors="replace").read()
    # 日志是跨轮次累加的，「整个日志里出现过 N 次超时」对判断此刻没什么用。
    # 所以切成两段：当前批（最后一个 [N/M] 批次头之后）单独看，整轮看累计。
    heads = list(re.finditer(r"^\[(\d+)/(\d+)\] \d\d:\d\d:\d\d ", txt, re.M))
    cur = txt[heads[-1].start():] if heads else txt
    return txt, cur


def signals(txt, cur):
    """从日志里找**会出事**的信号，而不是找报错。

    每条都对应一次真实踩过的坑；正则右边注的是日志里真实出现过的行。
    """
    sig = []

    def n(pat, s):
        return len(re.findall(pat, s))

    # ── 取图 ──────────────────────────────────────────────
    # `    10:20:22 ↓取图结束 0/457`
    # 这是 08-19 漏掉的那条。预取的是**下一批**的图，所以它失败意味着
    # run_chunked 跑完当前批、拿到这个结果时会判定限流并**主动停机**。
    rounds = re.findall(r"↓取图结束 (\d+)/(\d+)", txt)
    if rounds:
        ok, tot = int(rounds[-1][0]), int(rounds[-1][1])
        if tot and ok / tot < 0.5:
            sig.append(f"⛔ 最近一次预取图只成功 {ok}/{tot}——按设计，跑批**跑完当前批就会自动停机**。"
                       f"等网络恢复后重跑即可（已完成的自动跳过）")
        elif tot and ok < tot:
            sig.append(f"⚠ 最近一次预取图 {ok}/{tot}，差 {tot-ok} 页")
    # `[3/24] 取图 1888/1888`
    for m in re.finditer(r"取图 (\d+)/(\d+)", txt):
        ok, all_ = int(m.group(1)), int(m.group(2))
        if all_ and ok / all_ < 0.9:
            sig.append(f"⚠ 取图成功率 {ok}/{all_} = {ok/all_*100:.0f}%")
    if "判定为 archive.org 限流" in txt:
        sig.append("⛔ 已因限流自动停机——等一段时间后重跑")

    # ── 网络 ──────────────────────────────────────────────
    # `FILE: 第 11/26 块失败 {"message": "URLError: <urlopen error [Errno 8] ...>"}`
    # 08-19 全程 391 次，监控一次都没报。DNS 挂掉时长这样。
    n_url = n(r"URLError", txt)
    n_url_cur = n(r"URLError", cur)
    if n_url:
        dns = "（DNS 解析失败，多半是机器断网）" if "nodename nor servname" in txt else ""
        sig.append(f"⚠ 网络错误 URLError {n_url} 次（当前批 {n_url_cur}）{dns}")
    n_to = n(r"read operation timed out", txt)
    if n_to:
        sig.append(f"⚠ 接口超时 {n_to} 次")

    # ── 转录 ──────────────────────────────────────────────
    # `FILE: 0/67 页，失败 67，2091s，in=0 out=0`
    # 整件颗粒无收。in=0 out=0 说明请求根本没发出去（没烧钱），
    # 与「发出去了但内容被拒」是两回事，得分开报。
    dead = re.findall(r"^(\S+): 0/(\d+) 页，失败", cur, re.M)
    if dead:
        pg = sum(int(p) for _, p in dead)
        free = "，且 in=0 out=0 未产生费用" if "in=0 out=0" in cur else ""
        sig.append(f"⛔ 当前批有 {len(dead)} 件整件转录全失败，共 {pg} 页{free}"
                   f"（tries 未满会自动重试，别手动补）")
    # `FILE: 287/335 页，失败 48，...`
    part = [(f, int(a), int(b)) for f, a, b in
            re.findall(r"^(\S+): (\d+)/(\d+) 页，失败 [1-9]", cur, re.M)]
    part = [(f, a, b) for f, a, b in part if a]      # 全失败的上面已经报过
    if part:
        sig.append(f"· 当前批 {len(part)} 件部分页失败，共缺 {sum(b-a for _, a, b in part)} 页")

    # ── 翻译 ──────────────────────────────────────────────
    n_tf = n(r"翻译全失败", txt)
    if n_tf:
        sig.append(f"⚠ 「翻译全失败」{n_tf} 件——译文缺口会因此变大，收尾跑 fill_zh.py")
    # `FILE: 译文 100/133 页对齐（4 块） ⚠ 缺 33 页 1 块失败，58s`
    miss = [int(x) for x in re.findall(r"⚠ 缺 (\d+) 页", txt)]
    if miss:
        sig.append(f"· 译文对齐缺口 {sum(miss)} 页，分布在 {len(miss)} 件")
    n_deg = n(r"仍退化，留空", txt)
    if n_deg:
        sig.append(f"· 退化留空 {n_deg} 页")

    # ── 花钱 ──────────────────────────────────────────────
    # `⚠ reasoning=22943 思考没关掉！`
    # 已查清：全部来自备用端点，那个端点关不掉思考，主端点是好的。
    # 所以这条**不是故障**，只是账单上的一笔，报出来是为了别再被吓一跳。
    rs = [int(x) for x in re.findall(r"reasoning=(\d+) 思考没关掉", txt)]
    if rs:
        sig.append(f"· reasoning token {sum(rs):,}（{len(rs)} 件走了备用端点；"
                   f"该端点关不掉思考，主端点正常，非故障）")

    if "全部批次结束" in txt[-2000:] or "全部结束" in txt[-2000:]:
        sig.append("✔ 日志显示本轮已跑完")
    return sig or ["无异常信号"]


def once(prev=None):
    s = scan()
    ps_ = procs()
    n_chunked = sum(1 for l in ps_ if "run_chunked" in l)
    line = [f"{time.strftime('%m-%d %H:%M')}  "
            f"{s['files']:,} 件 / {s['pages']:,} 页 | 转录 {s['en']:,} "
            f"({s['en']/TOTAL_PAGES*100:.1f}%) | 译文 {s['zh']:,} | 译文缺口 {s['gap']}"]
    if prev:
        d_en, d_zh = s["en"] - prev["en"], s["zh"] - prev["zh"]
        line.append(f"  自上次：转录 {d_en:+,}  译文 {d_zh:+,}")
    line.append(f"  进程 {len(ps_)} 个；近 10 分钟写过 {recent_writes()} 个文件")
    # 这两条是「看着正常其实出事」的典型，必须显式查
    if n_chunked > 1:
        line.append(f"  ⛔ **有 {n_chunked} 个 run_chunked 同时在跑**——会互相覆盖 data/，需杀掉多余的")
    if not ps_:
        line.append("  ⏹ 没有流水线在跑")
    elif recent_writes() == 0:
        line.append("  ⚠ 进程在，但近 10 分钟无写入——可能卡在某个长请求上")
    if s["unread"]:
        line.append(f"  · {s['unread']} 件暂时读不了（多半正被写入）")
    txt, cur = read_log()
    if txt is None:
        line.append("  （无日志）")
    else:
        m = re.findall(r"^\[(\d+)/(\d+)\] \d\d:\d\d:\d\d (\d+) 件", txt, re.M)
        if m:
            line.append(f"  当前第 {m[-1][0]}/{m[-1][1]} 批")
        for x in signals(txt, cur):
            line.append(f"  {x}")
    return "\n".join(line), s


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--every", type=int, default=300, help="秒，默认 300")
    ap.add_argument("--log", help="同时追加到这个文件")
    a = ap.parse_args()
    prev = None
    while True:
        txt, prev = once(prev)
        print(txt, flush=True)
        if a.log:
            open(a.log, "a", encoding="utf-8").write(txt + "\n")
        if not a.watch:
            break
        print("-" * 70, flush=True)
        time.sleep(a.every)

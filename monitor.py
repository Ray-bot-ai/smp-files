"""流水线监控：一眼看清「在跑吗、推进了吗、有没有出事」。

只读，不改任何东西。用法：
    .venv/bin/python monitor.py            # 看一次
    .venv/bin/python monitor.py --watch    # 每 5 分钟刷一次，一直盯着
    .venv/bin/python monitor.py --watch --every 600 --log /tmp/smp-monitor.log

为什么要有它：这个项目最贵的几次教训都不是「程序崩了」，而是
**程序活得好好的、进度数字也在涨，实际上在批量制造垃圾**——
限流时把整批标成失败照样「推进」，两条流水线并行照样「在跑」。
所以这里查的不是「活着没有」，而是下面这几条会出事的信号。
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


def log_signals(tail=4000):
    """从日志尾部找**会出事**的信号，而不是找报错。"""
    if not os.path.exists(LOG):
        return ["（无日志）"]
    txt = open(LOG, encoding="utf-8", errors="replace").read()[-tail * 40:]
    sig = []
    # 取图成功率：整批大面积失败 = archive.org 限流，上次为此烧了 3,425 页
    for m in re.finditer(r"取图 (\d+)/(\d+)", txt):
        ok, all_ = int(m.group(1)), int(m.group(2))
        if all_ and ok / all_ < 0.9:
            sig.append(f"⚠ 取图成功率 {ok}/{all_} = {ok/all_*100:.0f}%")
    if "判定为 archive.org 限流" in txt:
        sig.append("⛔ 已因限流自动停机——等一段时间后重跑")
    n_to = len(re.findall(r"read operation timed out", txt))
    if n_to:
        sig.append(f"⚠ 接口超时 {n_to} 次")
    n_deg = len(re.findall(r"仍退化，留空", txt))
    if n_deg:
        sig.append(f"· 退化留空 {n_deg} 页")
    if "全部结束" in txt[-2000:]:
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
    for x in log_signals():
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

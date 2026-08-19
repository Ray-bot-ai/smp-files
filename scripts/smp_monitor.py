#!/usr/bin/env python3
"""SMP 跑批监控 v3（项目内持久版，scripts/）。
每 10 分钟采样：件数/转录页/译文页/阶段/run_chunked 实例数。
- 全部 try/except：读到半写 JSON 也不死（v1 死因）
- 实例数 >1 记 ⚠（并发同跑=重复烧钱+抢写）
- run_all.sh 消失 → 写终局行并退出（配 notify_on_complete 通知）
用法：python3 scripts/smp_monitor.py   （Hermes 后台跑，notify_on_complete=true）
"""
import subprocess
import json
import glob
import os
import sys
import time

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
LOG = "/tmp/smp-monitor.log"
INTERVAL = 600


def main():
    while True:
        time.sleep(INTERVAL)
        # run_all 还在吗？
        alive = False
        try:
            out = subprocess.run(["pgrep", "-f", "run_all\\.sh"],
                                 capture_output=True, text=True).stdout.strip()
            alive = bool(out)
        except Exception:
            pass
        if not alive:
            try:
                with open(LOG, "a", encoding="utf-8") as f:
                    f.write(f"{time.strftime('%m-%d %H:%M:%S')}  ⏹ 终局：run_all 已退出。"
                            f"files={nfiles}  转录={npages}  译文={nzh}\n")
            except Exception:
                pass
            return
        snap()


def snap():
    global nfiles, npages, nzh
    try:
        fs = glob.glob(os.path.join(DATA, "*.json"))
        nfiles = npages = nzh = bad = 0
        for p in fs:
            try:
                d = json.load(open(p, encoding="utf-8"))
            except Exception:
                bad += 1
                continue
            nfiles += 1
            npages += int(d.get("pages_done") or 0)
            nzh += int(d.get("zh_pages") or 0)
        nc = _count("run_chunked")
        phase = "补译缺口(fill_zh)" if _count("fill_zh") else "全量跑批(run_chunked)"
        warn = "  ⚠ 实例数>1!" if nc > 1 else ""
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%m-%d %H:%M:%S')}  files={nfiles}(可读{nfiles})  "
                    f"转录={npages}  译文={nzh}  阶段={phase}{warn}\n")
    except Exception as e:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%m-%d %H:%M:%S')}  ⚠ 采样异常: {type(e).__name__}: {e}\n")


def _count(pat):
    try:
        out = subprocess.run(["pgrep", "-fc", pat], capture_output=True, text=True).stdout.strip()
        return int(out) if out.isdigit() else 0
    except Exception:
        return 0


nfiles = npages = nzh = 0
with open(LOG, "a", encoding="utf-8") as f:
    f.write(f"=== 监控 v3 重启 {time.strftime('%m-%d %H:%M:%S')}（每 10 分钟采样，容错版）===\n")
main()

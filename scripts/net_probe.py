#!/usr/bin/env python3
"""本地网络探针：每 10 秒一次 DNS 解析 + 首字节延迟，
只把异常（解析>0.5s、失败、首字节>3s）记入 /tmp/net-probe.log。
目的是在故障发生的当下留下证据（事后测量无法回溯）。"""
import socket
import time
import urllib.error
import urllib.request

LOG = "/tmp/net-probe.log"
HOST = "dashscope.aliyuncs.com"

with open(LOG, "a", encoding="utf-8") as f:
    f.write(f"=== 网络探针启动 {time.strftime('%m-%d %H:%M:%S')}（10s/次，只记异常）===\n")

while True:
    t = time.strftime("%m-%d %H:%M:%S")
    try:
        t0 = time.time()
        socket.gethostbyname(HOST)
        dt = time.time() - t0
        if dt > 0.5:
            with open(LOG, "a", encoding="utf-8") as f:
                f.write(f"{t} ⚠ DNS 迟滞 {dt:.2f}s\n")
    except Exception as e:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{t} ⚠ DNS 失败: {e}\n")
    try:
        t0 = time.time()
        try:
            urllib.request.urlopen(f"https://{HOST}/compatible-mode/v1/models",
                                   timeout=10).read(50)
        except urllib.error.HTTPError:
            pass  # 401 等 = 连接本身正常、首字节已收到，只量延迟
        dt = time.time() - t0
        if dt > 3:
            with open(LOG, "a", encoding="utf-8") as f:
                f.write(f"{t} ⚠ 首字节迟滞 {dt:.2f}s\n")
    except Exception as e:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{t} ⚠ 连接失败: {type(e).__name__}\n")
    time.sleep(10)

"""测 OSS 上传吞吐：单线程 81KB/s 的话 9 万张要 121 小时，必须知道加并发能到多少。
顺便修掉 oss_check 里 HEAD/GET 签名不匹配的误报。
用法：.venv/bin/python oss_bench.py
"""
import concurrent.futures as cf
import glob, os, subprocess, sys, time

import oss2
from oss_check import bucket, HERE  # 复用已验证的连接

imgs = sorted(glob.glob(os.path.join(HERE, "images", "*.jpg")))
if len(imgs) < 8:
    sys.exit("本地样图不足 8 张，先跑 fetch_images 取一些")
print(f"样本 {len(imgs)} 张，共 {sum(os.path.getsize(f) for f in imgs)/2**20:.1f} MB\n")


def up(args):
    i, f = args
    bucket.put_object_from_file(f"bench/{i}_{os.path.basename(f)}", f)
    return os.path.getsize(f)


print("并发   耗时     吞吐        9万张(33.8GB)预计")
results = {}
for p in (1, 4, 8, 16, 32):
    tasks = [(f"p{p}_{i}", f) for i, f in enumerate(imgs)]
    t0 = time.time()
    with cf.ThreadPoolExecutor(p) as ex:
        total = sum(ex.map(up, tasks))
    dt = time.time() - t0
    mbps = total / 2**20 / dt
    hours = 33.8 * 1024 / mbps / 3600
    results[p] = mbps
    print(f"{p:>3}   {dt:5.1f}s   {mbps:6.2f} MB/s   {hours:5.1f} 小时")

best = max(results, key=results.get)
print(f"\n最佳并发 {best}：{results[best]:.2f} MB/s → 全量约 {33.8*1024/results[best]/3600:.1f} 小时")

# 修正版签名 URL 检查：必须用 GET，不能用 HEAD（签名绑方法）
print("\n=== 签名 URL 复检（这次用 GET）===")
key = f"bench/p1_0_{os.path.basename(imgs[0])}"
url = bucket.sign_url("GET", key, 7 * 24 * 3600, slash_safe=True)
out = subprocess.run(["curl", "-s", "-o", "/dev/null", "-D", "-", "--max-time", "30", url],
                     capture_output=True, text=True).stdout
for line in out.splitlines():
    if line.startswith("HTTP") or line.lower().startswith(("content-length", "content-type")):
        print("  " + line.strip())

print("\n=== 清理 ===")
n = 0
for obj in oss2.ObjectIterator(bucket, prefix="bench/"):
    bucket.delete_object(obj.key)
    n += 1
print(f"  删除 {n} 个测试对象")

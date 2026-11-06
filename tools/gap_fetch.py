"""稳妥地取回 MinerU 补空当的结果，取齐后自动合并。替代反复 resume。

  python3 tools/gap_fetch.py <batch_id> [<batch_id> ...]

为什么不用 mineru_api.py resume：它的下载超时只有 180 秒，几十 MB 的结果包常在中途断，
留下残缺 zip → 解压抛 BadZipFile → 整批中止；而 resume 又会把已取回的包全部重下一遍。
这里：借用该脚本的 load_token / json_request 查批次状态（不打印 token），
只下还缺的包，下载后先验 zip 完整再解压，失败单包重试，不拖垮整批。
全部取齐 → fill_gaps.py merge → build_roll_map.py，打印 FETCH-DONE；出错打印 FETCH-ERROR。
"""
import importlib.util, json, os, re, subprocess, sys, time, zipfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GAPD = os.path.join(HERE, ".roll_frames", "_gaps")
OUT = os.path.join(GAPD, "mineru")
LOG = os.path.join(HERE, "roll_walk", "gap_fetch.log")
SKILL = os.path.expanduser("~/.claude/skills/mineru1012/scripts/mineru_api.py")

spec = importlib.util.spec_from_file_location("mineru_api", SKILL)
M = importlib.util.module_from_spec(spec)
sys.modules["mineru_api"] = M          # 该脚本用了 dataclass，须先登记模块才能执行
spec.loader.exec_module(M)


def say(msg):
    line = time.strftime("%H:%M:%S ") + msg
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def have():
    got = set()
    for d in os.listdir(OUT) if os.path.isdir(OUT) else []:
        m = re.match(r"(roll\d+_gap\d+)", d)
        if m and d.endswith(".mineru") and any(x.endswith("_content_list.json") and "_v2" not in x
                                                for x in os.listdir(os.path.join(OUT, d))):
            got.add(m.group(1))
    return got


def fetch_one(url, stem):
    tmp = os.path.join(OUT, f"{stem}.part.zip")
    for t in range(6):
        r = subprocess.run(["curl", "-sSL", "--retry", "3", "--connect-timeout", "30", "--max-time", "1800",
                            "-o", tmp, url], capture_output=True, text=True)
        if r.returncode == 0 and zipfile.is_zipfile(tmp):
            try:
                with zipfile.ZipFile(tmp) as z:
                    if z.testzip() is None:
                        dest = os.path.join(OUT, f"{stem}.mineru")
                        n = 2
                        while os.path.exists(dest):
                            dest = os.path.join(OUT, f"{stem}-{n}.mineru"); n += 1
                        M.safe_extract(__import__("pathlib").Path(tmp), __import__("pathlib").Path(dest))
                        os.remove(tmp)
                        return True
            except zipfile.BadZipFile:
                pass
        say(f"  retry {stem} ({t + 1}): curl exit {r.returncode} {r.stderr.strip()[:120]}")
        time.sleep(10 * (t + 1))
    return False


def main():
    batches = sys.argv[1:]
    plan = json.load(open(os.path.join(GAPD, "plan.json")))
    token = M.load_token()
    t0 = time.time()
    while time.time() - t0 < 4 * 3600:
        need = set(plan) - have()
        say(f"missing {len(need)}")
        if not need:
            break
        pending = 0
        for b in batches:
            res = M.json_request("GET", f"extract-results/batch/{b}", token).get("data", {}).get("extract_result", [])
            for it in res:
                stem = it.get("file_name", "").rsplit(".pdf", 1)[0]
                if stem not in need:
                    continue
                st = it.get("state")
                if st == "done" and it.get("full_zip_url"):
                    ok = fetch_one(it["full_zip_url"], stem)
                    say(f"{'got' if ok else 'FETCH-ERROR could not get'} {stem}")
                elif st == "failed":
                    say(f"FETCH-ERROR {stem} failed on MinerU: {it.get('err_msg', '')[:200]}")
                else:
                    pending += 1
        if set(plan) - have() and pending:
            time.sleep(60)
        elif set(plan) - have():
            say("FETCH-ERROR some results can neither be fetched nor are pending")
            break
    left = set(plan) - have()
    if left:
        say(f"FETCH-ERROR still missing {len(left)}: {sorted(left)[:10]}")
    for cmd in ([sys.executable, os.path.join(HERE, "tools", "fill_gaps.py"), "merge"],
                [os.path.join(HERE, ".venv", "bin", "python"), os.path.join(HERE, "build_roll_map.py")]):
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=HERE,
                           env={k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")})
        for line in r.stdout.strip().splitlines()[-4:]:
            say(f"{os.path.basename(cmd[1])}: {line[:300]}")
        if r.returncode != 0:
            say(f"FETCH-ERROR {os.path.basename(cmd[1])}: {r.stderr.strip()[-400:]}")
            return 1
    say("FETCH-DONE" + (f" (with {len(left)} missing)" if left else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())

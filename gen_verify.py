"""生成抽样校验任务单：优先给「自洽性检查标记过的页」，再补随机页做无偏基线。

为什么要两种混着抽：
- 只抽标记页 → 错误率被高估（标记本来就是往可疑处指的）
- 只抽随机页 → 大部分页没问题，翻半天看不到几个错，效率极低
两者分开统计，才能同时得到「整体错误率」和「标记的准确率」。

用法：.venv/bin/python gen_verify.py [每类抽多少页，默认 25]
"""
import glob, json, os, random, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import consistency

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "docs", "data", "verify.json")


def main(n=25, seed=20260811):
    random.seed(seed)
    flagged, plain = [], []
    for f in sorted(glob.glob(os.path.join(HERE, "data", "*.json"))):
        d = json.load(open(f, encoding="utf-8"))
        short = d["ia_id"].replace("smpa-files-", "")
        issues = consistency.check(d)
        # 每页对应哪些标记
        by_page = {}
        for kind, detail, ps in issues:
            if kind == "转录过短":
                continue
            for p in ps:
                by_page.setdefault(p, []).append(f"{kind}：{detail}")
        for q in d.get("page_data", []):
            en = (q.get("en") or "").strip()
            if len(en) < 80:            # 空白页/封面不值得人看
                continue
            item = {"doc": short, "n": q["n"], "title": d.get("title", ""),
                    "en": en, "zh": (q.get("zh") or "").strip(),
                    "flags": by_page.get(q["n"], [])}
            (flagged if item["flags"] else plain).append(item)

    random.shuffle(flagged); random.shuffle(plain)
    tasks = ([{**x, "group": "flagged"} for x in flagged[:n]] +
             [{**x, "group": "random"} for x in plain[:n]])
    random.shuffle(tasks)               # 打乱顺序，避免看的时候产生预期偏差
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump({"tasks": tasks, "n_flagged": min(n, len(flagged)),
               "n_random": min(n, len(plain)),
               "pool_flagged": len(flagged), "pool_plain": len(plain)},
              open(OUT, "w", encoding="utf-8"), ensure_ascii=False,
              separators=(",", ":"))
    print(f"任务单 {len(tasks)} 页 → {OUT}")
    print(f"  被标记页 {min(n,len(flagged))}/{len(flagged)}，随机页 {min(n,len(plain))}/{len(plain)}")
    print("  顺序已打乱，校验时看不出哪些是被标记的（避免预期偏差）")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 25)

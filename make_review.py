"""生成人工复核队列 docs/data/review.json。

挑出**真正需要人看一眼**的页，而不是全库 7 万页。四类，按痛感排序：
  1 退化留空   模型反复吐重复内容，逐页单译也救不回来——每轮还要重烧十几次接口
  2 缺译文     有英文没译文，且不是中文原件
  3 已判不可用 自动判据标出来的，人复核一下是不是误判
  4 影像可疑   成像指标离群（imgqual），常是「模型编造」那一类的温床

人在工作台上判「放弃」的页会进 unusable_pages.json，
站点据此标注、流水线据此跳过——两头都认这一份名单。

用法：.venv/bin/python make_review.py
"""
import glob, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import demo_run as D
import build_site as B

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "docs", "data", "review.json")


def main():
    manual = B.load_manual_flags()
    qual = {}
    qp = os.path.join(HERE, "imgqual.json")
    if os.path.exists(qp):
        qual = json.load(open(qp, encoding="utf-8"))
    # 影像离群分：与 triage 同一套「双向离群」口径
    med = mad = None
    if qual:
        import statistics as st
        keys = ("contrast", "sharp", "ink", "mean", "blown")
        med = {k: st.median([v[k] for v in qual.values()]) for k in keys}
        mad = {k: max(st.median([abs(v[k] - med[k]) for v in qual.values()]), 1e-6) for k in keys}

    items = []
    for f in sorted(glob.glob(os.path.join(D.DATA, "*.json"))):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue                      # 跑批正在写这一份，下次再说
        iid = d["ia_id"]
        short = iid.replace("smpa-files-", "")
        gaps = set(D._missing_zh(d))
        for q in d.get("page_data", []):
            en = (q.get("en") or "").strip()
            zh = (q.get("zh") or "").strip()
            n = q["n"]
            why = None
            if q.get("zh_status") and not zh:
                why = "译文退化留空：" + str(q["zh_status"])
            elif n in gaps:
                why = "缺译文"
            elif B.damage_flag(iid, n, en, manual):
                why = "已判不可用（复核）"
            elif med and en:
                m = qual.get(f"{iid}#{n}")
                if m:
                    score = max(abs(m[k] - med[k]) / mad[k] for k in med)
                    if score >= 6:
                        why = f"影像离群 {score:.0f}"
            if why:
                items.append({"d": short, "n": n, "why": why,
                              "len": len(en), "box": en.count("□")})
    # 最痛的排前面
    order = {"译文退化": 0, "缺译文": 1, "已判不可用": 2, "影像离群": 3}
    items.sort(key=lambda x: (next((v for k, v in order.items() if x["why"].startswith(k)), 9),
                              -x["box"]))
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump({"items": items}, open(OUT, "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))
    from collections import Counter
    c = Counter(i["why"].split("：")[0].split(" ")[0] for i in items)
    print(f"待复核 {len(items)} 页 → docs/data/review.json")
    for k, v in c.most_common():
        print(f"   {k}: {v}")


if __name__ == "__main__":
    main()

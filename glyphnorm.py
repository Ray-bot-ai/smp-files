"""汉字字形归一化：把简体/繁体/异体/两岸三地字形差异都归到同一个代表字。

两个数据源合并（必须用并查集，不能简单覆盖）：
1. `variants.json` —— 用户自建 Obsidian 插件「简繁异体通搜」的字形数据，6,963 字头，
   由 cjkvi-tables 的简繁对照/简化字总表/异体字整理表/日本新旧字体四表并查集合并。
2. `glyph_cross_strait.json` —— github.com/cdtym/glyph_comparison《对比汇总表·比较差异表》，
   系统比对内地《通用规范汉字表》/香港《常用字字形表》/台湾《常用国字标准字体表》
   在 Unicode 上的 194 组差异。
   **实测源 1 只覆盖其中 122 组，缺 72 组**（吳/吴、呂/吕、戶/户、沒/没、說/説、
   兌/兑、換/换…全是常用字），因为那四张表按「简化字/异体字」分类，
   不覆盖「同一个字在不同地区标准下的字形差异」这一维。

**为什么必须并查集**：两个源的组会交叠。cjkvi 有「说說」，两岸表有「説說」，
共享「說」——本该并成一组 {说,説,說}。若按组依次覆盖 NORM[c]=g[0]，
后写的会把前面的拆散：说→说 而 說→説，于是搜「说」命不中「說」。
实测踩过这个 bug。
"""
import json, os

HERE = os.path.dirname(os.path.abspath(__file__))


def _build():
    groups = []
    with open(os.path.join(HERE, "variants.json"), encoding="utf-8") as f:
        groups += list(json.load(f).values())
    p = os.path.join(HERE, "glyph_cross_strait.json")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            for t in json.load(f):
                g = "".join(dict.fromkeys(x for x in t if x))
                if len(g) > 1:
                    groups.append(g)
    groups += ["么麽", "为爲為", "别別", "沉沈", "群羣"]

    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)     # 取码位小的当代表，结果稳定

    for g in groups:
        for c in g[1:]:
            union(g[0], c)

    norm = {c: find(c) for c in parent}
    cls = {}
    for c, r in norm.items():
        cls.setdefault(r, set()).add(c)
    return norm, ["".join(sorted(v)) for v in cls.values() if len(v) > 1]


NORM, GROUPS = _build()


def normalize(s):
    return "".join(NORM.get(c, c) for c in s)


if __name__ == "__main__":
    print(f"等价类 {len(GROUPS):,} 组，覆盖 {len(NORM):,} 字")
    for a, b in [("吴", "吳"), ("吕", "呂"), ("户", "戶"), ("没", "沒"), ("说", "説"),
                 ("说", "說"), ("换", "換"), ("顾", "顧"), ("国", "國"), ("发", "髮")]:
        ok = "✓" if normalize(a) == normalize(b) else "✗"
        print(f"  {a}/{b}: {ok}")

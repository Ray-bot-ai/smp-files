"""按 corpus_v1.json 取图并传 OSS（复用 pipeline 的 worker，只是换了目标集合）。"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pipeline
ids = set(json.load(open("corpus_v1.json")))
_orig = pipeline.all_pages
pipeline.all_pages = lambda: [(i, n) for i, n in _orig() if i in ids]
if __name__ == "__main__":
    pipeline.run(24, 32, 0, False, True)

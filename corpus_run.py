"""全量跑 corpus_v1.json 里的卷宗（转录 + 翻译）。复用 demo_run 的逻辑，只换目标集合。
可断点续传：已完成的件自动跳过。
用法：.venv/bin/python corpus_run.py [ocr|translate|all]
"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import demo_run

demo_run.DEMO = ["smpa-files-" + i if not str(i).startswith("smpa") else i
                 for i in json.load(open("corpus_v1.json"))]

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    if cmd in ("ocr", "all"):
        demo_run.run_ocr(workers=12)
    if cmd in ("translate", "all"):
        demo_run.run_translate()
    demo_run.stat()

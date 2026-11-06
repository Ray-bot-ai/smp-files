"""本机 Apple Vision OCR（英文打字稿），供 locate_frames.py 沿卷定位用。须用 ~/.ocr-vision-venv/bin/python。
实测（Roll 23 前 200 帧）：旧复写件上明显弱于 MinerU（稳定匹配 111 帧对 177），只作第一轮，空当由 MinerU 补。
"""
import sys, os, glob, json, time
import Vision, Quartz
from Foundation import NSURL


def vision_text(img_path):
    url = NSURL.fileURLWithPath_(img_path)
    src = Quartz.CGImageSourceCreateWithURL(url, None)
    if src is None:
        return ""
    cg = Quartz.CGImageSourceCreateImageAtIndex(src, 0, None)
    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    req.setUsesLanguageCorrection_(True)
    req.setRecognitionLanguages_(["en-US"])
    h = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg, None)
    ok = h.performRequests_error_([req], None)
    if not ok or req.results() is None:
        return ""
    obs = sorted(req.results(), key=lambda o: (-o.boundingBox().origin.y, o.boundingBox().origin.x))
    return "\n".join(o.topCandidates_(1)[0].string() for o in obs if o.topCandidates_(1))


if __name__ == "__main__":
    d, out = sys.argv[1:3]
    files = sorted(glob.glob(os.path.join(d, "n*.jpg")))
    t0 = time.time()
    res = [{"page_idx": k, "type": "text", "text": vision_text(f)} for k, f in enumerate(files)]
    json.dump(res, open(out, "w"), ensure_ascii=False)
    print(f"{len(files)} pages in {time.time()-t0:.1f}s")

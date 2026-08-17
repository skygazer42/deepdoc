#!/usr/bin/env python3
"""回归：页间并发 OCR（OCR_PARALLEL=4）vs 串行（=1）输出一致性 + 耗时对比。

用法: python script/compare_ocr.py [页数]
对比 __images__ 后 self.boxes 逐页逐框（text 精确、坐标容差 3px）。
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
ROOT = Path("/app") if Path("/app").exists() else Path("/data/temp4/deepdoc")

os.environ["OCR_ENGINE"] = "rapidocr"
os.environ["LAYOUT_MODEL"] = "doclayout"
os.environ["LAYOUT_MODEL_SIZE"] = "768"
os.environ["TABLE_ENGINE"] = "slanet"
# 本脚本只比较 OCR 并发，必须禁用原生文本页短路。
os.environ["NATIVE_TEXT_MODE"] = "off"

PDF = ROOT / "regression" / "documents" / "resnet.pdf"
N_PAGES = int(sys.argv[1]) if len(sys.argv) > 1 else 3


def run(parallel):
    os.environ["OCR_PARALLEL"] = str(parallel)
    from parser.pdf_parser import RAGFlowPdfParser
    p = RAGFlowPdfParser()
    t0 = time.time()
    p.__images__(str(PDF), zoomin=3)
    dt = time.time() - t0
    out = []
    for bxs in p.boxes[:N_PAGES]:
        page = []
        for b in bxs:
            page.append((b["text"], round(b["x0"], 1), round(b["x1"], 1),
                         round(b["top"], 1), round(b["bottom"], 1)))
        out.append(page)
    return out, dt


def main():
    d1, t1 = run(1)
    d4, t4 = run(4)

    n_pages = min(len(d1), len(d4))
    n_diff_pages = 0
    n_box = n_diff_box = n_text_diff = 0
    max_xy = 0.0
    for pi in range(n_pages):
        b1, b4 = d1[pi], d4[pi]
        if len(b1) != len(b4):
            n_diff_pages += 1
            print(f"  p{pi+1}: 框数 {len(b1)} vs {len(b4)} 不一致")
            continue
        for a, b in zip(b1, b4):
            n_box += 1
            if a[0] != b[0]:
                n_text_diff += 1
            max_xy = max(max_xy, max(abs(x - y) for x, y in zip(a[1:], b[1:])))
        if len(b1) != len(b4):
            n_diff_box += 1

    ok = (n_diff_pages == 0 and n_text_diff == 0 and max_xy <= 3.0)
    print(f"[resnet 前 {n_pages} 页] 串行 {t1:.0f}s vs 并发4 {t4:.0f}s "
          f"(加速 {t1/max(t4,1e-9):.2f}x)")
    print(f"  框数 {n_box} | 页不一致 {n_diff_pages} | 文本差异 {n_text_diff} "
          f"| 坐标最大偏差 {max_xy:.1f}px | 一致={ok}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""端到端 ResNet 解析：OCR_PARALLEL=1 vs 4 总耗时对比。

用法: python script/e2e_ocr.py
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

PDF = ROOT / "regression" / "documents" / "resnet.pdf"


def run(parallel):
    os.environ["OCR_PARALLEL"] = str(parallel)
    from parser.pdf_parser import RAGFlowPdfParser
    p = RAGFlowPdfParser()
    t0 = time.time()
    text_blocks, tbls = p(str(PDF), need_image=False)
    dt = time.time() - t0
    full_text = "".join(p.remove_tag(t) for t in text_blocks)
    n_tbl = sum(1 for x in tbls if isinstance(x[1], str))
    n_fig = sum(1 for x in tbls if not isinstance(x[1], str))
    print(f"OCR_PARALLEL={parallel}: 端到端 {dt:.0f}s ({dt/12:.1f}s/页) | "
          f"tables={n_tbl} figures={n_fig} text={len(full_text)}")
    return full_text, dt


if __name__ == "__main__":
    t1, dt1 = run(1)
    t4, dt4 = run(4)
    print(f"\n加速比: {dt1/max(dt4,1e-9):.2f}x")
    # 正文一致性（全文文本应一致）
    import difflib
    if t1 == t4:
        print("正文文本完全一致 ✓")
    else:
        # 找第一个差异位置
        for i, (a, b) in enumerate(zip(t1, t4)):
            if a != b:
                print(f"正文首个差异 @ {i}: ...{t1[max(0,i-20):i+20]!r}... vs ...{t4[max(0,i-20):i+20]!r}...")
                break
        else:
            print(f"正文前缀一致，长度 {len(t1)} vs {len(t4)}")

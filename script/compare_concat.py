#!/usr/bin/env python3
"""回归测试：批量预扫描重构后的 _concat_downward vs 原版逐条 predict。

对同一 PDF 的同一 boxes 快照分别跑原版与新版，逐字段对比 self.boxes 输出，
并报告耗时与加速比。用法: python script/compare_concat.py pdf1 pdf2 ...
"""
import copy
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))
ROOT = Path("/app") if Path("/app").exists() else Path("/data/temp4/deepdoc")

os.environ["OCR_ENGINE"] = "rapidocr"
os.environ["LAYOUT_MODEL"] = "doclayout"
os.environ["LAYOUT_MODEL_SIZE"] = "768"
os.environ["TABLE_ENGINE"] = "slanet"

from verify_precollect import run_original_concat  # noqa: E402  复用原版逻辑
from parser.pdf_parser import RAGFlowPdfParser, Recognizer  # noqa: E402

KEYS = ["text", "x0", "x1", "top", "bottom", "page_number", "layout_type"]


def merge_blocks(blocks):
    """原版 _concat_downward 的 blocks -> boxes 合并逻辑（721-744 行）。"""
    boxes = []
    for b in blocks:
        if len(b) == 1:
            boxes.append(b[0])
            continue
        t = b[0]
        for c in b[1:]:
            t["text"] = t["text"].strip()
            c["text"] = c["text"].strip()
            if not c["text"]:
                continue
            if t["text"] and re.match(
                    r"[0-9\.a-zA-Z]+$", t["text"][-1] + c["text"][-1]):
                t["text"] += " "
            t["text"] += c["text"]
            t["x0"] = min(t["x0"], c["x0"])
            t["x1"] = max(t["x1"], c["x1"])
            t["page_number"] = min(t["page_number"], c["page_number"])
            t["bottom"] = c["bottom"]
            if not t["layout_type"] and c["layout_type"]:
                t["layout_type"] = c["layout_type"]
        boxes.append(t)
    return Recognizer.sort_Y_firstly(boxes, 0)


def run(pdf_path):
    parser = RAGFlowPdfParser()
    t0 = time.time()
    zoomin = parser.__images__(str(pdf_path), zoomin=3)
    parser._layouts_rec(zoomin)
    parser._table_transformer_job(zoomin)
    parser._text_merge()
    parse_t = time.time() - t0
    snap = copy.deepcopy(parser.boxes)

    # ---- 原版（逐条 predict + 独立复制的合并逻辑）----
    t0 = time.time()
    G, blocks = run_original_concat(parser, copy.deepcopy(snap))
    R_orig = merge_blocks(blocks)
    t_orig = time.time() - t0

    # ---- 新版（批量预扫描 + 查表）----
    parser.boxes = copy.deepcopy(snap)
    t0 = time.time()
    parser._concat_downward()
    R_new = parser.boxes
    t_new = time.time() - t0

    # ---- 逐字段对比 ----
    n_orig, n_new = len(R_orig), len(R_new)
    ok = n_orig == n_new
    diffs = 0
    if ok:
        for a, b in zip(R_orig, R_new):
            for k in KEYS:
                va, vb = a.get(k), b.get(k)
                if k == "text":
                    va, vb = (va or "").strip(), (vb or "").strip()
                if va != vb:
                    ok = False
                    diffs += 1
                    if diffs <= 5:
                        print(f"    [差异][{k}] orig={va!r} new={vb!r}")
    else:
        print(f"    [差异] 长度 {n_orig} vs {n_new}")

    print(f"[{Path(pdf_path).name}] boxes={len(snap)} G={len(G)}对 | "
          f"原版 {t_orig:.1f}s 新版 {t_new:.2f}s 加速 {t_orig/max(t_new,1e-9):.1f}x | "
          f"一致={ok} (解析 {parse_t:.0f}s)")
    return ok


if __name__ == "__main__":
    names = sys.argv[1:] or ["scan_2p.pdf", "table_ruled.pdf",
                             "mixed_3p.pdf", "editable_twocol.pdf", "resnet.pdf"]
    all_ok = True
    for name in names:
        p = ROOT / "regression" / "documents" / name
        if not p.exists():
            print(f"[跳过] {name} 不存在")
            continue
        all_ok &= run(p)
    print("\n" + ("全部一致 ✓" if all_ok else "存在差异 ✗"))

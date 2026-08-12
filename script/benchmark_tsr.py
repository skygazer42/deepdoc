#!/usr/bin/env python3
"""
TSR（表格结构识别）基准对比：当前 YOLO tsr.onnx vs SLANet-plus

关键对比维度：
1. 推理速度 (ms/page)
2. 表格结构质量（cell 数量、行列是否正确、合并单元格处理）
3. 模型大小
"""
import sys
import time
from collections import Counter
from pathlib import Path

import cv2
import fitz
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from vision.table_structure_recognizer import TableStructureRecognizer as CurrentTSR
from rapid_table import RapidTable, RapidTableInput, ModelType, EngineType

TABLE_PAGES = [
    ("table_ruled.pdf", 0, "有框线表格（5 列 × 10 行）"),
    ("table_ruled.pdf", 1, "有框线表格 - 第二页（5 列）"),
    ("table_borderless.pdf", 0, "无框线表格"),
    ("table_crosspage.pdf", 0, "跨页表格 - 首页"),
    ("table_crosspage.pdf", 1, "跨页表格 - 续页"),
    ("table_merged.pdf", 0, "合并单元格表格"),
]


def render_page(pdf_name, pi):
    path = str(Path("/data/temp4/deepdoc/regression/documents") / pdf_name)
    doc = fitz.open(path)
    pix = doc[pi].get_pixmap(matrix=fitz.Matrix(3, 3))
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR if pix.n == 3 else cv2.COLOR_RGBA2BGR)
    doc.close()
    return img


def run_current(engine, img):
    """当前 YOLO TSR: 返回 (ms, label_counts, tbl_rows_cols_estimate)"""
    t0 = time.time()
    results = engine([img], thr=0.2)
    dt = (time.time() - t0) * 1000
    stats = {"n_tables": len(results)}
    for tbi, tbl in enumerate(results):
        cnt = Counter(b["label"] for b in tbl)
        # 从检测框推断行列数：row=多少行级框, col=多少列级框
        n_rows = cnt.get("table row", 0) + cnt.get("table projected row header", 0)
        n_cols = cnt.get("table column", 0) + cnt.get("table column header", 0)
        n_span = cnt.get("table spanning cell", 0)
        stats[f"t{tbi}_rows"] = n_rows
        stats[f"t{tbi}_cols"] = n_cols
        stats[f"t{tbi}_spans"] = n_span
    return dt, stats


def run_slanet(engine, img):
    """SLANet-plus: 返回 (ms, cell_bboxes, logic_points)"""
    t0 = time.time()
    result = engine(img)
    dt = (time.time() - t0) * 1000
    stats = {"n_cells": len(result.cell_bboxes[0]) if result.cell_bboxes else 0}
    if result.logic_points and len(result.logic_points[0]) > 0:
        lp = result.logic_points[0]
        stats["rows"] = int(lp[:, 1].max()) + 1
        stats["cols"] = int(lp[:, 3].max()) + 1
        # 检测合并单元格：start!=end 则存在 span
        row_spans = np.sum(lp[:, 0] != lp[:, 1])
        col_spans = np.sum(lp[:, 2] != lp[:, 3])
        stats["row_spans"] = int(row_spans)
        stats["col_spans"] = int(col_spans)
    return dt, stats


def main():
    print("初始化 SLANet-plus ...", end=" ", flush=True)
    t0 = time.time()
    slanet = RapidTable(RapidTableInput(
        model_type=ModelType.SLANETPLUS, engine_type=EngineType.ONNXRUNTIME, use_ocr=False,
    ))
    print(f"完成 ({time.time()-t0:.1f}s)")

    print("初始化当前 TSR (YOLO) ...", end=" ", flush=True)
    t0 = time.time()
    current = CurrentTSR()
    print(f"完成 ({time.time()-t0:.1f}s)")

    print(f"\n{'='*130}")
    print(f"{'PDF/页面':<28} {'描述':<22} {'当前TSR':>8} {'SLANet+':>8} {'速度比':>7}  {'当前TSR结构':<30}  {'SLANet+ 结构':<30}")
    print(f"{'='*130}")

    cur_times, sl_times = [], []
    for pdf_name, pi, desc in TABLE_PAGES:
        img = render_page(pdf_name, pi)

        dt_cur, sc = run_current(current, img)
        dt_sl, ss = run_slanet(slanet, img)

        cur_times.append(dt_cur)
        sl_times.append(dt_sl)
        ratio = dt_cur / max(dt_sl, 0.01)

        cur_str = f"{sc.get('n_tables',0)}tbl {sc.get('t0_rows','?')}r×{sc.get('t0_cols','?')}c {sc.get('t0_spans',0)}spans"
        sl_str = f"{ss.get('n_cells','?')}cells {ss.get('rows','?')}r×{ss.get('cols','?')}c {ss.get('row_spans',0)+ss.get('col_spans',0)}spans"

        label = f"{pdf_name}/p{pi+1}"
        print(f"  {label:<26} {desc:<22} {dt_cur:6.0f}ms {dt_sl:6.0f}ms {ratio:5.1f}x  {cur_str:<30}  {sl_str:<30}")

    print(f"{'='*130}")
    avg_c = np.mean(cur_times)
    avg_s = np.mean(sl_times)
    print(f"  {'平均':<26} {'':<22} {avg_c:6.0f}ms {avg_s:6.0f}ms {avg_c/max(avg_s,0.01):5.1f}x")
    print(f"\n模型大小: 当前 tsr.onnx = 12.2MB | SLANet-plus = 7.4MB (ONNX)")

    # 质量说明
    print(f"""
关键差异：
  SLANet-plus 直接输出每单元格坐标 + 逻辑结构（行列合并），无需 YOLO 后处理。
  当前 TSR: YOLO检测6类（table/column/row/header/spanningcell）→ 繁杂 NP 后处理拼表格。
  SLANet-plus: 端到端 CNN+Transformer → 直接 (startRow,endRow,startCol,endCol)。
""")


if __name__ == "__main__":
    main()

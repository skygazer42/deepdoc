#!/usr/bin/env python3
"""端到端 PDF 解析管线 CPU 基准（ResNet 论文风格）。

对 RAGFlowPdfParser.__call__ 各阶段分别计时，快速路径：
  OCR   = RapidOCR PP-OCRv6
  版面  = DocLayout-YOLO-768
  表格  = SLANet-plus
  文本  = XGBoost 合并（默认）

输出：分阶段耗时表 + 每页总耗时 + 吞吐（pages/sec）。
"""
import os
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ["OCR_ENGINE"] = "rapidocr"
os.environ["LAYOUT_MODEL"] = "doclayout"
os.environ["LAYOUT_MODEL_SIZE"] = "768"
os.environ["TABLE_ENGINE"] = "slanet"

ROOT = Path("/app") if Path("/app").exists() else Path("/data/temp4/deepdoc")

PDFS = [
    # (文件名, 描述, 预估页数)
    ("scan_2p.pdf", "扫描件 2页", 2),
    ("table_ruled.pdf", "有框线表格", 2),
    ("table_borderless.pdf", "无框线表格", 1),
    ("table_merged.pdf", "合并单元格", 2),
    ("mixed_3p.pdf", "混合文档 3页", 3),
]


def main():
    from parser.pdf_parser import RAGFlowPdfParser

    # 预热（首次 ONNX 初始化 + 模型加载不计入）
    print("预热解析器（首次模型加载）...", flush=True)
    warm = RAGFlowPdfParser()
    warm_w = Path(ROOT / "regression" / "documents" / "table_ruled.pdf")
    t0 = time.time()
    try:
        warm(str(warm_w), need_image=False)
    except Exception:
        pass
    print(f"  预热完成 ({time.time()-t0:.1f}s)\n")

    # 分阶段计时（用包装器，可跨 PDF 复用解析器以模拟连续处理）
    parser = RAGFlowPdfParser()

    stage_names = [
        ("__images__", "__images__"),           # 渲染+字符+OCR
        ("_layouts_rec", "_layouts_rec"),       # 版面检测
        ("_table_transformer_job", "_table_transformer_job"),  # 表格 TSR
        ("_text_merge", "_text_merge"),         # 文本行合并
        ("_concat_downward", "_concat_downward"),
        ("_filter_forpages", "_filter_forpages"),
        ("_extract_table_figure", "_extract_table_figure"),   # 抽表/图
    ]

    # 收集每文档数据
    print("=" * 100)
    print(f"{'文档':<24} {'页数':>4} {'__images__':>10} {'版面':>9} {'表格TSR':>9} {'合并':>8} {'抽表图':>9} {'总耗时':>10} {'每页':>7} {'吞吐':>8}")
    print("=" * 100)

    per_page_ms = []

    for pdf, desc, npages in PDFS:
        path = Path(ROOT / "regression" / "documents" / pdf)
        if not path.exists():
            print(f"[跳过] {pdf} 不存在")
            continue

        stage_times = {s: 0.0 for s, _ in stage_names}
        t_total_start = time.time()

        # __images__
        t0 = time.time()
        parser.__images__(str(path), zoomin=3)
        stage_times["__images__"] = (time.time() - t0) * 1000

        # _layouts_rec
        t0 = time.time()
        parser._layouts_rec(3)
        stage_times["_layouts_rec"] = (time.time() - t0) * 1000

        # _table_transformer_job
        t0 = time.time()
        parser._table_transformer_job(3)
        stage_times["_table_transformer_job"] = (time.time() - t0) * 1000

        # _text_merge
        t0 = time.time()
        parser._text_merge()
        stage_times["_text_merge"] = (time.time() - t0) * 1000

        # _concat_downward
        t0 = time.time()
        parser._concat_downward()
        stage_times["_concat_downward"] = (time.time() - t0) * 1000

        # _filter_forpages
        t0 = time.time()
        parser._filter_forpages()
        stage_times["_filter_forpages"] = (time.time() - t0) * 1000

        # _extract_table_figure
        t0 = time.time()
        try:
            parser._extract_table_figure(
                need_image=False, ZM=3, return_html=False, need_position=False)
        except Exception:
            pass
        stage_times["_extract_table_figure"] = (time.time() - t0) * 1000

        t_total = (time.time() - t_total_start) * 1000

        actual_pages = len(parser.page_images)
        pp = t_total / max(actual_pages, 1)
        per_page_ms.append(pp)

        print(f"{pdf:<24} {actual_pages:>4} "
              f"{stage_times['__images__']:>9.0f} "
              f"{stage_times['_layouts_rec']:>8.0f} "
              f"{stage_times['_table_transformer_job']:>8.0f} "
              f"{stage_times['_text_merge'] + stage_times['_concat_downward']:>7.0f} "
              f"{stage_times['_extract_table_figure']:>8.0f} "
              f"{t_total:>9.0f} "
              f"{pp:>6.0f} "
              f"{1000/max(pp,1):>7.2f} p/s")

    print("=" * 100)
    if per_page_ms:
        avg = np.mean(per_page_ms)
        print(f"平均每页: {avg:.0f}ms | 平均吞吐: {1000/max(avg,1):.2f} pages/sec")
    print(f"\n模型: RapidOCR PP-OCRv6 + DocLayout-YOLO-768 + SLANet-plus + XGBoost")
    print(f"CPU: {os.cpu_count()} 核 | 说明: __images__ 含 pdfplumber 渲染 + 字符提取 + OCR")


if __name__ == "__main__":
    main()

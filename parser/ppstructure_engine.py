# -*- coding: utf-8 -*-
"""
OCR 视觉引擎封装（升级计划 Step 3）

支持两种后端：
  1. PP-StructureV3：版面分析 + OCR + 表格结构（需要 PaddlePaddle）
  2. RapidOCR：轻量级 OCR（使用 ONNX Runtime，支持 PP-OCRv6）

通过环境变量 OCR_ENGINE 选择：
  - OCR_ENGINE=ppstructure: 使用 PP-StructureV3
  - OCR_ENGINE=rapidocr: 使用 RapidOCR（默认）
"""
import logging
import os
from typing import List, Optional, Tuple

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

# 全局单例：PP-StructureV3 模型加载开销大（~2-3s），进程内复用
_ppstructure_instance = None
_ppstructure_lock = None


def get_ppstructure_instance(**kwargs):
    """获取或创建 PP-StructureV3 单例实例。

    首次调用时加载模型（约 2-3s），后续复用。
    可通过环境变量控制线程数：
      - PPSTRUCTURE_THREADS: 推理线程数（默认 2）
    """
    global _ppstructure_instance, _ppstructure_lock
    if _ppstructure_instance is not None:
        return _ppstructure_instance

    import threading
    if _ppstructure_lock is None:
        _ppstructure_lock = threading.Lock()

    with _ppstructure_lock:
        # Double-check locking
        if _ppstructure_instance is not None:
            return _ppstructure_instance

        from paddleocr import PPStructureV3

        # CPU 推理线程控制
        threads = int(os.getenv("PPSTRUCTURE_THREADS", "2"))
        os.environ.setdefault("OMP_NUM_THREADS", str(threads))
        os.environ.setdefault("MKL_NUM_THREADS", str(threads))
        os.environ.setdefault("OPENBLAS_NUM_THREADS", str(threads))

        # OCR 版本：当前 PP-StructureV3 支持 PP-OCRv3/v4/v5（不支持 v6）
        # 可通过环境变量 OCR_VERSION 覆盖：PP-OCRv5, PP-OCRv4, PP-OCRv3
        ocr_version = os.getenv("OCR_VERSION", "PP-OCRv5")

        # 默认参数：关闭不需要的模块，只保留 OCR + 版面 + 表格结构
        default_kwargs = dict(
            use_doc_orientation_classify=False,  # 合同文档无需方向分类
            use_doc_unwarping=False,              # 合同文档无需去畸变
            use_textline_orientation=False,       # 中文横排为主
            use_seal_recognition=False,           # 不识别印章
            use_formula_recognition=False,        # 不识别公式
            use_chart_recognition=False,          # 不识别图表
            use_region_detection=False,           # 不做区域检测
            lang="ch",                            # 中文
            ocr_version=ocr_version,              # OCR 模型版本
        )
        default_kwargs.update(kwargs)

        logger.info("初始化 PP-StructureV3（threads=%d, ocr_version=%s）...", threads, ocr_version)
        instance = PPStructureV3(**default_kwargs)
        logger.info("PP-StructureV3 初始化完成")
        _ppstructure_instance = instance
        return instance


def ppstructure_ocr_page(page_img: Image.Image, page_no: int) -> dict:
    """对单页图像执行 PP-StructureV3 全流程：版面 + OCR + 表格结构。

    Args:
        page_img: PIL Image（RGB）
        page_no: 页码（0-based）

    Returns:
        dict: {
            "page_no": int,
            "chunks": [{"text": str, "tag": str, "kind": str, "positions": list}],
            "tables": [{"tag": str, "text": str, "html": str}],
            "elapsed_s": float,
        }
    """
    import time
    from parser.fast_pdf import _line_tag

    engine = get_ppstructure_instance()
    start = time.monotonic()

    # PP-StructureV3.predict() 接受 numpy array 或图像路径
    img_array = np.array(page_img)

    try:
        result_list = engine.predict(img_array)
    except Exception as e:
        logger.exception("PP-StructureV3 推理失败 (page %d)", page_no)
        return {
            "page_no": page_no,
            "chunks": [],
            "tables": [],
            "elapsed_s": time.monotonic() - start,
            "error": str(e),
        }

    chunks = []
    tables = []

    # predict() 返回 LayoutParsingResultV2 对象列表（每页一个）
    # 每个结果包含 parsing_res_list（LayoutBlock 对象列表）
    if result_list:
        result = result_list[0]  # 单页输入，取第一个结果

        # 解析 parsing_res_list：每个 LayoutBlock 有 label, bbox, content 属性
        parsing_res = result.get("parsing_res_list", []) if hasattr(result, 'get') else getattr(result, 'parsing_res_list', [])
        for item in parsing_res:
            label = getattr(item, 'label', 'text') if hasattr(item, 'label') else 'text'
            content = getattr(item, 'content', '') if hasattr(item, 'content') else ''
            bbox = getattr(item, 'bbox', None) if hasattr(item, 'bbox') else None

            if not content:
                continue

            # 构造位置标签
            if bbox and len(bbox) == 4:
                x0, y0, x1, y1 = bbox
                tag = _line_tag(page_no, float(x0), float(x1), float(y0), float(y1))
                positions = [{"pages": [page_no + 1], "x0": float(x0), "x1": float(x1),
                              "top": float(y0), "bottom": float(y1)}]
            else:
                tag = _line_tag(page_no, 0.0, 612.0, 0.0, 792.0)  # 默认 A4 尺寸
                positions = [{"pages": [page_no + 1], "x0": 0.0, "x1": 612.0,
                              "top": 0.0, "bottom": 792.0}]

            # 表格块：label 包含 "table"，从 table_res_list 获取 HTML
            if "table" in label.lower():
                table_res = result.get("table_res_list", []) if hasattr(result, 'get') else getattr(result, 'table_res_list', [])
                html = ""
                for t in table_res:
                    if hasattr(t, 'html'):
                        html = t.html
                        break
                chunks.append({
                    "text": content,
                    "tag": tag,
                    "kind": "table",
                    "positions": positions,
                    "meta": {"html": html, "engine": "ppstructure_v3"},
                })
                tables.append({"tag": tag, "text": content, "html": html})
            else:
                # 文本块（标题、正文等）
                chunks.append({
                    "text": content,
                    "tag": tag,
                    "kind": "text",
                    "positions": positions,
                    "meta": {"engine": "ppstructure_v3", "block_type": label},
                })

    elapsed = time.monotonic() - start
    logger.debug("PP-StructureV3 page %d: %d chunks, %d tables, %.2fs",
                 page_no, len(chunks), len(tables), elapsed)

    return {
        "page_no": page_no,
        "chunks": chunks,
        "tables": tables,
        "elapsed_s": elapsed,
    }


def ppstructure_ocr_pages(pages: List[Tuple[Image.Image, int]],
                          max_workers: int = 2) -> List[dict]:
    """批量 OCR 多页（串行，避免内存峰值过高）。

    Args:
        pages: [(PIL.Image, page_no), ...] 列表
        max_workers: 保留参数（当前串行执行）

    Returns:
        list of ppstructure_ocr_page 结果
    """
    results = []
    for img, pno in pages:
        results.append(ppstructure_ocr_page(img, pno))
    return results


def ocr_page(page_img: Image.Image, page_no: int) -> dict:
    """统一 OCR 接口：根据 OCR_ENGINE 环境变量选择后端。

    - OCR_ENGINE=rapidocr（默认）：使用 RapidOCR + PP-OCRv6
    - OCR_ENGINE=ppstructure：使用 PP-StructureV3

    Returns:
        dict: {
            "page_no": int,
            "chunks": [{"text": str, "tag": str, "kind": str, "positions": list}],
            "tables": list,  # 仅 PP-StructureV3 返回
            "elapsed_s": float,
        }
    """
    engine = os.getenv("OCR_ENGINE", "rapidocr").lower()

    if engine == "ppstructure":
        return ppstructure_ocr_page(page_img, page_no)
    else:
        from parser.rapidocr_engine import rapidocr_ocr_page
        return rapidocr_ocr_page(page_img, page_no)


def ocr_pages(pages: List[Tuple[Image.Image, int]],
              max_workers: int = 2) -> List[dict]:
    """统一批量 OCR 接口。"""
    results = []
    for img, pno in pages:
        results.append(ocr_page(img, pno))
    return results

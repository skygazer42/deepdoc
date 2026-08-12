# -*- coding: utf-8 -*-
"""
RapidOCR + PP-OCRv6 引擎封装

使用 ONNX Runtime 推理，无需 PaddlePaddle 框架。
支持 PP-OCRv4/v5/v6 模型，检测精度提升 4.6%。
"""
import logging
import os
from typing import List, Optional

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

# 全局单例
_rapidocr_instance = None
_rapidocr_lock = None


def get_rapidocr_instance(**kwargs):
    """获取或创建 RapidOCR 单例实例。

    首次调用时加载模型（首次需下载模型，后续复用）。
    可通过环境变量控制：
      - RAPIDOCR_OCR_VERSION: PP-OCRv4/v5/v6（默认 v6）
      - RAPIDOCR_MODEL_SIZE: tiny/small/medium（默认 small）
    """
    global _rapidocr_instance, _rapidocr_lock
    if _rapidocr_instance is not None:
        return _rapidocr_instance

    import threading
    if _rapidocr_lock is None:
        _rapidocr_lock = threading.Lock()

    with _rapidocr_lock:
        if _rapidocr_instance is not None:
            return _rapidocr_instance

        from rapidocr import RapidOCR, OCRVersion, LangDet, LangRec, ModelType, EngineType

        # 配置
        ocr_version_str = os.getenv("RAPIDOCR_OCR_VERSION", "PP-OCRv6")
        model_size_str = os.getenv("RAPIDOCR_MODEL_SIZE", "SMALL")

        # 映射版本
        ocr_version_map = {
            "PP-OCRv4": OCRVersion.PPOCRV4,
            "PP-OCRv5": OCRVersion.PPOCRV5,
            "PP-OCRv6": OCRVersion.PPOCRV6,
        }
        ocr_version = ocr_version_map.get(ocr_version_str, OCRVersion.PPOCRV6)

        # 映射模型大小
        model_size_map = {
            "TINY": ModelType.TINY,
            "SMALL": ModelType.SMALL,
            "MEDIUM": ModelType.MEDIUM,
        }
        model_type = model_size_map.get(model_size_str.upper(), ModelType.SMALL)

        logger.info("初始化 RapidOCR（ocr_version=%s, model_size=%s）...",
                    ocr_version_str, model_size_str)

        instance = RapidOCR(
            params={
                'Det.engine_type': EngineType.ONNXRUNTIME,
                'Det.lang_type': LangDet.CH,
                'Det.model_type': model_type,
                'Det.ocr_version': ocr_version,
                'Rec.engine_type': EngineType.ONNXRUNTIME,
                'Rec.lang_type': LangRec.CH,
                'Rec.model_type': model_type,
                'Rec.ocr_version': ocr_version,
                'Cls.engine_type': EngineType.ONNXRUNTIME,
                'Cls.lang_type': LangDet.CH,
                'Cls.model_type': ModelType.MOBILE,
                'Cls.ocr_version': OCRVersion.PPOCRV4,
            }
        )
        logger.info("RapidOCR 初始化完成")
        _rapidocr_instance = instance
        return instance


def rapidocr_ocr_page(page_img: Image.Image, page_no: int) -> dict:
    """对单页图像执行 RapidOCR。

    Args:
        page_img: PIL Image（RGB）
        page_no: 页码（0-based）

    Returns:
        dict: {
            "page_no": int,
            "chunks": [{"text": str, "tag": str, "kind": str, "positions": list}],
            "elapsed_s": float,
        }
    """
    import time
    from parser.fast_pdf import _line_tag

    engine = get_rapidocr_instance()
    start = time.monotonic()

    img_array = np.array(page_img)

    try:
        result = engine(img_array)
    except Exception as e:
        logger.exception("RapidOCR 推理失败 (page %d)", page_no)
        return {
            "page_no": page_no,
            "chunks": [],
            "elapsed_s": time.monotonic() - start,
            "error": str(e),
        }

    chunks = []

    # 解析结果
    if result and result.txts:
        boxes = result.boxes if result.boxes is not None else []
        txts = result.txts
        scores = result.scores if result.scores is not None else []

        for i, txt in enumerate(txts):
            if not txt or not txt.strip():
                continue

            # 获取 bbox
            if i < len(boxes) and boxes[i] is not None:
                bbox = boxes[i]  # [[x1,y1], [x2,y2], [x3,y3], [x4,y4]]
                # 取左上角和右下角
                x0, y0 = float(bbox[0][0]), float(bbox[0][1])
                x1, y1 = float(bbox[2][0]), float(bbox[2][1])
                tag = _line_tag(page_no, x0, x1, y0, y1)
                positions = [{"pages": [page_no + 1], "x0": x0, "x1": x1,
                              "top": y0, "bottom": y1}]
            else:
                tag = _line_tag(page_no, 0.0, 612.0, 0.0, 792.0)
                positions = [{"pages": [page_no + 1], "x0": 0.0, "x1": 612.0,
                              "top": 0.0, "bottom": 792.0}]

            score = float(scores[i]) if i < len(scores) else 1.0

            chunks.append({
                "text": txt.strip(),
                "tag": tag,
                "kind": "text",
                "positions": positions,
                "meta": {"engine": "rapidocr_ppocrv6", "confidence": score},
            })

    elapsed = time.monotonic() - start
    logger.debug("RapidOCR page %d: %d chunks, %.2fs", page_no, len(chunks), elapsed)

    return {
        "page_no": page_no,
        "chunks": chunks,
        "elapsed_s": elapsed,
    }


def rapidocr_ocr_pages(pages: List[tuple], max_workers: int = 2) -> List[dict]:
    """批量 OCR 多页。

    Args:
        pages: [(PIL.Image, page_no), ...] 列表
        max_workers: 保留参数（当前串行执行）

    Returns:
        list of rapidocr_ocr_page 结果
    """
    results = []
    for img, pno in pages:
        results.append(rapidocr_ocr_page(img, pno))
    return results

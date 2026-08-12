# -*- coding: utf-8 -*-
"""
RapidOCR + PP-OCRv6 适配器 — 暴露与 vision.ocr.OCR 相同的 API

用于替换 pdf_parser.py 中的 PaddleOCR，保留 YOLO 版面 + TSR + XGBoost 管道。

用法：
    from vision.rapidocr_wrapper import RapidOCREngine
    ocr = RapidOCREngine()
    boxes = ocr.detect(img)          # 仅检测
    texts = ocr.recognize_batch(imgs)  # 批量识别
"""
import copy
import logging
import os
import time

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# 全局单例（复用 rapidocr_engine.py 的缓存逻辑）
_rapidocr_instance = None
_rapidocr_lock = None


def _get_rapidocr():
    """获取或创建 RapidOCR 单例。"""
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

        ocr_version_str = os.getenv("RAPIDOCR_OCR_VERSION", "PP-OCRv6")
        model_size_str = os.getenv("RAPIDOCR_MODEL_SIZE", "SMALL")

        ocr_version_map = {
            "PP-OCRv4": OCRVersion.PPOCRV4,
            "PP-OCRv5": OCRVersion.PPOCRV5,
            "PP-OCRv6": OCRVersion.PPOCRV6,
        }
        ocr_version = ocr_version_map.get(ocr_version_str, OCRVersion.PPOCRV6)

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


class RapidOCREngine:
    """与 vision.ocr.OCR 相同 API 的 RapidOCR 适配器。

    核心方法：
      - detect(img) → zip(boxes, [("", 0), ...])
      - recognize_batch(img_list) → [str, ...]
      - get_rotate_crop_image(img, points) → np.ndarray
    """

    def __init__(self):
        self._engine = _get_rapidocr()
        self.drop_score = 0.5
        self.parallel_limiter = None  # 保持与原 OCR 相同的接口

    # ------------------------------------------------------------------
    # 检测：返回排序后的 boxes，格式与 OCR.detect() 完全一致
    # ------------------------------------------------------------------
    def detect(self, img, device_id=None):
        """仅检测文本框，返回 zip(sorted_boxes, [("", 0), ...])。

        Args:
            img: numpy array (H, W, 3) BGR
            device_id: 兼容参数，忽略

        Returns:
            zip of (box, (text, score)) — boxes 为 np.ndarray shape (4, 2)
        """
        if self.parallel_limiter is not None:
            with self.parallel_limiter:
                return self._detect_impl(img)
        return self._detect_impl(img)

    def _detect_impl(self, img):
        if img is None:
            return None

        op_record = {}
        _, det_result = self._engine.detect_and_crop(img, op_record)

        if det_result is None or det_result.boxes is None or len(det_result.boxes) == 0:
            return None

        boxes = det_result.boxes
        sorted_boxes = self._sorted_boxes(boxes)

        return zip(sorted_boxes, [("", 0) for _ in range(len(sorted_boxes))])

    # ------------------------------------------------------------------
    # 识别：批量识别裁剪后的图像
    # ------------------------------------------------------------------
    def recognize_batch(self, img_list, device_id=None):
        """批量识别裁剪后的文本图像。

        Args:
            img_list: list of numpy arrays (裁剪后的图像)
            device_id: 兼容参数，忽略

        Returns:
            list of text strings
        """
        if self.parallel_limiter is not None:
            with self.parallel_limiter:
                return self._recognize_batch_impl(img_list)
        return self._recognize_batch_impl(img_list)

    def _recognize_batch_impl(self, img_list):
        if not img_list:
            return []

        rec_result = self._engine.recognize_txt(img_list)

        texts = []
        if rec_result and rec_result.txts:
            for i, txt in enumerate(rec_result.txts):
                score = rec_result.scores[i] if rec_result.scores and i < len(rec_result.scores) else 1.0
                if score < self.drop_score:
                    texts.append("")
                else:
                    texts.append(txt if txt else "")
        return texts

    # ------------------------------------------------------------------
    # 裁剪旋转：直接复用原始 OCR 的实现（纯 numpy/cv2，无模型依赖）
    # ------------------------------------------------------------------
    def get_rotate_crop_image(self, img, points):
        """裁剪并旋转文本框区域。"""
        assert len(points) == 4, "shape of points must be 4*2"
        img_crop_width = int(
            max(
                np.linalg.norm(points[0] - points[1]),
                np.linalg.norm(points[2] - points[3])))
        img_crop_height = int(
            max(
                np.linalg.norm(points[0] - points[3]),
                np.linalg.norm(points[1] - points[2])))
        pts_std = np.float32([[0, 0], [img_crop_width, 0],
                              [img_crop_width, img_crop_height],
                              [0, img_crop_height]])
        M = cv2.getPerspectiveTransform(points, pts_std)
        dst_img = cv2.warpPerspective(
            img,
            M, (img_crop_width, img_crop_height),
            borderMode=cv2.BORDER_REPLICATE,
            flags=cv2.INTER_CUBIC)
        dst_img_height, dst_img_width = dst_img.shape[0:2]
        if dst_img_height * 1.0 / dst_img_width >= 1.5:
            dst_img = np.rot90(dst_img)
        return dst_img

    # ------------------------------------------------------------------
    # 排序：与 OCR.sorted_boxes() 相同的逻辑
    # ------------------------------------------------------------------
    def _sorted_boxes(self, dt_boxes):
        """按从上到下、从左到右排序文本框。"""
        num_boxes = len(dt_boxes)
        sorted_boxes = sorted(dt_boxes, key=lambda x: (x[0][1], x[0][0]))
        _boxes = list(sorted_boxes)

        for i in range(num_boxes - 1):
            for j in range(i, -1, -1):
                if abs(_boxes[j + 1][0][1] - _boxes[j][0][1]) < 10 and \
                        (_boxes[j + 1][0][0] < _boxes[j][0][0]):
                    tmp = _boxes[j]
                    _boxes[j] = _boxes[j + 1]
                    _boxes[j + 1] = tmp
                else:
                    break
        return _boxes

    # ------------------------------------------------------------------
    # 完整管道（可选，保持与 OCR.__call__ 兼容）
    # ------------------------------------------------------------------
    def __call__(self, img, device_id=0, cls=True):
        """完整 OCR 管道（检测 + 识别），与 OCR.__call__ 兼容。"""
        time_dict = {'det': 0, 'rec': 0, 'cls': 0, 'all': 0}
        if img is None:
            return None, None, time_dict

        start = time.time()

        # 检测
        det_start = time.time()
        op_record = {}
        cropped_imgs, det_result = self._engine.detect_and_crop(img, op_record)
        time_dict['det'] = time.time() - det_start

        if det_result is None or det_result.boxes is None or len(det_result.boxes) == 0:
            time_dict['all'] = time.time() - start
            return None, None, time_dict

        boxes = self._sorted_boxes(det_result.boxes)

        # 识别
        rec_start = time.time()
        rec_result = self._engine.recognize_txt(cropped_imgs)
        time_dict['rec'] = time.time() - rec_start

        # 过滤低置信度结果
        filter_boxes, filter_rec_res = [], []
        if rec_result and rec_result.txts:
            for i, (box, txt) in enumerate(zip(boxes, rec_result.txts)):
                score = rec_result.scores[i] if rec_result.scores and i < len(rec_result.scores) else 0
                if score >= self.drop_score:
                    filter_boxes.append(box)
                    filter_rec_res.append((txt, score))

        time_dict['all'] = time.time() - start
        return list(zip([a.tolist() for a in filter_boxes], filter_rec_res))

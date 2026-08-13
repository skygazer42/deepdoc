#!/usr/bin/env python3
"""OCR 瓶颈剖析：单页 detect/recognize 分解 + 页间并发收益实测。

用法: python script/profile_ocr.py [页数]
"""
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))
ROOT = Path("/app") if Path("/app").exists() else Path("/data/temp4/deepdoc")

os.environ["OCR_ENGINE"] = "rapidocr"
os.environ["RAPIDOCR_OCR_VERSION"] = "PP-OCRv6"
os.environ["RAPIDOCR_MODEL_SIZE"] = "SMALL"

N_PAGES = int(sys.argv[1]) if len(sys.argv) > 1 else 4


def render_pages(pdf_path, zoomin=3, max_pages=N_PAGES):
    import fitz
    doc = fitz.open(pdf_path)
    imgs = []
    for i, page in enumerate(doc):
        if i >= max_pages:
            break
        pix = page.get_pixmap(matrix=fitz.Matrix(zoomin, zoomin))
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
            pix.height, pix.width, pix.n)
        img = cv2.cvtColor(img, cv2.COLOR_RGBA2BGR if pix.n == 4 else cv2.COLOR_RGB2BGR)
        imgs.append(img)
    doc.close()
    return imgs


def ocr_page(ocr, img):
    """一页完整 OCR：detect + 裁剪 + recognize，返回 (detect_s, recognize_s, n_boxes)。"""
    t0 = time.time()
    bxs = ocr.detect(img)
    t_det = time.time() - t0
    if not bxs:
        return t_det, 0.0, 0
    crops = []
    for line in bxs:
        box = line[0]
        left, right, top, bott = box[0][0], box[1][0], box[0][1], box[-1][1]
        crops.append(ocr.get_rotate_crop_image(
            img, np.array([[left, top], [right, top], [right, bott], [left, bott]],
                          dtype=np.float32)))
    t0 = time.time()
    texts = ocr.recognize_batch(crops)
    t_rec = time.time() - t0
    return t_det, t_rec, len(crops)


def main():
    from vision import RapidOCREngine
    ocr = RapidOCREngine()
    pages = render_pages(ROOT / "regression" / "documents" / "resnet.pdf")

    # 1. 单页分解（前 3 页）
    print(f"=== 单页 detect/recognize 分解（{min(3, len(pages))} 页）===")
    tot_d, tot_r, tot_b = 0.0, 0.0, 0
    for i in range(min(3, len(pages))):
        d, r, n = ocr_page(ocr, pages[i])
        tot_d, tot_r, tot_b = tot_d + d, tot_r + r, tot_b + n
        print(f"  p{i+1}: detect {d:.2f}s | recognize {r:.2f}s | {n} boxes")
    print(f"  --- detect 占 {(tot_d/(tot_d+tot_r))*100:.0f}%, recognize 占 {(tot_r/(tot_d+tot_r))*100:.0f}%")

    # 2. 串行 vs 页间并发（ThreadPoolExecutor 模拟）
    print(f"\n=== 页间并发收益（{len(pages)} 页）===")
    # 串行
    t0 = time.time()
    for img in pages:
        ocr_page(ocr, img)
    t_ser = time.time() - t0
    print(f"  串行: {t_ser:.1f}s ({t_ser/len(pages):.2f}s/页)")
    # 并发 2/3/4/6
    for nw in (2, 3, 4, 6):
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=nw) as ex:
            list(ex.map(lambda im: ocr_page(ocr, im), pages))
        t_par = time.time() - t0
        print(f"  并发 {nw} worker: {t_par:.1f}s ({t_ser/max(t_par,1e-9):.2f}x)")


if __name__ == "__main__":
    main()

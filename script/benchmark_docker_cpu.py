#!/usr/bin/env python3
"""容器内 CPU 推理耗时基准：DocLayout-YOLO-768 版面 + SLANet-plus TSR + RapidOCR PP-OCRv6。

与宿主机 script/benchmark_layout_real.py / benchmark_tsr.py 的度量口径一致，
用于对比 Docker 容器 vs 宿主机 CPU 推理速度。仅输出统计信息，不输出文档正文。
"""
import os
import sys
import time
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ["LAYOUT_MODEL"] = "doclayout"
os.environ["LAYOUT_MODEL_SIZE"] = "768"
os.environ["TABLE_ENGINE"] = "slanet"
os.environ["OCR_ENGINE"] = "rapidocr"

# 路径自适应：容器内 /app，宿主机 /data/temp4/deepdoc
ROOT = Path("/app") if Path("/app").exists() else Path("/data/temp4/deepdoc")
MODEL_DIR = str(ROOT / "resources" / "models")
PDF_DIR = str(ROOT / "regression" / "documents")

LAYOUT_PATHS = {
    "DocLayout-768": f"{MODEL_DIR}/layout/layout_doclayout_768.onnx",
    "YOLOv10-当前": f"{MODEL_DIR}/layout/layout.onnx",
}

PDFS = [
    "scan_2p.pdf",
    "editable_twocol.pdf",
    "table_ruled.pdf",
    "mixed_3p.pdf",
]


def render_pages(pdf_path, zoomin=3, max_pages=2):
    import fitz
    doc = fitz.open(pdf_path)
    imgs = []
    for i, page in enumerate(doc):
        if i >= max_pages:
            break
        pix = page.get_pixmap(matrix=fitz.Matrix(zoomin, zoomin))
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        img = cv2.cvtColor(img, cv2.COLOR_RGBA2BGR if pix.n == 4 else cv2.COLOR_RGB2BGR)
        imgs.append(img)
    doc.close()
    return imgs


def preprocess(img, size=768):
    shape = img.shape[:2]
    r = min(size / shape[0], size / shape[1])
    new_unpad = int(round(shape[1] * r)), int(round(shape[0] * r))
    dw, dh = (size - new_unpad[0]) / 2, (size - new_unpad[1]) / 2
    x = cv2.cvtColor(img, cv2.COLOR_BGR2RGB).astype(np.float32)
    x = cv2.resize(x, new_unpad, interpolation=cv2.INTER_LINEAR)
    top, bottom = int(round(dh - 0.1)), int(round(dh + 0.1))
    left, right = int(round(dw - 0.1)), int(round(dw + 0.1))
    x = cv2.copyMakeBorder(x, top, bottom, left, right, cv2.BORDER_CONSTANT, value=(114, 114, 114))
    x = (x / 255.0).transpose(2, 0, 1)[np.newaxis].astype(np.float32)
    scale = [shape[1] / new_unpad[0], shape[0] / new_unpad[1], dw, dh]
    return x, scale


def bench_layout():
    import onnxruntime as ort
    print("\n" + "=" * 70)
    print("版面检测 (Layout) — 逐页推理耗时")
    print("=" * 70)
    sessions = {n: ort.InferenceSession(p, providers=["CPUExecutionProvider"])
                for n, p in LAYOUT_PATHS.items() if Path(p).exists()}
    if not sessions:
        print("! 无布局模型文件，跳过")
        return

    for pdf in PDFS:
        pdf_path = f"{PDF_DIR}/{pdf}"
        if not Path(pdf_path).exists():
            print(f"[跳过] {pdf} 不存在")
            continue
        try:
            pages = render_pages(pdf_path)
        except Exception as e:
            print(f"[跳过] {pdf}: {e}")
            continue
        line = f"  {pdf:<22} "
        for name, sess in sessions.items():
            times = []
            for img in pages:
                size = 768 if "768" in name else 1024
                inp, _ = preprocess(img, size)
                t0 = time.time()
                sess.run(None, {sess.get_inputs()[0].name: inp})
                times.append((time.time() - t0) * 1000)
            avg = np.mean(times)
            line += f"{name}: {avg:5.0f}ms/页  "
        print(line)


def bench_tsr():
    print("\n" + "=" * 70)
    print("表格结构 (TSR) — SLANet-plus vs YOLO tsr.onnx（全图）")
    print("=" * 70)
    try:
        from rapid_table import RapidTable, RapidTableInput, ModelType, EngineType
    except ImportError:
        print("! rapid_table 未安装，跳过 TSR")
        return

    slanet = RapidTable(RapidTableInput(
        model_type=ModelType.SLANETPLUS, engine_type=EngineType.ONNXRUNTIME, use_ocr=False))
    try:
        import onnxruntime as ort
        from vision.table_structure_recognizer import TableStructureRecognizer as YoloTSR
        yolo = YoloTSR()
        has_yolo = True
    except Exception as e:
        print(f"! YOLO TSR 不可用: {e}")
        has_yolo = False

    for pdf, pi in [("table_ruled.pdf", 0), ("table_merged.pdf", 0)]:
        pdf_path = f"{PDF_DIR}/{pdf}"
        if not Path(pdf_path).exists():
            continue
        pages = render_pages(pdf_path, max_pages=pi + 1)
        img = pages[pi]
        # TSR 需要 PIL
        from PIL import Image
        pil_img = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))

        line = f"  {pdf}/p{pi+1:<14} "
        # SLANet
        import numpy as np
        t0 = time.time()
        slanet(pil_img)
        dt_sl = (time.time() - t0) * 1000
        line += f"SLANet+: {dt_sl:5.0f}ms  "
        # YOLO
        if has_yolo:
            t0 = time.time()
            yolo([pil_img], thr=0.2)
            dt_y = (time.time() - t0) * 1000
            line += f"YOLO TSR: {dt_y:5.0f}ms"
        print(line)


def bench_ocr():
    print("\n" + "=" * 70)
    print("OCR — RapidOCR PP-OCRv6（全图 detect+recognize）")
    print("=" * 70)
    try:
        from vision import RapidOCREngine
    except ImportError:
        print("! RapidOCREngine 不可用，跳过")
        return
    ocr = RapidOCREngine()
    for pdf in PDFS:
        pdf_path = f"{PDF_DIR}/{pdf}"
        if not Path(pdf_path).exists():
            continue
        pages = render_pages(pdf_path, max_pages=1)
        img = pages[0]
        t0 = time.time()
        bxs = ocr.detect(img)
        n_box = len(list(bxs)) if bxs else 0
        dt = (time.time() - t0) * 1000
        print(f"  {pdf:<22} detect: {dt:5.0f}ms  ({n_box} boxes)")


if __name__ == "__main__":
    print(f"CPU: {os.cpu_count()} cores | 线程上限: {os.environ.get('OMP_NUM_THREADS', '默认')}")
    t0 = time.time()
    bench_layout()
    bench_tsr()
    bench_ocr()
    print(f"\n总耗时: {time.time()-t0:.0f}s")

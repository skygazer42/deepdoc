#!/usr/bin/env python3
"""
真实 PDF 版面检测对比：InfiniFlow YOLOv10 (当前) vs DocLayout-YOLO
两个模型输入/输出格式完全一致 (1,3,1024,1024) -> (1,300,6)，共用同一套前后处理。
仅输出统计信息（检测框数量/类别/耗时），不输出文档正文内容。
"""
import sys
import time
from collections import Counter
from pathlib import Path

import cv2
import fitz  # pymupdf
import numpy as np
import onnxruntime as ort

sys.path.insert(0, str(Path(__file__).parent.parent))

IF_LABELS = ["title", "Text", "Reference", "Figure", "Figure caption",
             "Table", "Table caption", "Table caption", "Equation", "Figure caption"]
DL_LABELS = ["title", "plain text", "abandon", "figure", "figure_caption",
             "table", "table_caption", "table_footnote", "isolate_formula", "formula_caption"]

MODELS = {
    "YOLOv10-InfiniFlow(当前)": {
        "path": "/data/temp4/deepdoc/resources/models/layout/layout.onnx",
        "labels": IF_LABELS, "size": 1024,
    },
    "DocLayout-YOLO-1024": {
        "path": "/data/temp4/deepdoc/resources/models/layout/layout_doclayout_1024.onnx",
        "labels": DL_LABELS, "size": 1024,
    },
    "DocLayout-YOLO-768": {
        "path": "/data/temp4/deepdoc/resources/models/layout/layout_doclayout_768.onnx",
        "labels": DL_LABELS, "size": 768,
    },
    "DocLayout-YOLO-640": {
        "path": "/data/temp4/deepdoc/resources/models/layout/layout_doclayout_640.onnx",
        "labels": DL_LABELS, "size": 640,
    },
}

PDFS = [
    "/data/temp4/deepdoc/regression/documents/scan_2p.pdf",
    "/data/temp4/deepdoc/regression/documents/editable_twocol.pdf",
    "/data/temp4/deepdoc/regression/documents/table_ruled.pdf",
    "/data/temp4/deepdoc/regression/documents/mixed_3p.pdf",
]

CONF_THR = 0.08  # 与 LayoutRecognizer4YOLOv10.postprocess 相同


def render_pages(pdf_path, zoomin=3, max_pages=2):
    """用 pymupdf 渲染 PDF 页面为 BGR 图像（与管道 72*zoomin DPI 一致）。"""
    doc = fitz.open(pdf_path)
    imgs = []
    for i, page in enumerate(doc):
        if i >= max_pages:
            break
        pix = page.get_pixmap(matrix=fitz.Matrix(zoomin, zoomin))
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        if pix.n == 4:
            img = cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
        else:
            img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
        imgs.append(img)
    doc.close()
    return imgs


def preprocess(img, size=1024):
    """与 LayoutRecognizer4YOLOv10.preprocess 相同的 letterbox（居中填充）。"""
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


def postprocess(out, scale, labels, thr=CONF_THR):
    """与 LayoutRecognizer4YOLOv10.postprocess 相同：输出 (300,6) = x1,y1,x2,y2,score,cls。"""
    boxes = np.squeeze(out)
    scores = boxes[:, 4]
    boxes = boxes[scores > thr, :]
    if len(boxes) == 0:
        return []
    dets = []
    for b in boxes:
        cls = int(b[5])
        dets.append({
            "label": labels[cls] if cls < len(labels) else f"cls_{cls}",
            "score": float(b[4]),
            "bbox": [
                (float(b[0]) - scale[2]) * scale[0],
                (float(b[1]) - scale[3]) * scale[1],
                (float(b[2]) - scale[2]) * scale[0],
                (float(b[3]) - scale[3]) * scale[1],
            ],
        })
    return dets


def main():
    sessions = {}
    for name, cfg in MODELS.items():
        sessions[name] = ort.InferenceSession(cfg["path"], providers=["CPUExecutionProvider"])

    totals = {name: {"time": 0.0, "pages": 0, "dets": Counter()} for name in MODELS}

    for pdf in PDFS:
        pdf_name = Path(pdf).name
        try:
            pages = render_pages(pdf)
        except Exception as e:
            print(f"[跳过] {pdf_name}: {e}")
            continue
        print(f"\n{'='*72}\n文档: {pdf_name}（{len(pages)} 页，{pages[0].shape[1]}x{pages[0].shape[0]}）\n{'='*72}")

        for pi, img in enumerate(pages):
            print(f"\n-- 第 {pi+1} 页 --")
            for name, cfg in MODELS.items():
                sess = sessions[name]
                inp, scale = preprocess(img, cfg["size"])
                t0 = time.time()
                out = sess.run(None, {sess.get_inputs()[0].name: inp})[0]
                dt = time.time() - t0
                dets = postprocess(out, scale, cfg["labels"])
                cnt = Counter(d["label"] for d in dets)
                totals[name]["time"] += dt
                totals[name]["pages"] += 1
                totals[name]["dets"] += cnt
                summary = ", ".join(f"{k}×{v}" for k, v in cnt.most_common()) or "无检测"
                print(f"  {name:<28} {dt*1000:7.1f}ms  {len(dets):3d} 框  [{summary}]")

    print(f"\n{'='*72}\n汇总\n{'='*72}")
    print(f"{'模型':<28} {'平均耗时':<12} {'总框数':<8} 类别分布")
    for name, t in totals.items():
        avg = t["time"] / max(t["pages"], 1) * 1000
        dist = ", ".join(f"{k}×{v}" for k, v in t["dets"].most_common())
        print(f"{name:<28} {avg:8.1f}ms  {sum(t['dets'].values()):<8d} {dist}")


if __name__ == "__main__":
    main()

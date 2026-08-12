#!/usr/bin/env python3
"""
版面检测效果可视化：当前 YOLOv10 vs DocLayout-YOLO-768 并排对比图。
输出到 output/layout_compare/，仅含检测框标注，不输出文档正文。
"""
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort

sys.path.insert(0, str(Path(__file__).parent))
from benchmark_layout_real import (DL_LABELS, IF_LABELS, postprocess,
                                   preprocess, render_pages)

OUT_DIR = Path("/data/temp4/deepdoc/output/layout_compare")

MODELS = {
    "YOLOv10-current": {
        "path": "/data/temp4/deepdoc/resources/models/layout/layout.onnx",
        "labels": IF_LABELS, "size": 1024,
    },
    "DocLayout-768": {
        "path": "/data/temp4/deepdoc/resources/models/layout/layout_doclayout_768.onnx",
        "labels": DL_LABELS, "size": 768,
    },
}

PAGES = [  # (pdf, page_idx)
    ("/data/temp4/deepdoc/regression/documents/scan_2p.pdf", 0),
    ("/data/temp4/deepdoc/regression/documents/editable_twocol.pdf", 0),
    ("/data/temp4/deepdoc/regression/documents/table_ruled.pdf", 0),
    ("/data/temp4/deepdoc/regression/documents/mixed_3p.pdf", 1),
]

# BGR 颜色，按归一化类别
COLORS = {
    "text": (0, 170, 0), "plain text": (0, 170, 0),
    "title": (0, 0, 230),
    "table": (230, 100, 0),
    "table caption": (230, 200, 0), "table_caption": (230, 200, 0), "table_footnote": (230, 200, 0),
    "figure": (200, 0, 200),
    "figure caption": (0, 140, 255), "figure_caption": (0, 140, 255), "formula_caption": (0, 140, 255),
    "reference": (128, 128, 128), "abandon": (128, 128, 128),
    "equation": (0, 220, 220), "isolate_formula": (0, 220, 220),
}


def draw(img, dets, header):
    vis = img.copy()
    for d in dets:
        x1, y1, x2, y2 = [int(v) for v in d["bbox"]]
        label = d["label"]
        color = COLORS.get(label.lower(), (60, 60, 60))
        cv2.rectangle(vis, (x1, y1), (x2, y2), color, 4)
        tag = f'{label} {d["score"]:.2f}'
        (tw, th), _ = cv2.getTextSize(tag, cv2.FONT_HERSHEY_SIMPLEX, 1.0, 2)
        ty = max(y1, th + 8)
        cv2.rectangle(vis, (x1, ty - th - 8), (x1 + tw + 8, ty + 4), color, -1)
        cv2.putText(vis, tag, (x1 + 4, ty - 2), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)
    # 顶部横幅
    banner = np.full((90, vis.shape[1], 3), 245, dtype=np.uint8)
    cv2.putText(banner, header, (20, 62), cv2.FONT_HERSHEY_SIMPLEX, 1.8, (0, 0, 0), 4)
    return np.vstack([banner, vis])


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    sessions = {n: ort.InferenceSession(c["path"], providers=["CPUExecutionProvider"])
                for n, c in MODELS.items()}

    for pdf, pi in PAGES:
        name = Path(pdf).stem
        pages = render_pages(pdf, max_pages=pi + 1)
        img = pages[pi]
        halves = []
        for mname, cfg in MODELS.items():
            sess = sessions[mname]
            inp, scale = preprocess(img, cfg["size"])
            t0 = time.time()
            out = sess.run(None, {sess.get_inputs()[0].name: inp})[0]
            dt = (time.time() - t0) * 1000
            dets = postprocess(out, scale, cfg["labels"])
            halves.append(draw(img, dets, f"{mname}  {dt:.0f}ms  {len(dets)} boxes"))
        gap = np.full((halves[0].shape[0], 16, 3), 30, dtype=np.uint8)
        combo = np.hstack([halves[0], gap, halves[1]])
        out_path = OUT_DIR / f"{name}_p{pi+1}.jpg"
        cv2.imwrite(str(out_path), combo, [cv2.IMWRITE_JPEG_QUALITY, 82])
        print(f"已生成: {out_path}")


if __name__ == "__main__":
    main()

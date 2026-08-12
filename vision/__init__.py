#
#  Copyright 2025 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
import io
import sys
import threading
import os
import pdfplumber
from vision.recognizer import Recognizer

# 版面识别引擎选择：
#   LAYOUT_MODEL=original（默认）→ InfiniFlow YOLOv10（layout.onnx）
#   LAYOUT_MODEL=doclayout       → DocLayout-YOLO（DocStructBench），
#                                  配合 LAYOUT_MODEL_SIZE=1024/768/640（默认 768，~2x 加速）
if os.getenv("LAYOUT_MODEL", "original").lower() == "doclayout":
    from .layout_recognizer import LayoutRecognizer4DocLayoutYOLO as LayoutRecognizer
else:
    from .layout_recognizer import LayoutRecognizer4YOLOv10 as LayoutRecognizer
from .ocr import OCR
from .rapidocr_wrapper import RapidOCREngine
from .table_structure_recognizer import TableStructureRecognizer, TableStructureRecognizer4SLANet

# 表格结构识别引擎选择：
#   TABLE_ENGINE=slanet → SLANet-plus (端到端单元格检测，合并单元格识别)
#   默认 → YOLO tsr.onnx
if os.getenv("TABLE_ENGINE", "").lower() == "slanet":
    TableStructureRecognizer = TableStructureRecognizer4SLANet

LOCK_KEY_pdfplumber = "global_shared_lock_pdfplumber"
if LOCK_KEY_pdfplumber not in sys.modules:
    sys.modules[LOCK_KEY_pdfplumber] = threading.Lock()


def traversal_files(base):
    for root, ds, fs in os.walk(base):
        for f in fs:
            fullname = os.path.join(root, f)
            yield fullname


def init_in_out(args):
    from PIL import Image
    import os
    import traceback
    images = []
    outputs = []

    if not os.path.exists(args.output_dir):
        os.mkdir(args.output_dir)

    def pdf_pages(fnm, zoomin=3):
        nonlocal outputs, images
        with sys.modules[LOCK_KEY_pdfplumber]:
            pdf = pdfplumber.open(fnm)
            images = [p.to_image(resolution=72 * zoomin).annotated for i, p in
                      enumerate(pdf.pages)]

        for i, page in enumerate(images):
            outputs.append(os.path.split(fnm)[-1] + f"_{i}.jpg")
        pdf.close()

    def images_and_outputs(fnm):
        nonlocal outputs, images
        if fnm.split(".")[-1].lower() == "pdf":
            pdf_pages(fnm)
            return
        try:
            fp = open(fnm, 'rb')
            binary = fp.read()
            fp.close()
            images.append(Image.open(io.BytesIO(binary)).convert('RGB'))
            outputs.append(os.path.split(fnm)[-1])
        except Exception:
            traceback.print_exc()

    if os.path.isdir(args.inputs):
        for fnm in traversal_files(args.inputs):
            images_and_outputs(fnm)
    else:
        images_and_outputs(args.inputs)

    for i in range(len(outputs)):
        outputs[i] = os.path.join(args.output_dir, outputs[i])

    return images, outputs


__all__ = [
    "OCR",
    "RapidOCREngine",
    "Recognizer",
    "LayoutRecognizer",
    "TableStructureRecognizer",
    "init_in_out",
]

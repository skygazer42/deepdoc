from pathlib import Path

import pdfplumber
from PIL import Image

from parser.pdf_parser import (
    RAGFlowPdfParser,
    _native_chars_are_usable,
    _native_text_boxes,
)


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "regression" / "documents"


def test_resnet_native_text_builds_spaced_layout_boxes(monkeypatch):
    monkeypatch.delenv("NATIVE_TEXT_MODE", raising=False)
    with pdfplumber.open(DOCS / "resnet.pdf") as pdf:
        page = pdf.pages[0]
        chars = page.dedupe_chars().chars
        boxes = _native_text_boxes(chars, 1, page.width)

    assert _native_chars_are_usable(chars)
    assert boxes[0]["text"] == "Deep Residual Learning for Image Recognition"
    assert 50 < len(boxes) < 200
    assert all(box["page_number"] == 1 for box in boxes)


class _FailIfCalledOCR:
    parallel_limiter = None

    def detect(self, *_args, **_kwargs):
        raise AssertionError("原生文本页不应调用整页 OCR")


class _RecordingEmptyOCR:
    parallel_limiter = None

    def __init__(self):
        self.detect_calls = 0

    def detect(self, *_args, **_kwargs):
        self.detect_calls += 1
        return []


def _parser_without_models(ocr):
    parser = object.__new__(RAGFlowPdfParser)
    parser.ocr = ocr
    parser.parallel_limiter = None
    return parser


def test_resnet_page_bypasses_ocr(monkeypatch):
    monkeypatch.delenv("NATIVE_TEXT_MODE", raising=False)
    parser = _parser_without_models(_FailIfCalledOCR())

    zoomin = parser.__images__(
        str(DOCS / "resnet.pdf"), zoomin=1, page_to=1)

    assert zoomin == 1
    assert parser.native_text_pages == [1]
    assert parser.ocr_pages == []
    assert parser.boxes[0][0]["text"] == "Deep Residual Learning for Image Recognition"


def test_scan_page_still_uses_ocr(monkeypatch):
    monkeypatch.delenv("NATIVE_TEXT_MODE", raising=False)
    ocr = _RecordingEmptyOCR()
    parser = _parser_without_models(ocr)

    zoomin = parser.__images__(
        str(DOCS / "scan_2p.pdf"), zoomin=1, page_to=1)

    assert zoomin == 1
    assert parser.native_text_pages == []
    assert parser.ocr_pages == [1]
    assert ocr.detect_calls == 1


def test_figure_text_is_removed_when_image_output_is_disabled():
    parser = object.__new__(RAGFlowPdfParser)
    parser.boxes = [
        {
            "text": "training error 20-layer",
            "x0": 300.0, "x1": 500.0, "top": 100.0, "bottom": 200.0,
            "page_number": 1, "layout_type": "figure", "layoutno": "figure-0",
        },
        {
            "text": "Abstract body",
            "x0": 50.0, "x1": 280.0, "top": 100.0, "bottom": 120.0,
            "page_number": 1, "layout_type": "text", "layoutno": "text-0",
        },
    ]
    parser.page_from = 0
    parser.page_images = [Image.new("RGB", (600, 800), "white")]
    parser.page_cum_height = [0, 800]
    parser.page_layout = [[]]
    parser.mean_height = [10]
    parser.is_english = True

    media = parser._extract_table_figure(
        need_image=False, ZM=1, return_html=False, need_position=False)

    assert media == []
    assert [box["text"] for box in parser.boxes] == ["Abstract body"]

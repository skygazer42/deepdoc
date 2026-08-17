from pathlib import Path

import fitz

from parser.fast_pdf import (
    ModelConfig,
    _find_table_regions,
    _table_region_needs_structure,
    parse_pdf_document,
)


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "regression" / "documents"


def test_resnet_charts_are_not_table_regions():
    doc = fitz.open(DOCS / "resnet.pdf")
    try:
        # 第 1 页是折线图，第 4 页是网络结构图；都不应触发表格判定。
        assert _find_table_regions(doc[0]) == []
        assert _find_table_regions(doc[3]) == []
        # 第 5 页有一个大型网络结构表和一个小型结果表；中间两张训练曲线
        # 不能被算作第三张表。
        regions = _find_table_regions(doc[4])
        assert len(regions) == 2
        assert regions[0][1] < 100 and regions[0][3] < 230
        assert 430 < regions[1][1] < 460 and regions[1][3] < 500
    finally:
        doc.close()


def test_only_resnet_complex_table_selects_structure_model():
    cfg = ModelConfig.from_env()
    doc = fitz.open(DOCS / "resnet.pdf")
    try:
        regions = _find_table_regions(doc[4])
        assert _table_region_needs_structure(doc[4], regions[0], cfg)
        assert not _table_region_needs_structure(doc[4], regions[1], cfg)
    finally:
        doc.close()


def test_regression_table_variants_keep_regions():
    for name in (
        "table_ruled.pdf",
        "table_merged.pdf",
        "table_crosspage.pdf",
        "table_borderless.pdf",
    ):
        doc = fitz.open(DOCS / name)
        try:
            assert all(_find_table_regions(page) for page in doc), name
        finally:
            doc.close()


def test_resnet_keeps_native_order_and_limits_table_scope():
    cfg = ModelConfig.from_env()
    cfg.ocr_depth = "skip"
    result = parse_pdf_document(str(DOCS / "resnet.pdf"), cfg=cfg)

    assert result.error is None
    assert len(result.chunks) > 500
    table_chunks = [chunk for chunk in result.chunks if chunk.kind == "table"]
    assert 10 < len(table_chunks) < 150
    structured = [chunk for chunk in table_chunks
                  if chunk.meta.get("engine") == "deepdoc_tsr"]
    assert len(structured) == 9
    assert structured[0].clean_text == (
        "layer name | output size | 18-layer | 34-layer | 50-layer | "
        "101-layer | 152-layer"
    )
    assert structured[0].meta["columns"] == 7
    assert structured[0].meta["text_retention"] >= 0.9
    assert "3×3, 64 / 3×3, 64" in structured[3].clean_text
    assert result.stats["selective_table_regions"] == 1

    page_five_fast_tables = [
        chunk for chunk in table_chunks
        if chunk.page == 4 and chunk.meta.get("engine") == "fitz_table_lines"
    ]
    assert [chunk.clean_text for chunk in page_five_fast_tables] == [
        "plain ResNet",
        "18 layers 27.94 27.88",
        "34 layers 28.54 25.03",
    ]

    first_page = [chunk for chunk in result.chunks if chunk.page == 0]
    first_texts = [chunk.clean_text for chunk in first_page[:12]]
    assert first_page[0].kind == "text"
    assert first_texts[0] == "Deep Residual Learning for Image Recognition"
    assert "Abstract" in first_texts
    assert all("Abstract 20 20" not in text for text in first_texts)

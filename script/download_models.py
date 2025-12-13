#!/usr/bin/env python3
"""
预下载 DeepDoc 所需模型到仓库内的 resources/models 目录。

可通过环境变量覆盖：
  - MODEL_BASE_DIR：模型根目录，默认 <repo>/resources/models
  - HF_ENDPOINT：HuggingFace 镜像
"""

import os
from pathlib import Path
from typing import Optional

from huggingface_hub import hf_hub_download

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MODEL_BASE = Path(os.getenv("MODEL_BASE_DIR", PROJECT_ROOT / "resources" / "models"))

_OCR_BASE = MODEL_BASE / "ocr" / "PP-OCRv4"
OCR_FILES = [
    _OCR_BASE / "ch_PP-OCRv4_det_infer.onnx",
    _OCR_BASE / "ch_PP-OCRv4_rec_infer.onnx",
    _OCR_BASE / "PP-OCRv4" / "ch_PP-OCRv4_det_infer.onnx",
    _OCR_BASE / "PP-OCRv4" / "ch_PP-OCRv4_rec_infer.onnx",
]
LAYOUT_FILES = [
    MODEL_BASE / "layout" / "layout.onnx",
]
TABLE_FILES = [
    MODEL_BASE / "table" / "tsr.onnx",
]
XGB_FILES = [
    MODEL_BASE / "xgboost" / "updown_concat_xgb.model",
]


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def download_file(repo: str, filename: str, target: Path, subfolder: Optional[str] = None) -> str:
    ensure_dir(target)
    return hf_hub_download(
        repo_id=repo,
        filename=filename,
        subfolder=subfolder,
        local_dir=target,
        local_dir_use_symlinks=False,
        resume_download=True,
    )


def already_has(files: list[Path]) -> bool:
    return any(p.exists() for p in files)


def download_ocr():
    if already_has(OCR_FILES):
        print("OCR 模型已存在，跳过下载")
        return [str(p) for p in OCR_FILES if p.exists()]
    engine_dir = ensure_dir(_OCR_BASE)
    det = download_file("SWHL/RapidOCR", "ch_PP-OCRv4_det_infer.onnx", engine_dir, subfolder="PP-OCRv4")
    rec = download_file("SWHL/RapidOCR", "ch_PP-OCRv4_rec_infer.onnx", engine_dir, subfolder="PP-OCRv4")
    return [det, rec]


def download_layout_and_table():
    if already_has(LAYOUT_FILES + TABLE_FILES):
        print("布局/表格模型已存在，跳过下载")
        return [str(p) for p in LAYOUT_FILES + TABLE_FILES]
    layout_dir = ensure_dir(MODEL_BASE / "layout")
    table_dir = ensure_dir(MODEL_BASE / "table")
    layout = download_file("InfiniFlow/deepdoc", "layout.onnx", layout_dir)
    tsr = download_file("InfiniFlow/deepdoc", "tsr.onnx", table_dir)
    return [layout, tsr]


def download_xgboost():
    if already_has(XGB_FILES):
        print("XGBoost 模型已存在，跳过下载")
        return [str(p) for p in XGB_FILES]
    xgb_dir = ensure_dir(MODEL_BASE / "xgboost")
    xgb = download_file("InfiniFlow/text_concat_xgb_v1.0", "updown_concat_xgb.model", xgb_dir)
    return [xgb]


def main():
    print(f"模型根目录: {MODEL_BASE}")
    # 如果全部存在，直接退出
    if already_has(OCR_FILES + LAYOUT_FILES + TABLE_FILES + XGB_FILES):
        print("所有模型已存在，跳过下载。")
        return

    downloaded = {}
    try:
        downloaded["ocr"] = download_ocr()
        print("✓ OCR 模型就绪")
    except Exception as exc:
        print(f"✗ OCR 下载失败: {exc}")
    try:
        downloaded["layout/table"] = download_layout_and_table()
        print("✓ 布局与表格模型就绪")
    except Exception as exc:
        print(f"✗ 布局/表格下载失败: {exc}")
    try:
        downloaded["xgboost"] = download_xgboost()
        print("✓ 段落合并模型就绪")
    except Exception as exc:
        print(f"✗ XGBoost 下载失败: {exc}")

    print("\n完成情况：")
    for k, v in downloaded.items():
        print(f"- {k}: {len(v) if isinstance(v, list) else 0} 个文件")


if __name__ == "__main__":
    main()

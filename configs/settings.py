#
#  Copyright 2024 The InfiniFlow Authors. All Rights Reserved.
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

import os
from pathlib import Path

# ======== 模型路径配置 ========

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 允许通过环境变量覆盖，默认写到仓库内的 resources/models，便于镜像/挂载
MODEL_BASE_DIR = os.getenv(
    "MODEL_BASE_DIR",
    str(_PROJECT_ROOT / "resources" / "models"),
)

# OCR 模型路径
MODEL_OCR_PATH = os.path.join(MODEL_BASE_DIR, "ocr")

# 布局识别模型路径
MODEL_LAYOUT_PATH = os.path.join(MODEL_BASE_DIR, "layout")

# 表格识别模型路径
MODEL_TABLE_PATH = os.path.join(MODEL_BASE_DIR, "table")

# 确保模型目录存在
os.makedirs(MODEL_OCR_PATH, exist_ok=True)
os.makedirs(MODEL_LAYOUT_PATH, exist_ok=True)
os.makedirs(MODEL_TABLE_PATH, exist_ok=True)

# ======== Hugging Face 配置 ========

# Hugging Face 仓库配置
HF_MODEL_REPO = os.getenv("HF_MODEL_REPO", "InfiniFlow/deepdoc")

# Hugging Face 镜像站点配置（如果需要）
HF_ENDPOINT = os.getenv("HF_ENDPOINT", "https://huggingface.co")

# ======== OCR 配置 ========

# OCR 检测阈值
OCR_DET_THRESHOLD = float(os.getenv("OCR_DET_THRESHOLD", "0.3"))

# OCR 识别阈值
OCR_REC_THRESHOLD = float(os.getenv("OCR_REC_THRESHOLD", "0.5"))

# ======== PDF 处理配置 ========

# PDF DPI 设置
PDF_DPI = int(os.getenv("PDF_DPI", "200"))

# 是否启用轻量模式
LIGHTEN = int(os.getenv("LIGHTEN", "0"))

# ======== 资源路径配置 ========

# 资源根目录
RESOURCE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "resources")

# 分词器词典路径
TOKENIZER_DICT_PATH = os.path.join(RESOURCE_DIR, "data_parser", "qieci")

# ======== 并发配置 ========

# 并行设备数量（GPU）
PARALLEL_DEVICES = int(os.getenv("PARALLEL_DEVICES", "0"))

# ======== 日志配置 ========

# 日志级别
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# ======== 其他配置 ========

# 临时文件目录
TEMP_DIR = os.path.join(os.path.expanduser("~"), ".cache", "deepdoc", "temp")
os.makedirs(TEMP_DIR, exist_ok=True)

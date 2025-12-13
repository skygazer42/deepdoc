# DeepDoc

DeepDoc 是一个高级文档解析和智能提取库，专为 RAG（检索增强生成）场景设计。它支持多种文档格式的智能解析，集成了 OCR、版面识别、表格结构识别等 AI 能力。

## 主要功能

### 多格式文档解析

支持 8 种主流文档格式：

- **PDF**：OCR 文字识别、版面布局识别、表格提取、图表分析
- **Word (DOCX)**：段落、表格、样式提取
- **Excel/CSV**：智能表格解析、编码检测
- **PowerPoint (PPTX)**：幻灯片文本、表格提取
- **HTML**：主体内容提取（去除样式）
- **Markdown**：表格和结构提取
- **JSON**：智能分块（避免超大 JSON）
- **TXT**：分隔符智能分段

### 高级 PDF 处理能力

- **版面布局识别**：识别标题、正文、图片、表格、页眉页脚等 11 种版面元素（基于 YOLOv10）
- **OCR 文字识别**：支持 CPU 和 GPU (CUDA) 推理，基于 ONNX 模型优化
- **表格结构识别**：智能识别表格行列、单元格合并
- **视觉语言模型集成**：支持 VLM 图像理解，生成 Markdown 格式文档
- **段落智能合并**：使用 XGBoost 模型预测段落合并
- **异步并发处理**：使用 `trio` 库实现多页 OCR 并发

### RAG 优化特性

- **智能分块（Chunking）**：基于 Token 数量的智能分段
- **多语言编码支持**：支持 40+ 种字符编码自动检测
- **表格内容语义化**：将表格转为文本描述，便于向量化和检索

## 安装

### 基础安装

```bash
# 克隆仓库
git clone <repository-url>
cd deepdoc

# 安装依赖
pip install -r requirements.txt
```

### 可选依赖

如果需要 GPU 加速，请安装支持 CUDA 的 PyTorch：

```bash
# CUDA 11.8
pip install torch --index-url https://download.pytorch.org/whl/cu118

# CUDA 12.1
pip install torch --index-url https://download.pytorch.org/whl/cu121
```

### NLTK 数据下载

首次使用需要下载 NLTK 数据：

```python
import nltk
nltk.download('punkt')
nltk.download('wordnet')
```

### 词典文件

分词器需要词典文件。首次运行时，程序会尝试从 HuggingFace 自动下载模型文件。

如果需要自定义词典，请将词典文件放置在：
```
resources/data_parser/qieci.txt
```

词典格式：每行包含 `词汇 频率 词性`，用空格或制表符分隔。

## 快速开始

### PDF 解析

```python
from deepdoc.parser import PdfParser

# 创建 PDF 解析器
parser = PdfParser()

# 解析 PDF 文件
chunks = parser("document.pdf", need_image=True)

# 遍历结果
for chunk in chunks:
    print(chunk)
```

### Word 文档解析

```python
from deepdoc.parser import DocxParser

# 创建 DOCX 解析器
docx_parser = DocxParser()

# 解析 Word 文档
sections = docx_parser("document.docx")

# 输出内容
for section in sections:
    print(section)
```

### Excel 解析

```python
from deepdoc.parser import ExcelParser

# 创建 Excel 解析器
excel_parser = ExcelParser()

# 解析 Excel 文件
data = excel_parser("data.xlsx")

# 处理数据
for row in data:
    print(row)
```

### 其他格式

```python
from deepdoc.parser import (
    PptParser,      # PowerPoint
    HtmlParser,     # HTML
    MarkdownParser, # Markdown
    JsonParser,     # JSON
    TxtParser       # 文本
)

# 使用方式类似
ppt_parser = PptParser()
result = ppt_parser("presentation.pptx")
```

## 配置

项目配置文件位于 `configs/settings.py`，主要配置项包括：

```python
# 模型存储路径（默认：~/.cache/deepdoc/models）
MODEL_BASE_DIR = "~/.cache/deepdoc/models"

# OCR 检测阈值（默认：0.3）
OCR_DET_THRESHOLD = 0.3

# PDF DPI 设置（默认：200）
PDF_DPI = 200

# Hugging Face 镜像站点（可选）
HF_ENDPOINT = "https://huggingface.co"
```

可以通过环境变量覆盖配置：

```bash
export OCR_DET_THRESHOLD=0.5
export PDF_DPI=300
export HF_ENDPOINT="https://hf-mirror.com"
```

## 项目结构

```
deepdoc/
├── parser/              # 文档解析器模块
│   ├── pdf_parser.py    # PDF 解析器
│   ├── docx_parser.py   # Word 解析器
│   ├── excel_parser.py  # Excel 解析器
│   └── ...
├── vision/              # 视觉识别模块
│   ├── ocr.py          # OCR 引擎
│   ├── layout_recognizer.py    # 版面识别
│   ├── table_structure_recognizer.py  # 表格识别
│   └── ...
├── src/
│   └── model/
│       └── rag_tokenizer.py  # 中文分词器
├── configs/
│   └── settings.py     # 配置文件
├── data/               # 示例数据
├── requirements.txt    # 依赖清单
└── README.md          # 项目说明
```

## 依赖说明

核心依赖：

- **文档处理**：pdfplumber, python-docx, openpyxl, python-pptx
- **AI 推理**：onnxruntime, xgboost, huggingface-hub
- **视觉处理**：opencv-python, Pillow, rapidocr-onnxruntime
- **NLP**：tiktoken, datrie, hanziconv, nltk
- **其他**：beartype, trio, chardet

完整依赖列表请查看 `requirements.txt`。

## 模型下载

首次运行时，程序会自动从 HuggingFace 下载所需模型：

- **OCR 模型**：来自 `SWHL/RapidOCR`
- **视觉模型**：来自 `InfiniFlow/deepdoc`
- **段落合并模型**：来自 `InfiniFlow/text_concat_xgb_v1.0`

模型会缓存在 `~/.cache/deepdoc/models/` 目录下。

如果网络不稳定，可以设置镜像站：

```bash
export HF_ENDPOINT="https://hf-mirror.com"
```

## 常见问题

### 1. 导入错误

如果遇到 `ModuleNotFoundError`，请确保：
- 已安装所有依赖：`pip install -r requirements.txt`
- 在项目根目录运行代码

### 2. OCR 识别率低

可以调整检测阈值：
```python
# 在 configs/settings.py 中修改
OCR_DET_THRESHOLD = 0.2  # 降低阈值提高召回率
```

### 3. PDF 处理慢

- 启用 GPU 加速（安装 CUDA 版 PyTorch）
- 降低 DPI 设置：`PDF_DPI = 150`
- 使用轻量模式：`LIGHTEN = 1`

### 4. 内存不足

处理大文件时可能内存不足，建议：
- 分批处理
- 减少并发数
- 关闭不必要的功能（如图像提取）

## 许可证

本项目采用 Apache License 2.0 许可证。详见 [LICENSE](LICENSE) 文件。

## 版权

Copyright 2024-2025 The InfiniFlow Authors. All Rights Reserved.

## 贡献

欢迎贡献代码、报告问题或提出建议！

## 致谢

本项目使用了以下开源项目：

- [RapidOCR](https://github.com/RapidAI/RapidOCR) - OCR 引擎
- [pdfplumber](https://github.com/jsvine/pdfplumber) - PDF 解析
- [YOLOv10](https://github.com/THU-MIG/yolov10) - 版面识别

---

**注意**：本项目仍在积极开发中，API 可能会有变化。

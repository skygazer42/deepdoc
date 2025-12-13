# DeepDoc 功能分析报告

## 测试总结

**测试日期**: 2025-12-11
**项目版本**: 0.1.0
**测试环境**: Python 3.10.19 (CPU only)

---

## 核心功能

### 1. 三种 PDF 解析器

#### 1.1 PlainParser（快速文本提取器）
- **用途**: 快速提取有文本层的 PDF
- **优点**: 速度快，无需模型
- **缺点**: 只能处理有文本层的 PDF，**无法处理扫描件**
- **测试结果**: ✓ 成功运行，但示例 PDF 是图片格式，提取为空

#### 1.2 RAGFlowPdfParser（完整解析器）⭐
- **用途**: 完整的 PDF 解析，支持 OCR + 版面识别 + 表格识别
- **核心能力**:
  - OCR 文字识别（支持 CPU/GPU）
  - 版面布局识别（11 种元素）
  - 表格结构识别
  - 段落智能合并（XGBoost 模型）
  - 异步并发处理（多页 OCR）
- **依赖模型**:
  - OCR 检测/识别模型（ONNX）
  - 版面识别模型（YOLOv10）
  - 表格识别模型
  - 段落合并模型（XGBoost）
- **测试状态**: ⚠️ 需要下载模型（约 100MB+）

#### 1.3 VisionParser（视觉语言模型解析器）⭐⭐
- **用途**: 使用 VLM（如 GPT-4V、Qwen-VL）直接理解 PDF 页面
- **类似项目**: **MinerU**
- **核心能力**:
  - 直接将 PDF 页面转为 Markdown
  - 保持原始版面结构
  - 理解复杂布局（图表、公式等）
  - 支持中英文双语
- **优点**: 准确度高，Markdown 格式好
- **缺点**: 需要调用 VLM API（成本较高）
- **使用示例**:
  ```python
  from deepdoc.parser.pdf_parser import VisionParser

  # 传入你的 VLM 模型实例
  parser = VisionParser(vision_model=your_vlm_model)
  docs, images = parser("document.pdf")
  ```

---

## 版面识别能力

### LayoutRecognizer - 版面布局识别器

**识别元素（11 种）**:
1. `_background_` - 背景
2. `Text` - 正文
3. `Title` - 标题
4. `Figure` - 图片
5. `Figure caption` - 图片标题
6. `Table` - 表格
7. `Table caption` - 表格标题
8. `Header` - 页眉
9. `Footer` - 页脚
10. `Reference` - 参考文献
11. `Equation` - 公式

**特点**:
- 基于 YOLOv10 深度学习模型
- 自动过滤垃圾内容（页眉页脚、参考文献）
- 支持批量处理
- 可自定义阈值

---

## Markdown 生成能力

### VisionParser 的 Markdown 生成

**Prompt 设计**（支持中英文）:
- 严格按照图像内容转录
- 保持原始语言和结构
- 不生成示例或模板
- 支持分页标识
- 自动识别表格、列表、标题等结构

**生成的 Markdown 特点**:
- ✓ 保持原始版面布局
- ✓ 准确识别表格结构
- ✓ 识别标题层级
- ✓ 保留列表格式
- ✓ 添加分页标识（`--- Page X ---`）

**与 MinerU 对比**:
| 功能 | DeepDoc VisionParser | MinerU |
|------|---------------------|--------|
| 版面识别 | ✓ | ✓ |
| OCR 识别 | ✓ | ✓ |
| Markdown 输出 | ✓ | ✓ |
| VLM 支持 | ✓ | ✓ |
| 表格识别 | ✓ | ✓ |
| 批量处理 | ✓ | ✓ |
| 开源 | ✓ | ✓ |

---

## 使用场景

### RAGFlowPdfParser 适用场景
1. **RAG 知识库构建**: 智能分块，保留语义
2. **文档批量处理**: 异步并发，速度快
3. **表格数据提取**: 准确识别表格结构
4. **多语言文档**: 支持中英文混合

### VisionParser 适用场景
1. **复杂版面文档**: 学术论文、技术手册
2. **高质量 Markdown 需求**: 需要保持原始格式
3. **多模态内容**: 图表、公式、表格混合
4. **类 MinerU 需求**: 替代 MinerU 的开源方案

---

## 测试结论

### ✅ 已验证的功能
1. ✓ 模块导入成功
2. ✓ PlainParser 可运行（适合有文本层的 PDF）
3. ✓ 配置文件正常工作
4. ✓ 项目结构完整

### ⚠️ 需要额外配置的功能
1. **RAGFlowPdfParser**:
   - 需要下载 OCR 模型（ONNX）
   - 需要下载版面识别模型
   - 需要下载表格识别模型
   - 需要分词词典（可选）

2. **VisionParser**:
   - 需要配置 VLM 模型（GPT-4V、Qwen-VL 等）
   - 需要 API 密钥

### 🎯 核心优势
1. **功能全面**: 三种解析器满足不同需求
2. **类 MinerU 能力**: VisionParser 提供类似功能
3. **RAG 优化**: 专为检索增强生成设计
4. **开源免费**: Apache 2.0 许可证

---

## 对比图片 PDF 处理能力

**示例 PDF 分析** (`picture.pdf`):
- 文件大小: 1.1 MB
- 类型: 图片 PDF（扫描件）
- PlainParser 结果: 无文本（需要 OCR）

**推荐方案**:
1. **基础需求**: 使用 `RAGFlowPdfParser` + OCR
2. **高质量需求**: 使用 `VisionParser` + VLM（类似 MinerU）

---

## 下一步操作

### 完整测试 RAGFlowPdfParser
1. 下载模型:
   ```python
   from deepdoc.parser import PdfParser
   parser = PdfParser()  # 首次运行会自动下载
   ```

2. 解析 PDF:
   ```python
   chunks = parser("picture.pdf", need_image=True)
   ```

### 使用 VisionParser（类 MinerU）
1. 配置 VLM 模型
2. 创建解析器:
   ```python
   from deepdoc.parser.pdf_parser import VisionParser
   parser = VisionParser(vision_model=your_model)
   docs, _ = parser("picture.pdf")
   ```

---

## 总结

**DeepDoc 是一个功能强大的文档解析库，特别适合 RAG 场景。VisionParser 提供了类似 MinerU 的版面匹配和 Markdown 生成能力，支持高质量的图片 PDF 识别。**

主要限制：
- 需要下载较大的模型文件
- VisionParser 需要 VLM API 支持
- 首次运行需要联网下载模型

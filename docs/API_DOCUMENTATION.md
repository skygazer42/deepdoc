# DeepDoc API 文档解析服务

## 快速开始

### 使用 Docker Compose 启动服务

```bash
# 1. 构建并启动服务
docker-compose up -d

# 2. 查看日志
docker-compose logs -f

# 3. 检查服务状态
curl http://localhost:8000/health

# 4. 访问 API 文档
# 打开浏览器访问: http://localhost:8000/docs
```

### 停止服务

```bash
docker-compose down

# 如果要删除模型缓存卷
docker-compose down -v
```

---

## API 接口说明

### 1. 健康检查

**GET /**

返回服务状态和支持的文件格式。

```bash
curl http://localhost:8000/
```

**响应示例:**
```json
{
  "status": "healthy",
  "version": "1.0.0",
  "supported_formats": [".pdf", ".docx", ".xlsx", ".pptx", ".html", ".txt"]
}
```

---

### 2. 文档解析（完整版）

**POST /parse**

解析上传的文档文件。

**请求参数:**
- `file` (必需): 上传的文档文件
- `use_ocr` (可选): 是否使用 OCR，默认 `true`（仅对 PDF 有效）
- `need_image` (可选): 是否提取图像，默认 `false`（仅对 PDF 有效）
- `zoomin` (可选): DPI 缩放倍数，默认 `3`（仅对 PDF 有效）
- `max_chunks` (可选): 最大返回块数，默认 `100`

**cURL 示例:**

```bash
# 解析 PDF 文件（使用 OCR）
curl -X POST "http://localhost:8000/parse" \
  -F "file=@document.pdf" \
  -F "use_ocr=true" \
  -F "need_image=false" \
  -F "zoomin=3" \
  -F "max_chunks=100"

# 解析 Word 文件
curl -X POST "http://localhost:8000/parse" \
  -F "file=@document.docx"

# 解析 Excel 文件
curl -X POST "http://localhost:8000/parse" \
  -F "file=@data.xlsx"
```

**Python 示例:**

```python
import requests

url = "http://localhost:8000/parse"

# 上传 PDF 文件
with open("document.pdf", "rb") as f:
    files = {"file": f}
    data = {
        "use_ocr": True,
        "need_image": False,
        "zoomin": 3,
        "max_chunks": 100
    }
    response = requests.post(url, files=files, data=data)
    result = response.json()

print(f"解析成功: {result['success']}")
print(f"总块数: {result['total_chunks']}")
for i, chunk in enumerate(result['chunks'][:5]):
    print(f"\n块 {i+1}:")
    print(chunk)
```

**响应示例:**
```json
{
  "success": true,
  "file_name": "document.pdf",
  "file_type": ".pdf",
  "total_chunks": 50,
  "chunks": [
    {
      "text": "这是第一段文本...",
      "index": 0
    },
    {
      "text": "这是第二段文本...",
      "index": 1
    }
  ],
  "error": null
}
```

---

### 3. 文档解析（简化版）

**POST /parse/simple**

使用默认参数解析文档的简化接口。

**请求参数:**
- `file` (必需): 上传的文档文件

**cURL 示例:**

```bash
curl -X POST "http://localhost:8000/parse/simple" \
  -F "file=@document.pdf"
```

**Python 示例:**

```python
import requests

url = "http://localhost:8000/parse/simple"

with open("document.pdf", "rb") as f:
    files = {"file": f}
    response = requests.post(url, files=files)
    result = response.json()

print(result)
```

---

### 4. 获取支持的文件格式

**GET /supported_formats**

返回所有支持的文件格式列表。

```bash
curl http://localhost:8000/supported_formats
```

**响应示例:**
```json
{
  "formats": [".pdf", ".docx", ".doc", ".xlsx", ".xls", ".csv", ".pptx", ".ppt", ".html", ".htm", ".txt", ".md"],
  "count": 12
}
```

---

## 支持的文件格式

| 文件类型 | 扩展名 | 说明 |
|---------|--------|------|
| PDF | `.pdf` | 支持 OCR、版面识别、表格提取 |
| Word | `.docx`, `.doc` | 提取文本、表格、样式 |
| Excel | `.xlsx`, `.xls`, `.csv` | 表格数据提取 |
| PowerPoint | `.pptx`, `.ppt` | 幻灯片文本提取 |
| HTML | `.html`, `.htm` | 主体内容提取 |
| 文本 | `.txt`, `.md` | 纯文本提取 |

---

## 参数说明

### PDF 解析参数

#### `use_ocr` (bool)
- **默认**: `true`
- **说明**: 是否使用 OCR 识别图片 PDF
- **适用**: 扫描件或图片 PDF
- **建议**: 有文本层的 PDF 可设为 `false` 加快速度

#### `need_image` (bool)
- **默认**: `false`
- **说明**: 是否提取 PDF 中的图像
- **注意**: 设为 `true` 会增加响应大小

#### `zoomin` (int)
- **默认**: `3`
- **范围**: 1-5
- **说明**: DPI 缩放倍数，值越大识别越准确但速度越慢
- **建议**:
  - 低分辨率 PDF: `4-5`
  - 正常 PDF: `3`
  - 高质量 PDF: `2`

#### `max_chunks` (int)
- **默认**: `100`
- **说明**: 最大返回的文本块数量
- **建议**: 根据需求调整，避免响应过大

---

## 环境变量配置

在 `docker-compose.yaml` 中可配置以下环境变量：

```yaml
environment:
  # 服务配置
  - PORT=8000                      # API 端口
  - HOST=0.0.0.0                   # 监听地址

  # OCR 配置
  - OCR_DET_THRESHOLD=0.3          # OCR 检测阈值（0-1）
  - OCR_REC_THRESHOLD=0.5          # OCR 识别阈值（0-1）

  # PDF 配置
  - PDF_DPI=200                    # PDF 渲染 DPI
  - LIGHTEN=0                      # 是否启用轻量模式（0/1）

  # HuggingFace 配置
  - HF_ENDPOINT=https://huggingface.co  # HF 镜像地址
```

---

## 性能优化

### 资源限制

在 `docker-compose.yaml` 中调整资源限制：

```yaml
deploy:
  resources:
    limits:
      cpus: '4.0'      # 最大 CPU 核心数
      memory: 8G       # 最大内存
    reservations:
      cpus: '2.0'      # 保留 CPU 核心数
      memory: 4G       # 保留内存
```

### 模型缓存

首次运行时会自动下载模型到 Docker 卷 `deepdoc-models` 中：
- OCR 模型: ~100MB
- 版面识别模型: ~50MB
- 表格识别模型: ~30MB
- 段落合并模型: ~10MB

**总计**: 约 200MB

### 性能建议

1. **小文件 (< 10MB)**:
   - 并发请求: 4-8
   - 响应时间: 1-5 秒

2. **中等文件 (10-50MB)**:
   - 并发请求: 2-4
   - 响应时间: 5-30 秒

3. **大文件 (> 50MB)**:
   - 并发请求: 1-2
   - 响应时间: 30 秒 - 数分钟

---

## 错误处理

### 常见错误

#### 1. 文件类型不支持

```json
{
  "success": false,
  "error": "不支持的文件类型: .exe"
}
```

**解决**: 检查文件格式是否在支持列表中。

#### 2. 文件过大

```json
{
  "success": false,
  "error": "File too large"
}
```

**解决**: 调整 FastAPI 的文件大小限制或分割文件。

#### 3. OCR 模型未下载

```json
{
  "success": false,
  "error": "not find model file path ..."
}
```

**解决**:
```bash
# 进入容器下载模型
docker-compose exec deepdoc-api python script/download_models.py
```

---

## 开发和调试

### 查看日志

```bash
# 实时查看日志
docker-compose logs -f deepdoc-api

# 查看最近 100 行日志
docker-compose logs --tail=100 deepdoc-api
```

### 进入容器

```bash
docker-compose exec deepdoc-api bash

# 在容器内运行 Python
python
>>> from deepdoc.parser import PdfParser
>>> parser = PdfParser()
```

### 手动下载模型

```bash
# 在容器内运行
docker-compose exec deepdoc-api python script/download_models.py
```

---

## 生产部署建议

### 1. 使用反向代理

配置 Nginx 作为反向代理：

```nginx
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;

        # 增加超时时间（处理大文件）
        proxy_read_timeout 300s;
        proxy_connect_timeout 300s;

        # 增加上传大小限制
        client_max_body_size 100M;
    }
}
```

### 2. 添加身份验证

在 API 服务前添加身份验证层（如 API Key）。

### 3. 监控和日志

- 使用 Prometheus + Grafana 监控
- 配置日志聚合（如 ELK Stack）

### 4. 水平扩展

使用负载均衡器（如 Nginx）配合多个容器实例：

```yaml
# docker-compose.yaml
services:
  deepdoc-api:
    deploy:
      replicas: 3  # 启动 3 个实例
```

---

## 故障排查

### 服务无法启动

1. 检查端口占用:
   ```bash
   lsof -i :8000
   ```

2. 查看日志:
   ```bash
   docker-compose logs deepdoc-api
   ```

### 解析速度慢

1. 减少 `zoomin` 值
2. 增加 CPU 核心数
3. 使用 `use_ocr=false`（如果 PDF 有文本层）

### 内存不足

1. 增加 Docker 内存限制
2. 减少并发请求
3. 启用 `LIGHTEN=1` 轻量模式

---

## 许可证

Apache License 2.0

---

## 支持和反馈

- GitHub Issues: [项目地址]
- 文档: [README.md](README.md)
- 功能分析: [FEATURE_ANALYSIS.md](FEATURE_ANALYSIS.md)

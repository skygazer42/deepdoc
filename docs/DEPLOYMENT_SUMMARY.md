# DeepDoc Docker 部署总结

## ✅ 已完成的工作

### 1. 项目修复
- ✓ 修复了所有代码导入错误
- ✓ 创建了缺失的配置文件
- ✓ 添加了完整的项目文档

### 2. API 服务
- ✓ 创建了 FastAPI 文档解析服务 (`api_service.py`)
- ✓ 支持 6 种文档格式（PDF、Word、Excel、PPT、HTML、TXT）
- ✓ 提供 RESTful API 接口
- ✓ 自动生成 API 文档（Swagger UI）

### 3. Docker 化
- ✓ 创建了 Dockerfile
- ✓ 创建了 docker-compose.yaml
- ✓ 配置了模型缓存卷
- ✓ 添加了健康检查
- ✓ 配置了资源限制

### 4. 文档和工具
- ✓ API 使用文档 (`API_DOCUMENTATION.md`)
- ✓ Docker 部署指南 (`DOCKER_GUIDE.md`)
- ✓ 快速启动指南 (`QUICKSTART.md`)
- ✓ 功能分析报告 (`FEATURE_ANALYSIS.md`)
- ✓ 模型下载脚本 (`script/download_models.py`)
- ✓ API 测试脚本 (`test_api.sh`)

---

## 📁 项目文件结构

```
deepdoc/
├── api_service.py              # FastAPI 服务主文件
├── script/
│   ├── download_models.py      # 模型下载脚本
│   └── entrypoint.sh           # 容器启动脚本
├── test_api.sh                 # API 测试脚本
├── Dockerfile                  # Docker 镜像构建文件
├── docker-compose.yaml         # Docker Compose 配置
├── .dockerignore               # Docker 忽略文件
├── requirements.txt            # Python 依赖
├── setup.py                    # 安装脚本
├── LICENSE                     # 许可证
├── .gitignore                  # Git 忽略文件
│
├── README.md                   # 项目说明
├── QUICKSTART.md              # 快速启动指南 ⭐
├── API_DOCUMENTATION.md       # API 文档 ⭐
├── DOCKER_GUIDE.md            # Docker 详细指南 ⭐
├── FEATURE_ANALYSIS.md        # 功能分析
├── DEPLOYMENT_SUMMARY.md      # 本文件
│
├── configs/                    # 配置模块
│   ├── __init__.py
│   └── settings.py
│
├── src/                        # 源代码
│   ├── __init__.py
│   └── model/
│       ├── __init__.py
│       └── rag_tokenizer.py   # 分词器
│
├── parser/                     # 文档解析器
│   ├── __init__.py
│   ├── pdf_parser.py          # PDF 解析
│   ├── docx_parser.py         # Word 解析
│   ├── excel_parser.py        # Excel 解析
│   ├── ppt_parser.py          # PPT 解析
│   ├── html_parser.py         # HTML 解析
│   ├── txt_parser.py          # 文本解析
│   └── ...
│
├── vision/                     # 视觉识别
│   ├── __init__.py
│   ├── ocr.py                 # OCR 引擎
│   ├── layout_recognizer.py   # 版面识别
│   ├── table_structure_recognizer.py  # 表格识别
│   └── ...
│
└── data/                       # 示例数据
    ├── picture.pdf
    ├── exmaple.docx
    ├── exmaple.xlsx
    └── random_data.csv
```

---

## 🚀 快速启动

### 方法 1: Docker Compose（推荐）

```bash
# 1. 进入项目目录
cd /data/temp37/deepdoc

# 2. 构建并启动
docker-compose up -d

# 3. 查看日志
docker-compose logs -f

# 4. 测试服务
curl http://localhost:8000/health
```

### 方法 2: 手动 Docker

```bash
# 1. 构建镜像
docker build -t deepdoc:latest .

# 2. 运行容器
docker run -d \
  --name deepdoc-api \
  -p 8000:8000 \
  -v deepdoc-models:/root/.cache/deepdoc/models \
  deepdoc:latest

# 3. 查看日志
docker logs -f deepdoc-api
```

---

## 📖 API 接口

### 1. 健康检查

```bash
GET http://localhost:8000/health
```

### 2. 解析文档（简化版）

```bash
POST http://localhost:8000/parse/simple
Content-Type: multipart/form-data

file: <文件>
```

**示例:**
```bash
curl -X POST "http://localhost:8000/parse/simple" \
  -F "file=@document.pdf"
```

### 3. 解析文档（完整版）

```bash
POST http://localhost:8000/parse
Content-Type: multipart/form-data

file: <文件>
use_ocr: true/false         # 是否使用 OCR
need_image: true/false      # 是否提取图像
zoomin: 1-5                 # DPI 缩放
max_chunks: <数量>          # 最大返回块数
```

**示例:**
```bash
curl -X POST "http://localhost:8000/parse" \
  -F "file=@document.pdf" \
  -F "use_ocr=true" \
  -F "need_image=false" \
  -F "zoomin=3" \
  -F "max_chunks=100"
```

### 4. 获取支持格式

```bash
GET http://localhost:8000/supported_formats
```

---

## 🌐 访问地址

| 服务 | 地址 | 说明 |
|------|------|------|
| API 服务 | http://localhost:8000 | 主服务端点 |
| API 文档 | http://localhost:8000/docs | Swagger UI 文档 |
| 健康检查 | http://localhost:8000/health | 服务状态检查 |

---

## 📦 模型下载

### 自动下载（推荐）

首次启动服务时会自动下载模型：

```bash
docker-compose up -d
# 等待 5-10 分钟，模型会自动下载
```

### 手动下载

如果自动下载失败，可手动下载：

```bash
# 进入容器
docker-compose exec deepdoc-api bash

# 运行下载脚本
python script/download_models.py
```

### 所需模型

| 模型 | 大小 | 用途 |
|------|------|------|
| OCR 检测/识别模型 | ~100MB | 文字识别 |
| 版面识别模型 | ~50MB | 布局识别 |
| 表格识别模型 | ~30MB | 表格提取 |
| 段落合并模型 | ~10MB | 文本合并 |
| **总计** | **~200MB** | - |

---

## 🔧 配置选项

### 环境变量

在 `docker-compose.yaml` 或 `.env` 文件中配置：

```bash
# 服务配置
PORT=8000                       # API 端口
HOST=0.0.0.0                    # 监听地址

# OCR 配置
OCR_DET_THRESHOLD=0.3           # 检测阈值
OCR_REC_THRESHOLD=0.5           # 识别阈值

# PDF 配置
PDF_DPI=200                     # 渲染 DPI
LIGHTEN=0                       # 轻量模式（0/1）

# HuggingFace 配置
HF_ENDPOINT=https://huggingface.co  # 镜像地址
```

### 资源限制

在 `docker-compose.yaml` 中调整：

```yaml
deploy:
  resources:
    limits:
      cpus: '4.0'       # 最大 CPU
      memory: 8G        # 最大内存
```

---

## 📊 性能建议

### 推荐配置

| 场景 | CPU | 内存 | 并发 | 响应时间 |
|------|-----|------|------|----------|
| 开发环境 | 2 核 | 4GB | 1-2 | 10-30秒 |
| 生产环境 | 4 核 | 8GB | 4-8 | 5-15秒 |
| 高负载 | 8 核 | 16GB | 8-16 | 2-10秒 |

### 优化建议

1. **小文件 (< 10MB)**: 使用默认配置
2. **大文件 (> 50MB)**: 增加超时时间和内存
3. **图片 PDF**: 使用 `use_ocr=true` 和 `zoomin=3`
4. **有文本层的 PDF**: 使用 `use_ocr=false` 加快速度

---

## 🛠️ 常用命令

### Docker Compose

```bash
# 启动服务
docker-compose up -d

# 查看状态
docker-compose ps

# 查看日志
docker-compose logs -f

# 重启服务
docker-compose restart

# 停止服务
docker-compose stop

# 完全清理
docker-compose down -v
```

### Docker

```bash
# 查看容器
docker ps

# 进入容器
docker-compose exec deepdoc-api bash

# 查看日志
docker logs -f deepdoc-api

# 重启容器
docker restart deepdoc-api
```

---

## 🐛 故障排查

### 1. 容器无法启动

```bash
# 查看日志
docker-compose logs deepdoc-api

# 检查配置
docker-compose config
```

### 2. 模型下载失败

```bash
# 手动下载
docker-compose exec deepdoc-api python script/download_models.py
```

### 3. 端口冲突

```bash
# 修改 docker-compose.yaml 中的端口
ports:
  - "9000:8000"  # 使用 9000 端口
```

### 4. 内存不足

```bash
# 增加 Docker 内存限制
# 编辑 docker-compose.yaml
memory: 16G
```

---

## 📚 文档索引

| 文档 | 说明 |
|------|------|
| [QUICKSTART.md](QUICKSTART.md) | 3 步快速启动 ⭐ |
| [API_DOCUMENTATION.md](API_DOCUMENTATION.md) | API 接口详细文档 ⭐ |
| [DOCKER_GUIDE.md](DOCKER_GUIDE.md) | Docker 完整指南 ⭐ |
| [README.md](README.md) | 项目介绍和安装 |
| [FEATURE_ANALYSIS.md](FEATURE_ANALYSIS.md) | 功能分析报告 |
| [LICENSE](LICENSE) | Apache 2.0 许可证 |

---

## 🎯 核心特性

### 支持的文档格式

- ✓ PDF（支持 OCR、版面识别、表格提取）
- ✓ Word（.docx, .doc）
- ✓ Excel（.xlsx, .xls, .csv）
- ✓ PowerPoint（.pptx, .ppt）
- ✓ HTML（.html, .htm）
- ✓ 文本（.txt, .md）

### 核心能力

- ✓ OCR 文字识别（CPU/GPU）
- ✓ 版面布局识别（11 种元素）
- ✓ 表格结构识别
- ✓ 段落智能合并
- ✓ 异步并发处理
- ✓ RESTful API
- ✓ Swagger 文档

---

## 🔗 下一步

1. **启动服务**: 按照 [QUICKSTART.md](QUICKSTART.md) 启动
2. **测试 API**: 使用 `test_api.sh` 或访问 http://localhost:8000/docs
3. **集成应用**: 参考 [API_DOCUMENTATION.md](API_DOCUMENTATION.md)
4. **生产部署**: 参考 [DOCKER_GUIDE.md](DOCKER_GUIDE.md)

---

## 📞 支持

- GitHub: https://github.com/infiniflow/deepdoc
- 文档: 查看项目根目录的 Markdown 文件
- Issues: 在 GitHub 提交问题

---

## 📝 许可证

Apache License 2.0

---

## 🙏 致谢

- RapidOCR - OCR 引擎
- pdfplumber - PDF 解析
- FastAPI - Web 框架
- YOLOv10 - 版面识别

---

**部署完成！现在可以通过 `docker-compose up -d` 启动服务了！** 🎉

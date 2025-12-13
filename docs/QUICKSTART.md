# DeepDoc 快速启动指南

## 🚀 3 步启动服务

### 步骤 1: 构建镜像

```bash
cd /data/temp37/deepdoc
docker-compose build
```

### 步骤 2: 启动服务

```bash
docker-compose up -d
```

服务将在后台启动，首次运行会自动下载模型（约 200MB）。

### 步骤 3: 测试服务

```bash
# 健康检查
curl http://localhost:8000/health

# 查看 API 文档（浏览器访问）
open http://localhost:8000/docs
```

---

## 📝 使用示例

### cURL 示例

```bash
# 解析 PDF 文件
curl -X POST "http://localhost:8000/parse/simple" \
  -F "file=@document.pdf"

# 解析 Word 文件
curl -X POST "http://localhost:8000/parse/simple" \
  -F "file=@document.docx"
```

### Python 示例

```python
import requests

# 上传并解析文件
url = "http://localhost:8000/parse/simple"
with open("document.pdf", "rb") as f:
    response = requests.post(url, files={"file": f})
    result = response.json()

print(f"成功: {result['success']}")
print(f"总块数: {result['total_chunks']}")
```

---

## 📚 文档

- **API 文档**: [API_DOCUMENTATION.md](API_DOCUMENTATION.md)
- **Docker 指南**: [DOCKER_GUIDE.md](DOCKER_GUIDE.md)
- **项目文档**: [README.md](README.md)
- **功能分析**: [FEATURE_ANALYSIS.md](FEATURE_ANALYSIS.md)

---

## 🛠️ 常用命令

```bash
# 查看日志
docker-compose logs -f

# 停止服务
docker-compose stop

# 重启服务
docker-compose restart

# 完全清理（包括模型缓存）
docker-compose down -v
```

---

## 🌐 访问地址

- API 服务: http://localhost:8000
- API 文档: http://localhost:8000/docs
- 健康检查: http://localhost:8000/health

---

## ✅ 支持的文件格式

- PDF (`.pdf`) - 支持 OCR
- Word (`.docx`, `.doc`)
- Excel (`.xlsx`, `.xls`, `.csv`)
- PowerPoint (`.pptx`, `.ppt`)
- HTML (`.html`, `.htm`)
- 文本 (`.txt`, `.md`)

---

## 💡 提示

1. **首次启动较慢**: 需要下载模型（约 5-10 分钟）
2. **模型缓存**: 模型会缓存在 Docker 卷中，后续启动很快
3. **网络问题**: 如果模型下载失败，可进入容器手动下载:
   ```bash
   docker-compose exec deepdoc-api python script/download_models.py
   ```

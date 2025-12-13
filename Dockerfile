# DeepDoc 文档解析服务 Dockerfile
FROM python:3.10-slim

# 设置工作目录
WORKDIR /app

# 设置环境变量
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DEBIAN_FRONTEND=noninteractive \
    MODEL_BASE_DIR=/app/resources/models \
    NLTK_DATA=/app/resources/nltk_data \
    PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple \
    PIP_TRUSTED_HOST=mirrors.aliyun.com

# 使用阿里云 Debian 镜像源（兼容新版 trixie 的 DEB822 格式）
RUN if [ -f /etc/apt/sources.list ]; then \
        sed -i 's@deb.debian.org@mirrors.aliyun.com@g;s@security.debian.org@mirrors.aliyun.com@g' /etc/apt/sources.list; \
    elif [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        sed -i 's@deb.debian.org@mirrors.aliyun.com@g' /etc/apt/sources.list.d/debian.sources; \
    fi && \
    apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgomp1 \
    wget \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 复制项目文件
COPY . /app/

# 安装 Python 依赖
RUN pip install --upgrade pip && \
    pip install -r requirements.txt && \
    pip install fastapi uvicorn python-multipart && \
    pip install -e .

# 创建模型目录（与应用内一致，便于挂载）
RUN mkdir -p /app/resources/models

# 启动前预下载模型（可失败，启动时仍可按需下载）
RUN chmod +x /app/script/entrypoint.sh

# 下载模型（可选，可以在运行时下载）
# RUN python script/download_models.py

# 暴露端口
EXPOSE 8000

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# 启动命令
CMD ["/app/script/entrypoint.sh"]

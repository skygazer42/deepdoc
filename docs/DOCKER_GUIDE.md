# DeepDoc Docker 部署指南

## 快速启动（3 步）

### 1. 构建镜像

```bash
cd /data/temp37/deepdoc
docker-compose build
```

**预计时间**: 5-10 分钟

### 2. 启动服务

```bash
docker-compose up -d
```

**说明**:
- 首次启动会自动下载模型（约 200MB）
- 模型会缓存在 Docker 卷中，后续启动无需重新下载

### 3. 测试服务

```bash
# 健康检查
curl http://localhost:8000/health

# 查看 API 文档
# 浏览器访问: http://localhost:8000/docs

# 运行测试脚本
./test_api.sh
```

---

## 完整操作命令

### 构建和启动

```bash
# 构建镜像
docker-compose build

# 启动服务（后台运行）
docker-compose up -d

# 启动服务（前台运行，查看日志）
docker-compose up

# 重新构建并启动
docker-compose up -d --build
```

### 查看状态

```bash
# 查看运行状态
docker-compose ps

# 查看日志
docker-compose logs -f

# 查看最近 100 行日志
docker-compose logs --tail=100
```

### 停止和清理

```bash
# 停止服务
docker-compose stop

# 停止并删除容器
docker-compose down

# 停止并删除容器、网络、卷（包括模型缓存）
docker-compose down -v

# 删除镜像
docker-compose down --rmi all
```

### 进入容器

```bash
# 进入运行中的容器
docker-compose exec deepdoc-api bash

# 在容器内查看模型
ls -lh /root/.cache/deepdoc/models/

# 手动下载模型
docker-compose exec deepdoc-api python script/download_models.py
```

---

## 端口配置

默认端口: `8000`

### 修改端口

编辑 `docker-compose.yaml`:

```yaml
services:
  deepdoc-api:
    ports:
      - "9000:8000"  # 将本地 9000 端口映射到容器 8000 端口
```

或使用环境变量：

```bash
# 修改 .env 文件
echo "EXTERNAL_PORT=9000" > .env

# 修改 docker-compose.yaml
services:
  deepdoc-api:
    ports:
      - "${EXTERNAL_PORT:-8000}:8000"
```

---

## 资源配置

### 内存和 CPU

在 `docker-compose.yaml` 中调整:

```yaml
deploy:
  resources:
    limits:
      cpus: '4.0'        # 最大 CPU 核心数
      memory: 8G         # 最大内存
    reservations:
      cpus: '2.0'        # 保留 CPU 核心数
      memory: 4G         # 保留内存
```

**建议配置**:
- 最小配置: 2 CPU + 4GB 内存
- 推荐配置: 4 CPU + 8GB 内存
- 高负载配置: 8 CPU + 16GB 内存

### 磁盘空间

- 镜像大小: ~1.5GB
- 模型缓存: ~200MB
- 总计: ~2GB

---

## 网络配置

### 外部访问

如果需要从其他机器访问，确保防火墙开放端口：

```bash
# Ubuntu/Debian
sudo ufw allow 8000

# CentOS/RHEL
sudo firewall-cmd --permanent --add-port=8000/tcp
sudo firewall-cmd --reload
```

### 反向代理（Nginx）

创建 Nginx 配置:

```nginx
# /etc/nginx/conf.d/deepdoc.conf
server {
    listen 80;
    server_name deepdoc.example.com;

    location / {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # 超时配置（处理大文件）
        proxy_read_timeout 300s;
        proxy_connect_timeout 300s;
        proxy_send_timeout 300s;

        # 上传大小限制
        client_max_body_size 100M;
    }
}
```

重启 Nginx:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

---

## 环境变量

创建 `.env` 文件:

```bash
# 服务配置
PORT=8000
HOST=0.0.0.0

# OCR 配置
OCR_DET_THRESHOLD=0.3
OCR_REC_THRESHOLD=0.5

# PDF 配置
PDF_DPI=200
LIGHTEN=0

# HuggingFace 镜像（可选）
# HF_ENDPOINT=https://hf-mirror.com
```

在 `docker-compose.yaml` 中引用:

```yaml
services:
  deepdoc-api:
    env_file:
      - .env
```

---

## 持久化数据

### 模型缓存

模型自动缓存在 Docker 卷 `deepdoc-models` 中:

```bash
# 查看卷
docker volume ls | grep deepdoc

# 查看卷详情
docker volume inspect deepdoc_deepdoc-models

# 备份卷
docker run --rm -v deepdoc_deepdoc-models:/data -v $(pwd):/backup \
    ubuntu tar czf /backup/deepdoc-models-backup.tar.gz /data

# 恢复卷
docker run --rm -v deepdoc_deepdoc-models:/data -v $(pwd):/backup \
    ubuntu tar xzf /backup/deepdoc-models-backup.tar.gz -C /
```

### 挂载本地目录

在 `docker-compose.yaml` 中添加:

```yaml
services:
  deepdoc-api:
    volumes:
      - ./local_models:/root/.cache/deepdoc/models  # 使用本地模型
      - ./test_files:/app/test_files:ro              # 挂载测试文件
```

---

## 多实例部署

### 使用 Docker Swarm

```bash
# 初始化 Swarm
docker swarm init

# 部署服务（3 个副本）
docker stack deploy -c docker-compose.yaml deepdoc

# 查看服务
docker service ls

# 扩展服务
docker service scale deepdoc_deepdoc-api=5

# 删除服务
docker stack rm deepdoc
```

### 使用 Kubernetes

创建 `k8s-deployment.yaml`:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: deepdoc-api
spec:
  replicas: 3
  selector:
    matchLabels:
      app: deepdoc-api
  template:
    metadata:
      labels:
        app: deepdoc-api
    spec:
      containers:
      - name: deepdoc-api
        image: deepdoc:latest
        ports:
        - containerPort: 8000
        resources:
          limits:
            cpu: "4"
            memory: "8Gi"
          requests:
            cpu: "2"
            memory: "4Gi"
---
apiVersion: v1
kind: Service
metadata:
  name: deepdoc-api
spec:
  type: LoadBalancer
  ports:
  - port: 80
    targetPort: 8000
  selector:
    app: deepdoc-api
```

部署:

```bash
kubectl apply -f k8s-deployment.yaml
```

---

## 故障排查

### 1. 容器无法启动

```bash
# 查看日志
docker-compose logs deepdoc-api

# 检查配置
docker-compose config

# 检查镜像
docker images | grep deepdoc
```

### 2. 端口冲突

```bash
# 查找占用端口的进程
lsof -i :8000

# 或使用 netstat
netstat -tulpn | grep 8000

# 杀死进程
kill -9 <PID>
```

### 3. 内存不足

```bash
# 增加 Docker 内存限制
# 编辑 /etc/docker/daemon.json
{
  "default-shm-size": "2G",
  "default-runtime": "runc"
}

# 重启 Docker
sudo systemctl restart docker
```

### 4. 模型下载失败

```bash
# 手动下载模型
docker-compose exec deepdoc-api python script/download_models.py

# 或使用镜像站
docker-compose exec deepdoc-api bash
export HF_ENDPOINT=https://hf-mirror.com
python script/download_models.py
```

### 5. API 请求超时

增加超时时间（在 Nginx 或 API 网关中配置）:

```nginx
proxy_read_timeout 600s;
proxy_connect_timeout 600s;
```

---

## 性能优化

### 1. 预加载模型

在 Dockerfile 中添加:

```dockerfile
# 预下载模型
RUN python script/download_models.py
```

### 2. 使用多阶段构建

```dockerfile
# 构建阶段
FROM python:3.10-slim as builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --user -r requirements.txt

# 运行阶段
FROM python:3.10-slim
COPY --from=builder /root/.local /root/.local
ENV PATH=/root/.local/bin:$PATH
COPY . /app
WORKDIR /app
CMD ["python", "api_service.py"]
```

### 3. 启用 HTTP/2

使用支持 HTTP/2 的反向代理（如 Nginx 1.9.5+）。

### 4. 使用缓存

实现 Redis 缓存层缓存解析结果。

---

## 监控和日志

### Prometheus 监控

添加 Prometheus 导出器:

```yaml
# docker-compose.yaml
services:
  prometheus:
    image: prom/prometheus
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml
    ports:
      - "9090:9090"
```

### Grafana 可视化

```yaml
services:
  grafana:
    image: grafana/grafana
    ports:
      - "3000:3000"
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=admin
```

### ELK 日志聚合

配置 Filebeat 收集容器日志到 Elasticsearch。

---

## 安全建议

### 1. 限制访问

使用防火墙限制访问源：

```bash
sudo ufw allow from 192.168.1.0/24 to any port 8000
```

### 2. 使用 HTTPS

配置 SSL 证书（使用 Let's Encrypt）:

```bash
sudo certbot --nginx -d deepdoc.example.com
```

### 3. API 认证

在 Nginx 中添加 Basic Auth:

```nginx
location / {
    auth_basic "Restricted Access";
    auth_basic_user_file /etc/nginx/.htpasswd;
    proxy_pass http://localhost:8000;
}
```

### 4. 限流

使用 Nginx 限流:

```nginx
limit_req_zone $binary_remote_addr zone=api_limit:10m rate=10r/s;

location / {
    limit_req zone=api_limit burst=20;
    proxy_pass http://localhost:8000;
}
```

---

## 备份和恢复

### 备份

```bash
# 备份模型卷
docker run --rm -v deepdoc_deepdoc-models:/data -v $(pwd):/backup \
    ubuntu tar czf /backup/deepdoc-$(date +%Y%m%d).tar.gz /data

# 备份配置
tar czf deepdoc-config-$(date +%Y%m%d).tar.gz \
    docker-compose.yaml Dockerfile .env
```

### 恢复

```bash
# 恢复模型卷
docker run --rm -v deepdoc_deepdoc-models:/data -v $(pwd):/backup \
    ubuntu tar xzf /backup/deepdoc-20231211.tar.gz -C /

# 恢复配置
tar xzf deepdoc-config-20231211.tar.gz
```

---

## 更新和升级

### 更新代码

```bash
# 停止服务
docker-compose down

# 拉取最新代码
git pull

# 重新构建
docker-compose build

# 启动服务
docker-compose up -d
```

### 更新依赖

编辑 `requirements.txt`，然后:

```bash
docker-compose build --no-cache
docker-compose up -d
```

---

## 支持

- API 文档: [API_DOCUMENTATION.md](API_DOCUMENTATION.md)
- 项目文档: [README.md](README.md)
- 功能分析: [FEATURE_ANALYSIS.md](FEATURE_ANALYSIS.md)

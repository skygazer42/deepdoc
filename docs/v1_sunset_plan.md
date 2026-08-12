# DeepDoc v1 API Sunset 计划

## 概述

在 v2 API 通过 canary 部署验证后，将逐步淘汰 v1 API。

## 时间线

| 阶段 | 时间 | 动作 |
|------|------|------|
| Phase 1 | 当前 | v1/v2 并行，canary 分流 |
| Phase 2 | +2 周 | v1 返回 Deprecation Header |
| Phase 3 | +4 周 | v1 返回 410 Gone |
| Phase 4 | +6 周 | 删除 v1 代码 |

## Phase 1: 并行运行（当前）

- 环境变量 `CANARY_PERCENT` 控制 v2 流量
- 默认 0% (全部 v1)，逐步增加到 100%
- 监控指标：
  - 响应时间 (P50/P95/P99)
  - 错误率
  - Chunk 质量（通过 shadow_run.py 对比）

## Phase 2: Deprecation Header

在 v1 响应中添加 deprecation header：

```python
@app.middleware("http")
async def add_deprecation_header(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/parse") and not request.url.path.startswith("/v2"):
        response.headers["Deprecation"] = "true"
        response.headers["Sunset"] = "2026-09-30"
        response.headers["Link"] = '</v2/parse>; rel="successor-version"'
    return response
```

## Phase 3: 410 Gone

v1 端点返回 410 状态码：

```python
@app.post("/parse", response_model=ParseResult)
async def parse_document(...):
    raise HTTPException(
        status_code=410,
        detail={
            "error": "v1 API has been deprecated",
            "migration": "Please use /v2/parse instead",
            "docs": "https://docs.deepdoc.dev/migration/v1-to-v2"
        }
    )
```

## Phase 4: 代码删除

删除以下文件/代码：
- `/parse` 端点（保留 `/v2/parse`）
- v1 相关的 response model（ParseResult）
- `script/dify_export.py` 中的 v1 调用逻辑

## 迁移指南

### 从 v1 迁移到 v2

**v1 (同步)**:
```python
POST /parse
Content-Type: multipart/form-data
file: <binary>
use_ocr: true

Response: { success, chunks, ... }
```

**v2 (异步)**:
```python
# 1. 提交任务
POST /v2/parse
Content-Type: multipart/form-data
file: <binary>
use_ocr: true

Response: { task_id, status: "pending" }

# 2. 查询状态
GET /v2/status/{task_id}

Response: { task_id, status: "completed" }

# 3. 获取结果
GET /v2/result/{task_id}

Response: { task_id, status, result: { chunks, ... } }
```

**v2 (同步模式 - 内存队列)**:
```python
POST /v2/parse
# 同上

Response: { task_id: "sync", status: "completed", result: { chunks, ... } }
```

### 关键差异

| 特性 | v1 | v2 |
|------|----|----|
| 执行模式 | 同步 | 异步（Redis）/ 同步（内存） |
| 响应格式 | 直接返回结果 | task_id + 轮询 |
| 错误处理 | HTTP 状态码 | 任务状态 + 错误信息 |
| 结果格式 | ParseResult | ParseSyncResponse / TaskResultResponse |

## 监控告警

### 关键指标

1. **v2 API 成功率**: > 99.9%
2. **v2 API P95 延迟**: < 30s（PDF 解析）
3. **v1 API 请求量**: 递减趋势
4. **Shadow run 差异率**: < 5%

### 告警规则

```yaml
- alert: V2HighErrorRate
  expr: rate(http_requests_total{path="/v2/parse", status=~"5.."}[5m]) > 0.01
  for: 5m
  labels:
    severity: critical

- alert: V1DeprecationWarning
  expr: rate(http_requests_total{path="/parse"}[1h]) > 100
  for: 1h
  labels:
    severity: warning
  annotations:
    summary: "v1 API 仍有大量请求，请检查迁移进度"
```

## 回滚计划

如果 v2 API 出现严重问题：

1. 设置 `CANARY_FORCE_V1=1` 强制所有流量走 v1
2. 或设置 `CANARY_PERCENT=0` 停止 v2 流量
3. 检查日志和监控，定位问题
4. 修复后重新开启 canary

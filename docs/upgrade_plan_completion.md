# DeepDoc CPU 文档解析升级计划 - 完成报告

## 总览

| 步骤 | 名称 | 状态 | 说明 |
|------|------|------|------|
| Step 1 | P0 修复 | ✅ 完成 | 安全加固、依赖清理 |
| Step 2 | 统一结果模型 | ✅ 完成 | ParseDocument + Fitz 预检 + fast path |
| Step 3 | PP-StructureV3 | ✅ 完成 | 本地 OCR 引擎集成 |
| Step 4 | Redis + 队列 + /v2 API | ✅ 完成 | 异步任务处理 |
| Step 5 | 迁移 Dify 导出脚本 | ✅ 完成 | 支持 v1/v2 API |
| Step 6 | Shadow Run | ✅ 完成 | v1/v2 结果对比工具 |
| Step 7 | Canary 部署 | ✅ 完成 | 百分比分流配置 |
| Step 8 | Sunset 旧 API | ✅ 完成 | 迁移计划文档 |

## 关键成果

### 1. 性能提升

| 指标 | 旧引擎 | 新引擎 | 改进 |
|------|--------|--------|------|
| Fitz fast 路径 | N/A | 0.03-0.06s/page | **新功能** |
| RapidOCR + PP-OCRv6 | N/A | ~1-2s/page (CPU) | **检测精度 +4.6%** |
| PP-StructureV3 | N/A | ~20s/page (CPU) | 质量提升 |
| Chunk 粒度 | 1 chunk/page | 10-24 chunks/page | **10-24x 提升** |
| 内存占用 | N/A | 41MB RSS | 远低于 20GB 目标 |

### 2. 架构改进

- **三层路由**: fitz_fast / hybrid / slow_full
- **统一结果模型**: ParseDocument + ParseChunk
- **异步任务队列**: InMemoryQueue (dev) + RedisQueue (prod)
- **Canary 部署**: 百分比分流，支持灰度发布

### 3. 文件变更

**新增文件**:
- `parser/ppstructure_engine.py` - PP-StructureV3 引擎封装
- `parser/task_queue.py` - 任务队列（内存/Redis）
- `parser/worker.py` - 后台工作进程
- `parser/canary.py` - Canary 部署配置
- `script/shadow_run.py` - v1/v2 对比工具
- `docs/v1_sunset_plan.md` - v1 API 淘汰计划

**修改文件**:
- `api_service.py` - 添加 /v2 API 端点 + Canary 分流
- `script/dify_export.py` - 支持 v2 API

## 验收检查清单

### Step 1: P0 修复
- [x] 安全漏洞修复
- [x] 依赖清理
- [x] 错误处理改进

### Step 2: 统一结果模型
- [x] ParseDocument 数据模型
- [x] Fitz 预检（加密/损坏/空文件）
- [x] fitz_fast 路径（≤0.5s/page）

### Step 3: PP-StructureV3
- [x] paddleocr 3.7.0 安装
- [x] PP-StructureV3 单例
- [x] fast_pdf.py 集成
- [x] 分块输出解析

### Step 4: Redis + 队列 + /v2 API
- [x] InMemoryQueue 实现
- [x] RedisQueue 实现（需安装 Redis）
- [x] parse_document_task worker
- [x] /v2/parse, /v2/status, /v2/result 端点

### Step 5: 迁移 Dify 导出脚本
- [x] parse_with_deepdoc_v1()
- [x] parse_with_deepdoc_v2()
- [x] --use-v2 命令行参数

### Step 6: Shadow Run
- [x] shadow_run.py 脚本
- [x] v1/v2 结果对比
- [x] JSON 输出支持

### Step 7: Canary 部署
- [x] CANARY_PERCENT 环境变量
- [x] CANARY_FORCE_V2/V1 强制模式
- [x] 确定性分流（基于 request_id）

### Step 8: Sunset 旧 API
- [x] 迁移指南
- [x] 时间线规划
- [x] 回滚计划

## 使用指南

### 启动服务

```bash
# 开发模式（内存队列）
QUEUE_BACKEND=memory python api_service.py

# 生产模式（Redis 队列）
QUEUE_BACKEND=redis REDIS_URL=redis://localhost:6379/0 python api_service.py
```

### 测试 v2 API

```bash
# 同步模式测试
curl -X POST http://localhost:8000/v2/parse \
  -F "file=@document.pdf" \
  -F "use_ocr=false"

# 异步模式测试（需要 Redis）
curl -X POST http://localhost:8000/v2/parse \
  -F "file=@document.pdf" \
  -F "use_ocr=true"

# 查询状态
curl http://localhost:8000/v2/status/{task_id}

# 获取结果
curl http://localhost:8000/v2/result/{task_id}
```

### Canary 部署

```bash
# 10% 流量走 v2
CANARY_PERCENT=10 python api_service.py

# 强制 v2（测试）
CANARY_FORCE_V2=1 python api_service.py

# 强制 v1（回滚）
CANARY_FORCE_V1=1 python api_service.py
```

### Shadow Run

```bash
# 比较单个文件
python script/shadow_run.py document.pdf

# 比较目录
python script/shadow_run.py --dir /path/to/pdfs

# 输出 JSON
python script/shadow_run.py document.pdf --json
```

## 后续工作

### 短期（1-2 周）
- [ ] 安装 Redis 服务（生产环境）
- [ ] 运行 shadow_run 验证 v2 结果
- [ ] 逐步增加 CANARY_PERCENT（10% → 50% → 100%）

### 中期（1 个月）
- [ ] 添加 Deprecation Header 到 v1 API
- [ ] 监控 v2 API 性能指标
- [ ] 优化 PP-StructureV3 推理速度（GPU 加速）

### 长期（2-3 个月）
- [ ] 删除 v1 API 代码
- [ ] 支持更多文档格式（docx, xlsx, pptx）的异步解析
- [ ] 分布式 worker 集群

## 已知限制

1. **PP-StructureV3 速度**: CPU 上约 20s/page，超过 7s 目标（无 GPU）
2. **Redis 依赖**: 异步模式需要 Redis 服务，当前环境未安装
3. **v1/v2 结果差异**: 需要通过 shadow_run 验证一致性

## 参考文档

- `docs/v1_sunset_plan.md` - v1 API 淘汰计划
- `docs/dify_integration.md` - Dify 集成指南
- `step3-ppstructure-benchmark.md` - PP-StructureV3 性能基准
- `step4-redis-queues-workers.md` - Step 4 实现细节

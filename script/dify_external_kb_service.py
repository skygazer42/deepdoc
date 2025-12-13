#!/usr/bin/env python3
"""
Dify 外部知识库 API 服务

这个服务实现了 Dify 外部知识库检索 API 规范，
让 Dify 可以直接调用 DeepDoc 进行文档检索。

参考: https://docs.dify.ai/guides/knowledge-base/external-knowledge-api-documentation

使用方法:
1. 启动此服务: python dify_external_kb_service.py
2. 在 Dify 中添加外部知识库:
   - API 端点: http://your-server:8001/retrieval
   - API Key: your-secret-key (在环境变量 DIFY_EXTERNAL_KB_KEY 中设置)
"""

import os
import json
import hashlib
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import uvicorn

# 配置
DOCS_DIR = os.getenv("DOCS_DIR", "/app/data/parsed_docs")  # 已解析文档存储目录
API_KEY = os.getenv("DIFY_EXTERNAL_KB_KEY", "your-secret-key")
PORT = int(os.getenv("EXTERNAL_KB_PORT", "8001"))

app = FastAPI(title="DeepDoc External Knowledge Base for Dify")


class RetrievalRequest(BaseModel):
    """Dify 检索请求格式"""
    knowledge_id: str  # 知识库 ID
    query: str  # 查询文本
    retrieval_setting: dict  # 检索设置 (top_k, score_threshold)


class Record(BaseModel):
    """检索结果记录"""
    content: str
    score: float
    title: str
    metadata: Optional[dict] = None


class RetrievalResponse(BaseModel):
    """Dify 检索响应格式"""
    records: list[Record]


# 简单的文档存储（生产环境应使用向量数据库）
documents_cache: dict = {}


def load_documents():
    """加载已解析的文档"""
    global documents_cache

    docs_path = Path(DOCS_DIR)
    if not docs_path.exists():
        docs_path.mkdir(parents=True, exist_ok=True)
        return

    for json_file in docs_path.glob("*.json"):
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                doc = json.load(f)
                doc_id = json_file.stem
                documents_cache[doc_id] = doc
                print(f"Loaded document: {doc_id}")
        except Exception as e:
            print(f"Error loading {json_file}: {e}")


def simple_search(query: str, chunks: list, top_k: int = 5, score_threshold: float = 0.0) -> list:
    """
    简单的关键词匹配搜索
    生产环境应使用向量数据库进行语义搜索
    """
    query_lower = query.lower()
    query_terms = query_lower.split()

    results = []
    for i, chunk in enumerate(chunks):
        chunk_lower = chunk.lower()

        # 计算匹配分数（简单的词频匹配）
        score = 0
        for term in query_terms:
            if term in chunk_lower:
                score += chunk_lower.count(term) / len(chunk_lower.split())

        if score > score_threshold:
            results.append({
                "content": chunk,
                "score": min(score, 1.0),  # 归一化到 0-1
                "index": i
            })

    # 按分数排序
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:top_k]


@app.on_event("startup")
async def startup():
    """启动时加载文档"""
    load_documents()
    print(f"Loaded {len(documents_cache)} documents")


@app.post("/retrieval")
async def retrieval(
    request: RetrievalRequest,
    authorization: str = Header(None)
):
    """
    Dify 外部知识库检索 API

    请求格式:
    {
        "knowledge_id": "kb-001",
        "query": "用户查询",
        "retrieval_setting": {
            "top_k": 5,
            "score_threshold": 0.5
        }
    }
    """
    # 验证 API Key
    if authorization != f"Bearer {API_KEY}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    knowledge_id = request.knowledge_id
    query = request.query
    top_k = request.retrieval_setting.get("top_k", 5)
    score_threshold = request.retrieval_setting.get("score_threshold", 0.0)

    # 获取文档
    if knowledge_id not in documents_cache:
        # 尝试重新加载
        load_documents()
        if knowledge_id not in documents_cache:
            return JSONResponse(content={"records": []})

    doc = documents_cache[knowledge_id]
    chunks = doc.get("chunks", [])

    # 搜索
    search_results = simple_search(query, chunks, top_k, score_threshold)

    # 构造响应
    records = []
    for result in search_results:
        records.append(Record(
            content=result["content"],
            score=result["score"],
            title=f"{doc.get('name', knowledge_id)} - Part {result['index'] + 1}",
            metadata={
                "source": doc.get("source", ""),
                "chunk_index": result["index"]
            }
        ))

    return RetrievalResponse(records=records)


@app.get("/health")
async def health():
    """健康检查"""
    return {"status": "healthy", "documents": len(documents_cache)}


@app.get("/documents")
async def list_documents(authorization: str = Header(None)):
    """列出所有文档"""
    if authorization != f"Bearer {API_KEY}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    docs = []
    for doc_id, doc in documents_cache.items():
        docs.append({
            "id": doc_id,
            "name": doc.get("name", doc_id),
            "chunks": len(doc.get("chunks", [])),
            "source": doc.get("source", "")
        })
    return {"documents": docs}


@app.post("/documents")
async def add_document(request: Request, authorization: str = Header(None)):
    """
    添加文档到知识库

    请求格式:
    {
        "id": "doc-001",  # 可选，不提供则自动生成
        "name": "文档名称",
        "chunks": ["chunk1", "chunk2", ...],
        "source": "来源信息"
    }
    """
    if authorization != f"Bearer {API_KEY}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    body = await request.json()

    doc_id = body.get("id") or hashlib.md5(body.get("name", "").encode()).hexdigest()[:12]
    name = body.get("name", doc_id)
    chunks = body.get("chunks", [])
    source = body.get("source", "")

    doc = {
        "name": name,
        "chunks": chunks,
        "source": source
    }

    # 保存到文件
    docs_path = Path(DOCS_DIR)
    docs_path.mkdir(parents=True, exist_ok=True)

    with open(docs_path / f"{doc_id}.json", "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)

    # 更新缓存
    documents_cache[doc_id] = doc

    return {"id": doc_id, "name": name, "chunks": len(chunks)}


@app.delete("/documents/{doc_id}")
async def delete_document(doc_id: str, authorization: str = Header(None)):
    """删除文档"""
    if authorization != f"Bearer {API_KEY}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    if doc_id not in documents_cache:
        raise HTTPException(status_code=404, detail="Document not found")

    # 删除文件
    doc_path = Path(DOCS_DIR) / f"{doc_id}.json"
    if doc_path.exists():
        doc_path.unlink()

    # 删除缓存
    del documents_cache[doc_id]

    return {"deleted": doc_id}


if __name__ == "__main__":
    print(f"Starting Dify External Knowledge Base Service on port {PORT}")
    print(f"Documents directory: {DOCS_DIR}")
    print(f"API endpoint: http://localhost:{PORT}/retrieval")
    uvicorn.run(app, host="0.0.0.0", port=PORT)

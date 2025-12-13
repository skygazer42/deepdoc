#!/usr/bin/env python3
"""
DeepDoc 文档解析 API 服务
"""
import base64
import os
import sys
import tempfile
import logging
from io import BytesIO
from typing import Optional, List
from pathlib import Path
import re

# 添加项目路径
sys.path.insert(0, '/app')

from fastapi import FastAPI, File, UploadFile, HTTPException, Form
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 导入解析器
from parser import PdfParser, DocxParser, ExcelParser, PptParser, HtmlParser, TxtParser, PlainParser

# 创建 FastAPI 应用
app = FastAPI(
    title="DeepDoc 文档解析服务",
    description="支持 PDF、Word、Excel、PPT、HTML、TXT 等多种文档格式的智能解析",
    version="1.0.0"
)

# 允许本地前端 / 不同端口访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 支持的文件类型
SUPPORTED_TYPES = {
    '.pdf': PdfParser,
    '.docx': DocxParser,
    '.doc': DocxParser,
    '.xlsx': ExcelParser,
    '.xls': ExcelParser,
    '.csv': ExcelParser,
    '.pptx': PptParser,
    '.ppt': PptParser,
    '.html': HtmlParser,
    '.htm': HtmlParser,
    '.txt': TxtParser,
    '.md': TxtParser,
}

# 响应模型
class ParseResult(BaseModel):
    success: bool
    file_name: str
    file_type: str
    total_chunks: int
    chunks: List[dict]
    images: Optional[List[dict]] = None
    error: Optional[str] = None

class HealthCheck(BaseModel):
    status: str
    version: str
    supported_formats: List[str]


POS_PATTERN = re.compile(r"@@([0-9\-]+)\t([0-9.]+)\t([0-9.]+)\t([0-9.]+)\t([0-9.]+)##")


def parse_positions(text: str) -> List[dict]:
    """从文本中的 @@...## 标签解析位置信息。"""
    if not text or "@@" not in text:
        return []
    positions = []
    for match in POS_PATTERN.finditer(text):
        pages, x0, x1, top, bottom = match.groups()
        try:
            page_list = [int(p) for p in pages.split("-") if p]
            positions.append(
                {
                    "pages": page_list,
                    "x0": float(x0),
                    "x1": float(x1),
                    "top": float(top),
                    "bottom": float(bottom),
                }
            )
        except Exception:
            continue
    return positions


def strip_positions(text: str) -> str:
    """去掉 @@...## 标记，保留纯文本。"""
    if not text:
        return text
    return POS_PATTERN.sub("", text)

# 初始化解析器缓存
_parser_cache = {}

def get_parser(file_ext: str, use_ocr: bool = True):
    """获取或创建解析器实例"""
    cache_key = f"{file_ext}_{use_ocr}"

    if cache_key not in _parser_cache:
        if file_ext == '.pdf':
            if use_ocr:
                _parser_cache[cache_key] = PdfParser()
            else:
                _parser_cache[cache_key] = PlainParser()
        elif file_ext in SUPPORTED_TYPES:
            _parser_cache[cache_key] = SUPPORTED_TYPES[file_ext]()
        else:
            raise ValueError(f"不支持的文件类型: {file_ext}")

    return _parser_cache[cache_key]

@app.get("/", response_model=HealthCheck)
async def root():
    """健康检查接口"""
    return {
        "status": "healthy",
        "version": "1.0.0",
        "supported_formats": list(SUPPORTED_TYPES.keys())
    }

STATIC_DIR = Path(__file__).resolve().parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

@app.get("/ui", include_in_schema=False)
async def ui_index():
    """简单的前端页入口"""
    if not STATIC_DIR.exists():
        return JSONResponse({"error": "static frontend not found"}, status_code=404)
    return RedirectResponse(url="/static/index.html")

@app.get("/health")
async def health_check():
    """健康检查"""
    return {"status": "ok"}

@app.post("/parse", response_model=ParseResult)
async def parse_document(
    file: UploadFile = File(...),
    use_ocr: bool = Form(default=True),
    need_image: bool = Form(default=False),
    zoomin: int = Form(default=3),
    max_chunks: int = Form(default=100)
):
    """
    解析文档接口

    参数:
    - file: 上传的文档文件
    - use_ocr: 是否使用 OCR（仅对 PDF 有效）
    - need_image: 是否提取图像（仅对 PDF 有效）
    - zoomin: DPI 缩放倍数（仅对 PDF 有效，默认 3）
    - max_chunks: 最大返回块数（默认 100）

    返回:
    - success: 是否成功
    - file_name: 文件名
    - file_type: 文件类型
    - total_chunks: 总块数
    - chunks: 解析结果块（限制为 max_chunks）
    - error: 错误信息（如果有）
    """
    temp_path = None

    try:
        # 检查文件类型
        file_ext = Path(file.filename).suffix.lower()
        if file_ext not in SUPPORTED_TYPES:
            raise HTTPException(
                status_code=400,
                detail=f"不支持的文件类型: {file_ext}. 支持的格式: {list(SUPPORTED_TYPES.keys())}"
            )

        logger.info(f"开始解析文件: {file.filename} (类型: {file_ext}, OCR: {use_ocr})")

        # 保存上传的文件到临时目录
        with tempfile.NamedTemporaryFile(delete=False, suffix=file_ext) as tmp:
            content = await file.read()
            tmp.write(content)
            temp_path = tmp.name

        logger.info(f"文件已保存到临时路径: {temp_path}")

        # 获取解析器
        parser = get_parser(file_ext, use_ocr)

        # 解析文档
        media_results = []
        if file_ext == '.pdf':
            if use_ocr:
                parsed = parser(temp_path, need_image=need_image, zoomin=zoomin, need_position=need_image)
                if isinstance(parsed, tuple) and len(parsed) == 2:
                    text_part, media_results = parsed
                else:
                    text_part = parsed
                if not need_image:
                    media_results = []
            else:
                text_part, _ = parser(temp_path)
                # PlainParser 返回 (line, tag) 元组
                text_part = [{"text": line, "tag": tag} for line, tag in text_part]
        elif file_ext in ['.docx', '.doc']:
            text_part = parser(temp_path)
        elif file_ext in ['.xlsx', '.xls', '.csv']:
            text_part = parser(temp_path)
        elif file_ext in ['.pptx', '.ppt']:
            text_part = parser(temp_path)
        elif file_ext in ['.html', '.htm']:
            text_part = parser(temp_path)
        elif file_ext in ['.txt', '.md']:
            text_part = parser(temp_path)
        else:
            raise ValueError(f"不支持的文件类型: {file_ext}")

        # 处理文本结果
        if isinstance(text_part, str):
            iterable = [text_part]
        elif isinstance(text_part, tuple):
            iterable = list(text_part)
        else:
            iterable = text_part

        chunks = []
        for i, chunk in enumerate(iterable):
            if i >= max_chunks:
                break
            if isinstance(chunk, dict):
                ck = {"index": i, **chunk}
            elif isinstance(chunk, str):
                ck = {"text": chunk, "index": i}
            elif isinstance(chunk, tuple):
                ck = {"text": chunk[0], "tag": chunk[1], "index": i}
            else:
                ck = {"content": str(chunk), "index": i}
            # 附加位置信息与纯文本
            if "text" in ck:
                positions = parse_positions(ck["text"])
                if positions:
                    ck["positions"] = positions
                    ck["clean_text"] = strip_positions(ck["text"])
        chunks.append(ck)

        # 处理图片/表格裁剪
        images_payload = []
        if need_image and media_results:
            for idx, item in enumerate(media_results):
                try:
                    positions = None
                    # 形如 ((img, meta), positions)
                    if isinstance(item, tuple) and len(item) == 2 and isinstance(item[0], tuple):
                        (img, meta), positions = item
                    # 形如 (img, meta)
                    elif isinstance(item, tuple) and len(item) == 2:
                        img, meta = item
                    else:
                        continue

                    bio = BytesIO()
                    img.save(bio, format="PNG")
                    b64 = base64.b64encode(bio.getvalue()).decode("ascii")
                    payload = {
                        "index": idx,
                        "kind": "figure_or_table",
                        "content": f"data:image/png;base64,{b64}",
                        "meta": meta,
                    }
                    if positions is not None:
                        payload["positions"] = positions
                    images_payload.append(payload)
                except Exception as e:
                    logger.warning(f"图片处理失败: {e}")
                    continue

        total_chunks = len(chunks)
        logger.info(f"解析完成: {file.filename}, 共 {total_chunks} 个块")

        return ParseResult(
            success=True,
            file_name=file.filename,
            file_type=file_ext,
            total_chunks=total_chunks,
            chunks=chunks,
            images=images_payload or None
        )

    except Exception as e:
        logger.error(f"解析失败: {file.filename}, 错误: {str(e)}", exc_info=True)
        return ParseResult(
            success=False,
            file_name=file.filename if file else "unknown",
            file_type=file_ext if 'file_ext' in locals() else "unknown",
            total_chunks=0,
            chunks=[],
            error=str(e)
        )

    finally:
        # 清理临时文件
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
                logger.info(f"临时文件已删除: {temp_path}")
            except Exception as e:
                logger.warning(f"无法删除临时文件: {temp_path}, 错误: {e}")

@app.post("/parse/simple")
async def parse_document_simple(file: UploadFile = File(...)):
    """
    简化的解析接口（使用默认参数）

    参数:
    - file: 上传的文档文件

    返回:
    - 解析结果的 JSON
    """
    return await parse_document(
        file=file,
        use_ocr=True,
        need_image=False,
        zoomin=3,
        max_chunks=100
    )

@app.get("/supported_formats")
async def get_supported_formats():
    """获取支持的文件格式列表"""
    return {
        "formats": list(SUPPORTED_TYPES.keys()),
        "count": len(SUPPORTED_TYPES)
    }

if __name__ == "__main__":
    # 启动服务
    port = int(os.getenv("PORT", "8000"))
    host = os.getenv("HOST", "0.0.0.0")

    logger.info(f"启动 DeepDoc API 服务: http://{host}:{port}")
    logger.info(f"API 文档: http://{host}:{port}/docs")

    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info"
    )

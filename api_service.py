#!/usr/bin/env python3
"""
DeepDoc 文档解析 API 服务
"""
import asyncio
import base64
import os
import sys
import tempfile
import logging
from concurrent.futures import ThreadPoolExecutor
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

# 线程数预算（CPU 推理防过订）——必须在 torch/xgboost 导入前设置
for _var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
    os.environ.setdefault(_var, os.getenv(_var, "2"))

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

# 文件大小限制（默认 50MB，可通过 MAX_FILE_SIZE_MB 环境变量覆盖）
MAX_FILE_SIZE_BYTES = int(os.getenv("MAX_FILE_SIZE_MB", "50")) * 1024 * 1024
# 单文件解压上限（zip 炸弹防护）
MAX_EXTRACT_SIZE_BYTES = int(os.getenv("MAX_EXTRACT_SIZE_MB", "200")) * 1024 * 1024
# 每页可提取文本的最低字符数，低于则视为扫描页
MIN_TEXT_CHARS_PER_PAGE = int(os.getenv("MIN_TEXT_CHARS_PER_PAGE", "20"))

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


# 解析器抛出的异常：错误类型 -> 稳定状态码（不再把失败包装成 HTTP 200）
class ParseError(Exception):
    """解析失败基类。"""

    def __init__(self, status_code: int, error: str):
        self.status_code = status_code
        self.error = error
        super().__init__(error)


class InvalidFileTypeError(ParseError):
    def __init__(self, error: str):
        super().__init__(400, error)


class FileTooLargeError(ParseError):
    def __init__(self, error: str):
        super().__init__(413, error)


class PasswordProtectedError(ParseError):
    def __init__(self, error: str):
        super().__init__(422, error)


class CorruptFileError(ParseError):
    def __init__(self, error: str):
        super().__init__(422, error)


class EmptyFileError(ParseError):
    def __init__(self, error: str):
        super().__init__(400, error)


class NoTextFoundError(ParseError):
    """PDF 有效但没有任何可提取文本（可能全部为扫描页）。"""
    def __init__(self, error: str):
        super().__init__(200, error)


class OCRFailureError(ParseError):
    """OCR 引擎初始化/推理失败。"""
    def __init__(self, error: str):
        super().__init__(500, error)


def classify_pdf_failure(temp_path: str) -> ParseError:
    """
    在进入重量级解析前对 PDF 做轻量预检，把失败场景映射为稳定错误码：
    - 文件过小/非 PDF 头  -> EmptyFileError(400)
    - 加密/密码保护        -> PasswordProtectedError(422)
    - 结构损坏             -> CorruptFileError(422)
    依赖：pypdf 只解析文件头与 trailer，不做页面渲染，开销可忽略。
    """
    try:
        size = os.path.getsize(temp_path)
    except OSError:
        return EmptyFileError("无法读取上传文件")
    if size == 0:
        return EmptyFileError("文件为空")
    if size < 4:
        return EmptyFileError("文件过小，不是有效的 PDF")
    try:
        with open(temp_path, "rb") as f:
            head = f.read(1024)
    except OSError as e:
        return EmptyFileError(f"无法读取文件头: {e}")
    if not head.lstrip().startswith(b"%PDF"):
        return EmptyFileError("不是有效的 PDF 文件（缺少 %PDF 头）")

    from pypdf import PdfReader, errors
    try:
        reader = PdfReader(temp_path)
    except errors.PdfReadError as e:
        return CorruptFileError(f"PDF 结构损坏: {e}")
    except Exception as e:
        return CorruptFileError(f"无法读取 PDF: {e}")

    if reader.is_encrypted:
        try:
            # 无密码尝试解密：成功说明是"已解锁"但带加密标记的 PDF，可继续
            reader.decrypt("")
        except Exception:
            return PasswordProtectedError("PDF 已加密，需要密码")
        if reader.is_encrypted:
            return PasswordProtectedError("PDF 已加密，需要密码")
    try:
        if len(reader.pages) == 0:
            return EmptyFileError("PDF 没有任何页面")
    except Exception as e:
        return CorruptFileError(f"PDF 页面读取失败: {e}")
    return None


def precheck_plain_parser(temp_path: str) -> bool:
    """
    快速判断 PlainParser 是否适用：仅当 pypdf 能打开、未加密、且提取出非空白文本时返回 True。
    返回 False 并不报错，调用方会转向 PDF OCR 路径。
    """
    from pypdf import PdfReader, errors
    try:
        reader = PdfReader(temp_path)
        if getattr(reader, "is_encrypted", False):
            return False
        for page in reader.pages:
            try:
                text = page.extract_text()
            except Exception:
                continue
            if text and text.strip():
                return True
    except Exception:
        return False
    return False

# 解析器缓存：所有解析器共享同一批 ORT 推理会话（vision/ocr.py 的 loaded_models），
# 但每个请求单独创建解析器实例，避免请求间共享有状态解析状态（boxes/page_images 等）。
def get_parser(file_ext: str, use_ocr: bool = True):
    """按需创建解析器实例（每个请求一个，模型会话通过全局缓存复用）"""
    if file_ext == '.pdf':
        if use_ocr:
            return PdfParser()
        return PlainParser()
    if file_ext in SUPPORTED_TYPES:
        return SUPPORTED_TYPES[file_ext]()
    raise ValueError(f"不支持的文件类型: {file_ext}")


# CPU 推理是阻塞操作，放到后台线程池执行，避免阻塞 asyncio 事件循环。
# 线程数与解析器并发限制解耦：解析器内部的 parallel_limiter 已限制同一文件内部推理并发，
# 这里仅负责把阻塞工作移出事件循环，防止多路并发请求拖垮单进程事件循环。
_CPU_BOUND_EXECUTOR = ThreadPoolExecutor(
    max_workers=max(2, (os.cpu_count() or 2) // 2),
    thread_name_prefix="parse-worker",
)


async def _run_sync(fn, *args, **kwargs):
    """在后台线程池中运行同步阻塞函数，返回其结果。"""
    return await asyncio.get_running_loop().run_in_executor(
        _CPU_BOUND_EXECUTOR, lambda: fn(*args, **kwargs)
    )

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
        # 校验文件类型（未上传文件 / 无扩展名 -> 400）
        if not file or not file.filename:
            raise InvalidFileTypeError("未上传文件或文件名为空")
        file_ext = Path(file.filename).suffix.lower()
        if file_ext not in SUPPORTED_TYPES:
            raise InvalidFileTypeError(
                f"不支持的文件类型: {file_ext}. 支持的格式: {list(SUPPORTED_TYPES.keys())}"
            )

        logger.info(f"开始解析文件: {file.filename} (类型: {file_ext}, OCR: {use_ocr})")

        # 保存上传的文件到临时目录
        with tempfile.NamedTemporaryFile(delete=False, suffix=file_ext) as tmp:
            content = await file.read()
            tmp.write(content)
            temp_path = tmp.name

        # 文件大小限制（在读取之后立刻检查，避免流式读取超限）
        if len(content) > MAX_FILE_SIZE_BYTES:
            raise FileTooLargeError(
                f"文件过大: {len(content)} 字节，超过限制 {MAX_FILE_SIZE_BYTES} 字节"
            )

        # PDF 轻量预检：加密/损坏/空文件给出稳定错误码
        if file_ext == ".pdf":
            precheck_err = classify_pdf_failure(temp_path)
            if precheck_err is not None:
                raise precheck_err

        logger.info(f"文件已保存到临时路径: {temp_path}")

        # 获取解析器
        parser = get_parser(file_ext, use_ocr)

        # 解析文档（CPU 推理在后台线程池执行，不阻塞事件循环）
        media_results = []
        if file_ext == '.pdf':
            if use_ocr:
                # 若 PDF 有可编辑文本层则走快速路径，否则回落到 OCR 全量解析
                if await _run_sync(precheck_plain_parser, temp_path):
                    lines, tags = await _run_sync(
                        parser, temp_path) if isinstance(parser, PlainParser) else await _run_sync(PlainParser(), temp_path)
                    text_part = [{"text": line, "tag": tag} for line, tag in lines]
                else:
                    try:
                        parsed = await _run_sync(
                            parser, temp_path, need_image=need_image,
                            zoomin=zoomin, need_position=need_image)
                    except Exception as e:
                        raise OCRFailureError(f"OCR 解析失败: {e}") from e
                    if isinstance(parsed, tuple) and len(parsed) == 2:
                        text_part, media_results = parsed
                    else:
                        text_part = parsed
                    if not need_image:
                        media_results = []
            else:
                text_part, _ = await _run_sync(parser, temp_path)
                # PlainParser 返回 (line, tag) 元组
                text_part = [{"text": line, "tag": tag} for line, tag in text_part]
                if not any(c["text"].strip() for c in text_part):
                    raise NoTextFoundError("PDF 未提取到任何文本")
        elif file_ext in ['.docx', '.doc']:
            text_part = await _run_sync(parser, temp_path)
        elif file_ext in ['.xlsx', '.xls', '.csv']:
            text_part = await _run_sync(parser, temp_path)
        elif file_ext in ['.pptx', '.ppt']:
            text_part = await _run_sync(parser, temp_path)
        elif file_ext in ['.html', '.htm']:
            text_part = await _run_sync(parser, temp_path)
        elif file_ext in ['.txt', '.md']:
            text_part = await _run_sync(parser, temp_path)
        else:
            raise ValueError(f"不支持的文件类型: {file_ext}")

        # 处理文本结果
        if isinstance(text_part, str):
            iterable = [text_part]
        elif isinstance(text_part, tuple):
            iterable = list(text_part)
        else:
            iterable = text_part

        # total_chunks 反映真实块数（不受 max_chunks 截断影响）
        try:
            total_chunks = len(iterable) if hasattr(iterable, "__len__") else 0
        except Exception:
            total_chunks = 0

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

        logger.info(f"解析完成: {file.filename}, 共 {total_chunks} 个块")

        return ParseResult(
            success=True,
            file_name=file.filename,
            file_type=file_ext,
            total_chunks=total_chunks,
            chunks=chunks,
            images=images_payload or None
        )

    except ParseError as e:
        # 已知错误类型：按错误映射返回稳定状态码，而不是 HTTP 200
        logger.warning(f"解析请求失败: {file.filename if file else 'unknown'}, {e.error}")
        return JSONResponse(
            status_code=e.status_code,
            content={
                "success": False,
                "file_name": file.filename if file else "unknown",
                "file_type": file_ext if 'file_ext' in locals() else "unknown",
                "total_chunks": 0,
                "chunks": [],
                "error": e.error,
            }
        )
    except Exception as e:
        logger.error(f"解析失败: {file.filename}, 错误: {str(e)}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "file_name": file.filename if file else "unknown",
                "file_type": file_ext if 'file_ext' in locals() else "unknown",
                "total_chunks": 0,
                "chunks": [],
                "error": f"内部错误: {str(e)}",
            }
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

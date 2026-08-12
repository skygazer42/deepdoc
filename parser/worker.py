# -*- coding: utf-8 -*-
"""
后台工作进程（升级计划 Step 4）

用于异步处理文档解析任务。
支持单次执行和持续运行模式。

用法：
    # 单次处理队列中的下一个任务
    python -m parser.worker --once

    # 持续运行，轮询队列
    python -m parser.worker --interval 1

    # 指定 Redis URL
    python -m parser.worker --redis-url redis://localhost:6379/0
"""
import argparse
import logging
import os
import sys
import time
from typing import Dict, Callable

# 设置 ORT 线程数（CPU 优化）
os.environ.setdefault("OMP_NUM_THREADS", "2")
os.environ.setdefault("MKL_NUM_THREADS", "2")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "2")

logger = logging.getLogger(__name__)


def parse_document_task(file_path: str, file_name: str, use_ocr: bool = False,
                       max_chunks: int = 0) -> dict:
    """后台执行的文档解析任务

    Args:
        file_path: 临时文件路径
        file_name: 原始文件名
        use_ocr: 是否启用 OCR
        max_chunks: 最大 chunk 数（0=无限制）

    Returns:
        dict: 解析结果（success, chunks, total_chunks, error, ...）
    """
    import tempfile
    from pathlib import Path

    logger.info("开始后台解析: %s (ocr=%s)", file_name, use_ocr)

    try:
        # 获取文件后缀
        suffix = Path(file_name).suffix.lower()

        # PDF 解析
        if suffix == ".pdf":
            from dataclasses import asdict
            from parser.fast_pdf import parse_pdf_document, ModelConfig
            # use_ocr=False -> ocr_depth="skip"
            cfg = ModelConfig(ocr_depth="skip" if not use_ocr else "full")
            doc = parse_pdf_document(file_path, cfg=cfg, max_chunks=max_chunks)
            chunks = [asdict(ck) for ck in doc.chunks]
            return {
                "success": not doc.error,
                "error": doc.error,
                "engine": doc.engine,
                "total_chunks": len(doc.chunks),
                "chunks": chunks,
                "truncated": doc.stats.get("truncated", 0),
            }

        # 纯文本
        elif suffix in (".txt", ".md", ".csv"):
            from parser.txt_parser import RAGFlowTxtParser
            parser = RAGFlowTxtParser()
            text = Path(file_path).read_text(encoding="utf-8", errors="ignore")
            chunks = parser.parser_txt(text)
            return {
                "success": True,
                "error": None,
                "engine": None,
                "total_chunks": len(chunks),
                "chunks": chunks,
                "truncated": 0,
            }

        # 其他格式（docx, xlsx, pptx, html）暂时不支持异步
        else:
            return {
                "success": False,
                "error": f"异步解析暂不支持 {suffix} 格式",
                "engine": None,
                "total_chunks": 0,
                "chunks": [],
                "truncated": 0,
            }

    except Exception as e:
        logger.exception("后台解析失败: %s", file_name)
        return {
            "success": False,
            "error": str(e),
            "engine": None,
            "total_chunks": 0,
            "chunks": [],
            "truncated": 0,
        }
    finally:
        # 清理临时文件
        if file_path and os.path.exists(file_path):
            try:
                os.unlink(file_path)
            except OSError:
                pass


def get_task_registry() -> Dict[str, Callable]:
    """获取可执行的任务函数注册表"""
    return {
        "parse_document_task": parse_document_task,
    }


def worker_loop(queue, registry: Dict[str, Callable], interval: float = 1.0):
    """工作循环：持续轮询队列并处理任务"""
    logger.info("工作进程启动，轮询间隔: %.1fs", interval)
    while True:
        try:
            if queue.process_next(registry):
                logger.debug("任务处理完成，继续轮询...")
            else:
                time.sleep(interval)
        except KeyboardInterrupt:
            logger.info("收到中断信号，停止工作进程")
            break
        except Exception as e:
            logger.exception("工作循环异常")
            time.sleep(interval)


def main():
    parser = argparse.ArgumentParser(description="DeepDoc 后台工作进程")
    parser.add_argument("--once", action="store_true",
                        help="只处理一个任务后退出")
    parser.add_argument("--interval", type=float, default=1.0,
                        help="轮询间隔（秒），默认 1.0")
    parser.add_argument("--redis-url", type=str, default=None,
                        help="Redis URL（默认使用环境变量 REDIS_URL）")
    parser.add_argument("--queue-backend", type=str, default=None,
                        help="队列后端：memory 或 redis")
    args = parser.parse_args()

    # 配置日志
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    # 设置队列后端
    if args.queue_backend:
        os.environ["QUEUE_BACKEND"] = args.queue_backend

    from parser.task_queue import get_queue
    queue = get_queue(args.redis_url)
    registry = get_task_registry()

    if args.once:
        # 单次处理
        if queue.process_next(registry):
            logger.info("单次任务处理完成")
        else:
            logger.info("队列为空，无任务处理")
    else:
        # 持续运行
        worker_loop(queue, registry, args.interval)


if __name__ == "__main__":
    main()

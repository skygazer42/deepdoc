#!/usr/bin/env python3
"""
DeepDoc -> Dify 集成脚本

将 DeepDoc 解析结果导出为 Dify 知识库可用的格式：
1. 纯文本文件（.txt）- 直接上传到 Dify 知识库
2. JSON 格式 - 用于 Dify 外部知识库 API
3. Markdown 格式 - 保留结构信息

支持 v1 (同步) 和 v2 (异步) API。
"""

import os
import sys
import json
import argparse
import requests
import time
from pathlib import Path
from typing import Optional

# DeepDoc API 地址
DEEPDOC_API = os.getenv("DEEPDOC_API", "http://localhost:8000")


def parse_with_deepdoc_v1(file_path: str, api_url: str = None, need_image: bool = False) -> dict:
    """调用 DeepDoc v1 API 解析文档（同步）"""
    base_url = api_url or DEEPDOC_API
    url = f"{base_url}/parse"

    with open(file_path, "rb") as f:
        files = {"file": (os.path.basename(file_path), f)}
        data = {"need_image": str(need_image).lower()}
        response = requests.post(url, files=files, data=data, timeout=300)

    if response.status_code != 200:
        raise Exception(f"DeepDoc API error: {response.text}")

    return response.json()


def parse_with_deepdoc_v2(file_path: str, api_url: str = None, need_image: bool = False,
                          poll_interval: float = 1.0, max_wait: float = 600.0) -> dict:
    """调用 DeepDoc v2 API 解析文档（异步）

    1. 提交任务到 /v2/parse
    2. 轮询 /v2/status/{task_id} 直到完成
    3. 获取 /v2/result/{task_id}
    """
    base_url = api_url or DEEPDOC_API

    # 提交任务
    submit_url = f"{base_url}/v2/parse"
    with open(file_path, "rb") as f:
        files = {"file": (os.path.basename(file_path), f)}
        data = {"need_image": str(need_image).lower()}
        response = requests.post(submit_url, files=files, data=data, timeout=60)

    if response.status_code != 200:
        raise Exception(f"DeepDoc v2 submit error: {response.text}")

    result = response.json()
    task_id = result.get("task_id")

    # 同步模式（内存队列）
    if task_id == "sync":
        return result.get("result", {})

    # 异步模式：轮询状态
    print(f"任务已提交: {task_id}")
    elapsed = 0.0
    while elapsed < max_wait:
        status_url = f"{base_url}/v2/status/{task_id}"
        response = requests.get(status_url, timeout=30)
        if response.status_code != 200:
            raise Exception(f"DeepDoc v2 status error: {response.text}")

        status_data = response.json()
        status = status_data.get("status")
        print(f"  状态: {status} ({elapsed:.1f}s)")

        if status == "completed":
            # 获取结果
            result_url = f"{base_url}/v2/result/{task_id}"
            response = requests.get(result_url, timeout=30)
            if response.status_code != 200:
                raise Exception(f"DeepDoc v2 result error: {response.text}")
            return response.json().get("result", {})

        elif status == "failed":
            error = status_data.get("error", "未知错误")
            raise Exception(f"DeepDoc v2 task failed: {error}")

        time.sleep(poll_interval)
        elapsed += poll_interval

    raise Exception(f"DeepDoc v2 task timeout after {max_wait}s")


def parse_with_deepdoc(file_path: str, api_url: str = None, need_image: bool = False,
                       use_v2: bool = False, **kwargs) -> dict:
    """统一入口：根据参数选择 v1 或 v2 API"""
    if use_v2:
        return parse_with_deepdoc_v2(file_path, api_url, need_image, **kwargs)
    else:
        return parse_with_deepdoc_v1(file_path, api_url, need_image)


def extract_text_from_chunks(chunks: list) -> list:
    """从 chunks 中提取纯文本"""
    texts = []
    for chunk in chunks:
        if isinstance(chunk, dict):
            # DeepDoc 返回的 chunk 格式: {"text": "...", ...}
            text = chunk.get("text", "")
            # 清理坐标信息 (格式: 文本@@页码\tx1\ty1\tx2\ty2##)
            import re
            # 移除坐标信息，只保留文本
            text = re.sub(r'@@\d+\t[\d.]+\t[\d.]+\t[\d.]+\t[\d.]+##', '', text)
            texts.append(text.strip())
        else:
            texts.append(str(chunk))
    return texts


def export_to_txt(result: dict, output_path: str) -> str:
    """
    导出为纯文本格式
    适合直接上传到 Dify 知识库，让 Dify 自己切块
    """
    chunks = result.get("chunks", [])
    texts = extract_text_from_chunks(chunks)

    # 合并所有文本块
    full_text = "\n\n".join(texts)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(full_text)

    return output_path


def export_to_chunks_txt(result: dict, output_dir: str, prefix: str = "chunk") -> list:
    """
    导出为多个文本文件（每个 chunk 一个文件）
    适合已经用 DeepDoc 切好块的情况
    """
    chunks = result.get("chunks", [])
    texts = extract_text_from_chunks(chunks)
    os.makedirs(output_dir, exist_ok=True)

    output_files = []
    for i, text in enumerate(texts):
        output_path = os.path.join(output_dir, f"{prefix}_{i:04d}.txt")
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(text)
        output_files.append(output_path)

    return output_files


def export_to_dify_external_kb_format(result: dict, output_path: str, doc_name: str) -> str:
    """
    导出为 Dify 外部知识库 API 格式
    参考: https://docs.dify.ai/guides/knowledge-base/external-knowledge-api-documentation

    格式:
    {
        "records": [
            {
                "content": "chunk text",
                "score": 1.0,
                "title": "document title",
                "metadata": {}
            }
        ]
    }
    """
    chunks = result.get("chunks", [])
    texts = extract_text_from_chunks(chunks)

    records = []
    for i, text in enumerate(texts):
        records.append({
            "content": text,
            "score": 1.0,
            "title": f"{doc_name} - Part {i+1}",
            "metadata": {
                "chunk_index": i,
                "source": doc_name,
                "total_chunks": len(texts)
            }
        })

    output_data = {"records": records}

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)

    return output_path


def export_to_markdown(result: dict, output_path: str) -> str:
    """
    导出为 Markdown 格式
    保留章节结构，适合需要结构化信息的场景
    """
    chunks = result.get("chunks", [])
    texts = extract_text_from_chunks(chunks)

    lines = ["# 文档内容\n"]

    for i, text in enumerate(texts):
        lines.append(f"## 段落 {i+1}\n")
        lines.append(text)
        lines.append("\n---\n")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return output_path


def export_for_dify_api(result: dict, output_path: str, doc_name: str) -> str:
    """
    导出为 Dify 知识库 API 创建文档的格式
    参考: POST /datasets/{dataset_id}/document/create_by_text

    可以用这个 JSON 直接调用 Dify API 创建文档
    """
    chunks = result.get("chunks", [])
    texts = extract_text_from_chunks(chunks)
    full_text = "\n\n".join(texts)

    # Dify create document by text API 格式
    dify_payload = {
        "name": doc_name,
        "text": full_text,
        "indexing_technique": "high_quality",  # or "economy"
        "process_rule": {
            "mode": "custom",
            "rules": {
                "pre_processing_rules": [
                    {"id": "remove_extra_spaces", "enabled": True},
                    {"id": "remove_urls_emails", "enabled": False}
                ],
                "segmentation": {
                    "separator": "\n\n",  # 使用双换行分隔（DeepDoc 已切好）
                    "max_tokens": 1000
                }
            }
        }
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(dify_payload, f, ensure_ascii=False, indent=2)

    return output_path


def upload_to_dify(
    dify_api: str,
    api_key: str,
    dataset_id: str,
    result: dict,
    doc_name: str
) -> dict:
    """
    直接上传到 Dify 知识库

    Args:
        dify_api: Dify API 地址，如 http://localhost/v1
        api_key: Dify API Key
        dataset_id: 知识库 ID
        result: DeepDoc 解析结果
        doc_name: 文档名称
    """
    chunks = result.get("chunks", [])
    texts = extract_text_from_chunks(chunks)
    full_text = "\n\n".join(texts)

    url = f"{dify_api}/datasets/{dataset_id}/document/create_by_text"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    payload = {
        "name": doc_name,
        "text": full_text,
        "indexing_technique": "high_quality",
        "process_rule": {
            "mode": "custom",
            "rules": {
                "pre_processing_rules": [
                    {"id": "remove_extra_spaces", "enabled": True},
                    {"id": "remove_urls_emails", "enabled": False}
                ],
                "segmentation": {
                    "separator": "\n\n",
                    "max_tokens": 1000
                }
            }
        }
    }

    response = requests.post(url, headers=headers, json=payload, timeout=120)

    if response.status_code != 200:
        raise Exception(f"Dify API error: {response.status_code} - {response.text}")

    return response.json()


def main():
    parser = argparse.ArgumentParser(
        description="DeepDoc -> Dify 集成工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 解析 PDF 并导出为纯文本
  python dify_export.py document.pdf -o output.txt -f txt

  # 解析并导出为 Dify 外部知识库格式
  python dify_export.py document.pdf -o output.json -f external_kb

  # 解析并直接上传到 Dify
  python dify_export.py document.pdf --upload \\
      --dify-api http://localhost/v1 \\
      --api-key your-api-key \\
      --dataset-id your-dataset-id

  # 批量处理目录中的所有 PDF
  for f in docs/*.pdf; do
      python dify_export.py "$f" -o "output/$(basename "$f" .pdf).txt" -f txt
  done
        """
    )

    parser.add_argument("input_file", help="输入文件路径")
    parser.add_argument("-o", "--output", help="输出文件路径")
    parser.add_argument(
        "-f", "--format",
        choices=["txt", "chunks", "markdown", "external_kb", "dify_api"],
        default="txt",
        help="输出格式 (默认: txt)"
    )
    parser.add_argument("--deepdoc-api", default=DEEPDOC_API, help="DeepDoc API 地址")
    parser.add_argument("--need-image", action="store_true", help="是否提取图片")
    parser.add_argument("--use-v2", action="store_true", help="使用 v2 异步 API（需要 Redis 队列支持）")

    # Dify 直接上传选项
    parser.add_argument("--upload", action="store_true", help="直接上传到 Dify")
    parser.add_argument("--dify-api", help="Dify API 地址")
    parser.add_argument("--api-key", help="Dify API Key")
    parser.add_argument("--dataset-id", help="Dify 知识库 ID")

    args = parser.parse_args()

    # 设置 DeepDoc API (使用命令行参数覆盖默认值)
    deepdoc_api = args.deepdoc_api

    input_path = Path(args.input_file)
    if not input_path.exists():
        print(f"错误: 文件不存在 - {args.input_file}")
        sys.exit(1)

    doc_name = input_path.stem

    print(f"正在解析: {args.input_file}")
    result = parse_with_deepdoc(args.input_file, api_url=deepdoc_api, need_image=args.need_image,
                                use_v2=args.use_v2)

    chunk_count = len(result.get("chunks", []))
    print(f"解析完成: {chunk_count} 个文本块")

    # 直接上传到 Dify
    if args.upload:
        if not all([args.dify_api, args.api_key, args.dataset_id]):
            print("错误: 上传需要 --dify-api, --api-key, --dataset-id")
            sys.exit(1)

        print(f"正在上传到 Dify...")
        response = upload_to_dify(
            args.dify_api,
            args.api_key,
            args.dataset_id,
            result,
            doc_name
        )
        print(f"上传成功: {response}")
        return

    # 导出到文件
    if not args.output:
        # 默认输出路径
        suffix_map = {
            "txt": ".txt",
            "chunks": "_chunks",
            "markdown": ".md",
            "external_kb": "_external_kb.json",
            "dify_api": "_dify_api.json"
        }
        args.output = str(input_path.parent / f"{doc_name}{suffix_map[args.format]}")

    if args.format == "txt":
        output = export_to_txt(result, args.output)
        print(f"已导出纯文本: {output}")

    elif args.format == "chunks":
        outputs = export_to_chunks_txt(result, args.output, doc_name)
        print(f"已导出 {len(outputs)} 个文本块到: {args.output}/")

    elif args.format == "markdown":
        output = export_to_markdown(result, args.output)
        print(f"已导出 Markdown: {output}")

    elif args.format == "external_kb":
        output = export_to_dify_external_kb_format(result, args.output, doc_name)
        print(f"已导出 Dify 外部知识库格式: {output}")

    elif args.format == "dify_api":
        output = export_for_dify_api(result, args.output, doc_name)
        print(f"已导出 Dify API 格式: {output}")


if __name__ == "__main__":
    main()

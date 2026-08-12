#!/usr/bin/env python3
"""
DeepDoc Shadow Run 模式

同时调用 v1 和 v2 API，比较结果差异，用于验证 v2 API 的正确性。

用法：
    # 比较单个文件
    python shadow_run.py document.pdf

    # 比较目录中所有 PDF
    python shadow_run.py --dir /path/to/pdfs

    # 输出详细差异
    python shadow_run.py document.pdf --verbose
"""

import os
import sys
import json
import argparse
import requests
import hashlib
from pathlib import Path
from typing import Optional, List, Dict, Any

# DeepDoc API 地址
DEEPDOC_API = os.getenv("DEEPDOC_API", "http://localhost:8000")


def parse_v1(file_path: str, api_url: str = None) -> dict:
    """调用 v1 API"""
    base_url = api_url or DEEPDOC_API
    url = f"{base_url}/parse"

    with open(file_path, "rb") as f:
        files = {"file": (os.path.basename(file_path), f)}
        response = requests.post(url, files=files, timeout=300)

    if response.status_code != 200:
        raise Exception(f"v1 API error: {response.status_code} - {response.text}")

    return response.json()


def parse_v2(file_path: str, api_url: str = None) -> dict:
    """调用 v2 API（同步模式）"""
    base_url = api_url or DEEPDOC_API
    url = f"{base_url}/v2/parse"

    with open(file_path, "rb") as f:
        files = {"file": (os.path.basename(file_path), f)}
        response = requests.post(url, files=files, timeout=300)

    if response.status_code != 200:
        raise Exception(f"v2 API error: {response.status_code} - {response.text}")

    result = response.json()
    # v2 同步模式返回 result 字段
    if "result" in result:
        return result["result"]
    return result


def normalize_chunks(chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """标准化 chunk 格式以便比较"""
    normalized = []
    for chunk in chunks:
        if isinstance(chunk, dict):
            # 提取纯文本（去掉位置标签）
            import re
            text = chunk.get("text", "")
            text = re.sub(r'@@\d+\t[\d.]+\t[\d.]+\t[\d.]+\t[\d.]+##', '', text).strip()

            normalized.append({
                "text_hash": hashlib.md5(text.encode()).hexdigest(),
                "text_length": len(text),
                "kind": chunk.get("kind", "text"),
            })
    return normalized


def compare_results(v1_result: dict, v2_result: dict, verbose: bool = False) -> dict:
    """比较 v1 和 v2 结果"""
    v1_chunks = v1_result.get("chunks", [])
    v2_chunks = v2_result.get("chunks", [])

    v1_norm = normalize_chunks(v1_chunks)
    v2_norm = normalize_chunks(v2_chunks)

    comparison = {
        "v1_chunk_count": len(v1_chunks),
        "v2_chunk_count": len(v2_chunks),
        "chunk_count_diff": len(v2_chunks) - len(v1_chunks),
        "v1_engine": v1_result.get("engine"),
        "v2_engine": v2_result.get("engine"),
        "v1_success": v1_result.get("success", True),
        "v2_success": v2_result.get("success", True),
        "v1_error": v1_result.get("error"),
        "v2_error": v2_result.get("error"),
    }

    # 计算文本相似度（基于 hash）
    v1_hashes = set(c["text_hash"] for c in v1_norm)
    v2_hashes = set(c["text_hash"] for c in v2_norm)

    common = v1_hashes & v2_hashes
    only_v1 = v1_hashes - v2_hashes
    only_v2 = v2_hashes - v1_hashes

    comparison["common_chunks"] = len(common)
    comparison["only_in_v1"] = len(only_v1)
    comparison["only_in_v2"] = len(only_v2)

    if verbose:
        comparison["v1_texts"] = [c["text_hash"] for c in v1_norm[:10]]
        comparison["v2_texts"] = [c["text_hash"] for c in v2_norm[:10]]

    return comparison


def run_shadow_test(file_path: str, api_url: str = None, verbose: bool = False) -> dict:
    """对单个文件执行 shadow test"""
    print(f"测试文件: {file_path}")

    try:
        v1_result = parse_v1(file_path, api_url)
        v1_ok = True
    except Exception as e:
        print(f"  v1 解析失败: {e}")
        v1_result = {"chunks": [], "error": str(e)}
        v1_ok = False

    try:
        v2_result = parse_v2(file_path, api_url)
        v2_ok = True
    except Exception as e:
        print(f"  v2 解析失败: {e}")
        v2_result = {"chunks": [], "error": str(e)}
        v2_ok = False

    if not v1_ok and not v2_ok:
        return {"file": file_path, "status": "both_failed"}

    comparison = compare_results(v1_result, v2_result, verbose)

    print(f"  v1: {comparison['v1_chunk_count']} chunks ({comparison['v1_engine']})")
    print(f"  v2: {comparison['v2_chunk_count']} chunks ({comparison['v2_engine']})")
    print(f"  相同: {comparison['common_chunks']}, 仅v1: {comparison['only_in_v1']}, 仅v2: {comparison['only_in_v2']}")

    return {
        "file": file_path,
        "status": "ok",
        "comparison": comparison,
    }


def main():
    parser = argparse.ArgumentParser(
        description="DeepDoc Shadow Run - 比较 v1 和 v2 API 结果",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 比较单个文件
  python shadow_run.py document.pdf

  # 比较目录中所有 PDF
  python shadow_run.py --dir /path/to/pdfs

  # 输出详细差异
  python shadow_run.py document.pdf --verbose

  # 输出 JSON 格式
  python shadow_run.py document.pdf --json
        """
    )

    parser.add_argument("input_file", nargs="?", help="输入文件路径")
    parser.add_argument("--dir", help="输入目录（处理所有 PDF）")
    parser.add_argument("--deepdoc-api", default=DEEPDOC_API, help="DeepDoc API 地址")
    parser.add_argument("--verbose", action="store_true", help="输出详细差异")
    parser.add_argument("--json", action="store_true", help="输出 JSON 格式")

    args = parser.parse_args()

    if not args.input_file and not args.dir:
        parser.error("需要指定输入文件或目录")

    files = []
    if args.dir:
        for f in Path(args.dir).glob("*.pdf"):
            files.append(str(f))
    else:
        files = [args.input_file]

    results = []
    for file_path in files:
        result = run_shadow_test(file_path, args.deepdoc_api, args.verbose)
        results.append(result)
        print()

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        # 汇总
        total = len(results)
        ok = sum(1 for r in results if r["status"] == "ok")
        print(f"汇总: {total} 个文件, {ok} 个成功")


if __name__ == "__main__":
    main()

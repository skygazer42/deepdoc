#!/usr/bin/env python3
"""
旧引擎现状基准测试（回归集 → 现 /parse 接口）

对 regression/manifest.json 中每份文档调用本地 /parse 接口，
记录每份文档的:
  - HTTP 状态码 / 是否成功
  - total_chunks / 实际 chunks 数（chunk 丢失检查）
  - 解析耗时（秒）
  - 需要 OCR 的文档：OCR 页数占比
  - RSS 峰值（内存）
输出 versioned baseline JSON 报告。

用法:
  python benchmark.py [--out baseline.json] [--base http://127.0.0.1:8000]
"""
import argparse
import json
import os
import resource
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent
MANIFEST = ROOT / "manifest.json"
DOCS = ROOT / "documents"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "baseline.json"))
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    args = ap.parse_args()

    manifest = json.load(open(MANIFEST, encoding="utf-8"))
    results = {"engine": "legacy deepdoc", "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
               "docs": [], "summary": {}}

    max_rss = 0
    errors = []
    # trust_env=False: 屏蔽本机代理环境变量（socks:// 代理会让 httpx 报错，本地回环无需代理）
    with httpx.Client(timeout=600, base_url=args.base, trust_env=False) as client:
        for doc in manifest["docs"]:
            name = doc["name"]
            path = DOCS / name
            if not path.exists():
                print(f"[SKIP] {name}: 文件不存在")
                continue
            fsize = path.stat().st_size
            expect = doc["expected"]
            use_ocr = expect in ("ocr", "ocr_or_text") or doc["type"] in ("pdf_scan", "pdf_mixed", "pdf_table")
            start = time.monotonic()
            try:
                with open(path, "rb") as f:
                    r = client.post(
                        "/parse",
                        files={"file": (name, f, "application/octet-stream")},
                        data={"use_ocr": str(use_ocr).lower()},
                    )
                elapsed = time.monotonic() - start
                payload = r.json()
                # RSS 快照（进程峰值近似）
                max_rss = max(max_rss, resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)
                entry = {
                    "name": name,
                    "size_kb": round(fsize / 1024, 1),
                    "expect": expect,
                    "status_code": r.status_code,
                    "success": payload.get("success"),
                    "error": payload.get("error"),
                    "total_chunks": payload.get("total_chunks"),
                    "returned_chunks": len(payload.get("chunks", [])),
                    "chunk_loss": (payload.get("total_chunks") or 0) - len(payload.get("chunks", [])),
                    "elapsed_s": round(elapsed, 3),
                }
                results["docs"].append(entry)
                flag = "OK " if entry["success"] else "ERR"
                print(f"[{flag}] {name:28s} {entry['elapsed_s']:8.2f}s "
                      f"chunks={entry['returned_chunks']}/{entry['total_chunks']} "
                      f"status={r.status_code} {entry['error'] or ''}")
            except Exception as e:
                errors.append((name, str(e)))
                print(f"[EXC] {name}: {e}")

    # 汇总
    ok = [d for d in results["docs"] if d["success"]]
    err = [d for d in results["docs"] if not d["success"]]
    results["summary"] = {
        "total": len(results["docs"]),
        "ok": len(ok),
        "err": len(err),
        "error_names": [d["name"] for d in err],
        "total_pages": sum(m["pages"] for m in manifest["docs"]),
        "elapsed_total_s": round(sum(d["elapsed_s"] for d in results["docs"]), 3),
        "chunk_loss_total": sum(d.get("chunk_loss", 0) for d in results["docs"]),
        "rss_peak_mb": round(max_rss, 1),
        "errors": errors,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print("\n=== 汇总 ===")
    print(json.dumps(results["summary"], ensure_ascii=False, indent=2))
    print(f"报告: {args.out}")


if __name__ == "__main__":
    main()

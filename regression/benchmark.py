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
    ap.add_argument("--fast", action="store_true",
                    help="v2 快速路径模式：记录 engine，并与 baseline.json 对比")
    ap.add_argument("--compare", default=str(ROOT / "baseline.json"),
                    help="对比基准 JSON（--fast 时使用）")
    args = ap.parse_args()

    manifest = json.load(open(MANIFEST, encoding="utf-8"))
    mode = "fast (v2 three-tier)" if args.fast else "legacy deepdoc"
    results = {"engine": mode, "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
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
                    "engine": payload.get("engine"),
                    "total_chunks": payload.get("total_chunks"),
                    "returned_chunks": len(payload.get("chunks", [])),
                    "chunk_loss": (payload.get("total_chunks") or 0) - len(payload.get("chunks", [])),
                    "elapsed_s": round(elapsed, 3),
                }
                results["docs"].append(entry)
                flag = "OK " if entry["success"] else "ERR"
                eng = f" [{entry['engine']}]" if entry["engine"] else ""
                print(f"[{flag}] {name:28s}{eng:22s} {entry['elapsed_s']:8.2f}s "
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
    # v2 快速路径模式：与基准对比，并核对验收阈值
    if args.fast:
        results["comparison"] = compare_with_baseline(results, args.compare)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print("\n=== 汇总 ===")
    print(json.dumps(results["summary"], ensure_ascii=False, indent=2))
    if args.fast and results.get("comparison"):
        print("\n=== 与基准对比 ===")
        print(json.dumps(results["comparison"], ensure_ascii=False, indent=2))
    print(f"报告: {args.out}")


def compare_with_baseline(fast_results: dict, baseline_path: str) -> dict:
    """v2 快速路径 vs legacy 基准：逐文档耗时/块数对比 + 验收阈值核对。

    仅对比两边都成功且都存在的文档；错误文档仅列出名称（错误码不应退化）。
    """
    import os as _os
    if not _os.path.exists(baseline_path):
        return {"note": f"未找到基准 {baseline_path}，跳过对比"}
    base = json.load(open(baseline_path, encoding="utf-8"))
    base_by_name = {d["name"]: d for d in base.get("docs", [])}

    rows = []
    fast_by_name = {d["name"]: d for d in fast_results["docs"]}
    for name, b in base_by_name.items():
        f = fast_by_name.get(name)
        if f is None:
            continue
        row = {"name": name}
        if b.get("success") and f.get("success"):
            row.update({
                "base_s": b.get("elapsed_s"),
                "fast_s": f.get("elapsed_s"),
                "speedup_x": round(b["elapsed_s"] / f["elapsed_s"], 2) if f["elapsed_s"] else None,
                "engine": f.get("engine"),
                "chunk_parity": b.get("total_chunks") == f.get("total_chunks"),
                "base_chunks": b.get("total_chunks"),
                "fast_chunks": f.get("total_chunks"),
            })
        else:
            row.update({
                "base_status": b.get("status_code"), "fast_status": f.get("status_code"),
                "base_err": b.get("error"), "fast_err": f.get("error"),
            })
        rows.append(row)

    # 验收阈值核对（仅 fitz_fast 路由：≤0.5s/页）
    manifest = json.load(open(MANIFEST, encoding="utf-8"))
    pages_by = {m["name"]: m["pages"] for m in manifest["docs"]}
    fast_fitz = [d for d in fast_results["docs"]
                 if d.get("success") and d.get("engine") == "fitz_fast"]
    over = []
    for d in fast_fitz:
        pages = pages_by.get(d["name"], 1)
        spp = d["elapsed_s"] / max(pages, 1)   # 秒/页
        if spp > 0.5:
            over.append({"name": d["name"], "engine": d.get("engine"),
                         "s_per_page": round(spp, 3)})
    # chunk 差异分析：fitz_fast 路由应保持 total_chunks 与 legacy 完全一致；
    # hybrid / slow_full 差异属于预期改进（更细粒度分块），单独列出。
    fitz_mismatch = [r for r in rows
                     if r.get("chunk_parity") is False and r.get("engine") == "fitz_fast"]
    other_mismatch = [r for r in rows
                      if r.get("chunk_parity") is False and r.get("engine") != "fitz_fast"]
    return {
        "docs_compared": len(rows),
        "rows": rows,
        "checks": {
            "fitz_fast_<=0.5s_per_page": "PASS" if not over else "FAIL",
            "over_threshold": over,
            "fitz_chunk_parity": len(rows) - len(fitz_mismatch) - len(other_mismatch),
            "fitz_chunk_mismatch": [r["name"] for r in fitz_mismatch],
            "other_chunk_diff": [{"name": r["name"], "engine": r.get("engine"),
                                  "base": r.get("base_chunks"),
                                  "fast": r.get("fast_chunks")}
                                 for r in other_mismatch],
            "chunk_loss_total": fast_results["summary"].get("chunk_loss_total"),
        },
    }


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""验证"预扫描批量 predict"方案的候选集覆盖性。

复制原版 _concat_downward 逻辑（逐条 predict），记录 DFS 实际评估的候选对 G；
再用两种预扫描过滤规则生成候选集 C，检查 G ⊆ C（保证批量查表不 miss）。

用法: python script/verify_precollect.py [pdf名]
"""
import copy
import os
import re
import sys
import time
from pathlib import Path

import numpy as np
import xgboost as xgb

sys.path.insert(0, str(Path(__file__).parent.parent))
ROOT = Path("/app") if Path("/app").exists() else Path("/data/temp4/deepdoc")

os.environ["OCR_ENGINE"] = "rapidocr"
os.environ["LAYOUT_MODEL"] = "doclayout"
os.environ["LAYOUT_MODEL_SIZE"] = "768"
os.environ["TABLE_ENGINE"] = "slanet"


def run_original_concat(parser, boxes):
    """复制原版 _concat_downward（boxes 已 deepcopy，不再二次复制），
    predict 处记录 (cidx_up, cidx_down, fea, prob) 到 G。"""
    # in_row 统计（原版第 1 部分）
    for i in range(len(boxes)):
        mh = parser.mean_height[boxes[i]["page_number"] - 1]
        boxes[i]["in_row"] = 0
        j = max(0, i - 12)
        while j < min(i + 12, len(boxes)):
            if j == i:
                j += 1
                continue
            ydis = parser._y_dis(boxes[i], boxes[j]) / mh
            if abs(ydis) < 1:
                boxes[i]["in_row"] += 1
            elif ydis > 0:
                break
            j += 1

    for _i, b in enumerate(boxes):
        b["_cidx"] = _i  # 初始索引标记

    G = []  # (cidx_up, cidx_down, fea, prob)
    blocks = []
    while boxes:
        chunks = []

        def dfs(up, dp):
            chunks.append(up)
            i = dp
            while i < min(dp + 12, len(boxes)):
                ydis = parser._y_dis(up, boxes[i])
                smpg = up["page_number"] == boxes[i]["page_number"]
                mh = parser.mean_height[up["page_number"] - 1]
                mw = parser.mean_width[up["page_number"] - 1]
                if smpg and ydis > mh * 4:
                    break
                if not smpg and ydis > mh * 16:
                    break
                down = boxes[i]
                if up.get("R", "") != down.get("R", "") and up["text"][-1] != "，":
                    i += 1
                    continue
                if re.match(r"[0-9]{2,3}/[0-9]{3}$", up["text"]) \
                        or re.match(r"[0-9]{2,3}/[0-9]{3}$", down["text"]) \
                        or not down["text"].strip():
                    i += 1
                    continue
                if not down["text"].strip() or not up["text"].strip():
                    i += 1
                    continue
                if up["x1"] < down["x0"] - 10 * mw or up["x0"] > down["x1"] + 10 * mw:
                    i += 1
                    continue
                if i - dp < 5 and up.get("layout_type") == "text":
                    if up.get("layoutno", "1") == down.get("layoutno", "2"):
                        dfs(down, i + 1)
                        boxes.pop(i)
                        return
                    i += 1
                    continue
                fea = parser._updown_concat_features(up, down)
                prob = parser.updown_cnt_mdl.predict(xgb.DMatrix([fea]))[0]
                G.append((up["_cidx"], down["_cidx"], fea, float(prob)))
                if prob <= 0.5:
                    i += 1
                    continue
                dfs(down, i + 1)
                boxes.pop(i)
                return

        dfs(boxes[0], 1)
        boxes.pop(0)
        if chunks:
            blocks.append(chunks)
    return G, blocks


def precollect(parser, boxes, config):
    """预扫描候选对。config: 'limit12' | 'ybreak' | 'limit12_ybreak'"""
    n = len(boxes)
    C = set()
    for i in range(n):
        up = boxes[i]
        mh = parser.mean_height[up["page_number"] - 1]
        mw = parser.mean_width[up["page_number"] - 1]
        if config in ("limit12", "limit12_ybreak"):
            js = range(i + 1, min(i + 13, n))
        else:
            js = range(i + 1, n)
        for j in js:
            down = boxes[j]
            smpg = up["page_number"] == down["page_number"]
            ydis = parser._y_dis(up, down)
            # 复刻 dfs 的 y break（break 语义：超过即停）
            if smpg and ydis > mh * 4:
                break
            if not smpg and ydis > mh * 16:
                break
            # 复刻 dfs 到达模型预测前的 4 个 continue 条件
            if up.get("R", "") != down.get("R", "") and up["text"][-1] != "，":
                continue
            if re.match(r"[0-9]{2,3}/[0-9]{3}$", up["text"]) \
                    or re.match(r"[0-9]{2,3}/[0-9]{3}$", down["text"]) \
                    or not down["text"].strip():
                continue
            if not down["text"].strip() or not up["text"].strip():
                continue
            if up["x1"] < down["x0"] - 10 * mw or up["x0"] > down["x1"] + 10 * mw:
                continue
            C.add((i, j))
    return C


def main():
    pdf_name = sys.argv[1] if len(sys.argv) > 1 else "resnet.pdf"
    pdf_path = ROOT / "regression" / "documents" / pdf_name
    if not pdf_path.exists():
        print(f"[!] 不存在: {pdf_path}")
        return

    from parser.pdf_parser import RAGFlowPdfParser

    parser = RAGFlowPdfParser()
    t0 = time.time()
    parser.__images__(str(pdf_path), zoomin=3)
    parser._layouts_rec(3)
    parser._table_transformer_job(3)
    parser._text_merge()
    print(f"[{pdf_name}] 解析到 _text_merge 完成: {time.time()-t0:.0f}s, "
          f"boxes={len(parser.boxes)}")

    # 逐条 predict 记录 G
    t0 = time.time()
    G, _ = run_original_concat(parser, copy.deepcopy(parser.boxes))
    t1 = time.time()
    Gset = {(g[0], g[1]) for g in G}
    print(f"逐条 predict: {len(Gset)} 对, {t1-t0:.1f}s")

    # 三种预扫描配置
    for cfg in ("limit12", "ybreak", "limit12_ybreak"):
        t0 = time.time()
        C = precollect(parser, copy.deepcopy(parser.boxes), cfg)
        t1 = time.time()
        covered = Gset <= C
        print(f"[{cfg:<15}] C={len(C):>6}  G⊆C={covered}  "
              f"预扫描+特征耗时 {t1-t0:.1f}s")
        if not covered:
            miss = sorted(Gset - C)[:8]
            print(f"   缺失样例(cidx_up,cidx_down): {miss}")

    # 批量 predict 与逐条一致性（对 G 全部特征）
    fea_arr = np.array([g[2] for g in G], dtype=np.float32)
    probs_batch = parser.updown_cnt_mdl.inplace_predict(fea_arr)
    probs_seq = [g[3] for g in G]
    maxdev = max(abs(float(a) - float(b))
                 for a, b in zip(probs_seq, probs_batch))
    # 边界翻转检查（跨 0.5 阈值才影响合并决策）
    flip = sum(1 for a, b in zip(probs_seq, probs_batch)
               if (a <= 0.5) != (b <= 0.5))
    print(f"逐条 vs 批量 predict: 最大偏差 {maxdev:.2e}, 跨阈值翻转 {flip} 条")


if __name__ == "__main__":
    main()

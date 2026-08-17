# -*- coding: utf-8 -*-
"""
统一结果模型 + Fitz 预检 + 快速路径（升级计划 Step 2）

三层路由：
  1. 全部页面为「可编辑文本」                    -> Fitz 原生快速提取（约 0.01s/页，
                                                     表格区域原位重建）
  2. 混有少量扫描/混合页                      -> Fitz 快速提取文本页 + 旧引擎只在扫描页上跑
                                                  完整管线（局部慢路径，`ocr_depth` 可降级）
  3. 扫描/复杂为主                            -> 旧引擎（RAGFlowPdfParser）全量解析，
                                                 表格走 TSR + 表格结构识别

上层的 API 层负责：文件预检稳定错误码、文件大小限制、线程池调度、输出截断。
本模块保证：
  - 返回统一结果模型 ParseDocument / ParseChunk
  - 只记录文件哈希与统计信息，不记录合同正文（日志脱敏）
"""
import hashlib
import html
import json
import logging
import os
import re
import threading
import time
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# 表格候选必须有足够的已填充单元格。仅统计页面图元会把论文中的
# 折线图、坐标轴和网络结构图误判成表格。
_TABLE_MIN_OCCUPANCY = 0.45
# 文本对齐表格检测比线框检测昂贵，只对稀疏页面启用。长篇论文正文页即使
# 没有线框候选，也不应被当作无框表格整页扫描。
_TABLE_TEXT_FALLBACK_MAX_WORDS = 200
# 一页中文本块数量上限（高于此值不太可能是合同正文页）
_ABS_MAX_BLOCKS = 400


@dataclass
class PageInfo:
    """单页分类结果（预检产出）。"""
    page_no: int            # 0-based 页码
    page_kind: str          # text | mixed | scan | complex | blank
    text_chars: int         # 页内文本字符数（去空白）
    image_count: int        # 页内图像块数量
    has_text_layer: bool
    engine: str = "fitz"    # fitz | slow_ocr | slow_full | skip


@dataclass
class ModelConfig:
    """快速路径 / 路由可调参数（环境变量可覆盖）。"""
    min_text_chars_per_page: int = 20       # 低于此字符数视为无文本层
    zoom_in: int = 3                        # OCR 渲染 DPI 倍数（与旧引擎一致）
    max_ocr_pages_for_partial: int = 4      # 仅有 <= 4 个扫描/混合页时走局部慢路径
    max_mixed_ratio: float = 0.35           # 扫描+混合页占比阈值（超出走全量慢路径）
    ocr_depth: str = "full"                 # full | fast | skip（ocr_depth 降级）
    max_chunks: int = 200                   # 结果块上限（超出截断，与旧 behavior 一致）
    # 原生文字 PDF 只在复杂表格裁剪区域调用 DeepDoc TSR；off 可完全关闭。
    selective_table_engine: str = "deepdoc"  # deepdoc | off
    selective_table_min_spans: int = 80      # 低于该复杂度继续用 Fitz 快速重建
    selective_table_max_regions: int = 2     # 单页最多调用 TSR 的表格区域数
    selective_table_scale: int = 2           # 裁剪渲染倍数（144 DPI）

    @staticmethod
    def from_env() -> "ModelConfig":
        def _int(name: str, default: int) -> int:
            return int(os.getenv(name, str(default)))

        depth = os.getenv("OCR_DEPTH", "full").strip().lower()
        if depth not in ("full", "fast", "skip"):
            depth = "full"
        table_engine = os.getenv(
            "SELECTIVE_TABLE_ENGINE", "deepdoc").strip().lower()
        if table_engine not in ("deepdoc", "off"):
            table_engine = "deepdoc"
        return ModelConfig(
            min_text_chars_per_page=_int("MIN_TEXT_CHARS_PER_PAGE", 20),
            zoom_in=_int("ZOOM_IN", 3),
            max_ocr_pages_for_partial=_int("MAX_OCR_PAGES_PARTIAL", 4),
            max_mixed_ratio=float(os.getenv("MAX_MIXED_RATIO", "0.35")),
            ocr_depth=depth,
            max_chunks=_int("MAX_CHUNKS", 200),
            selective_table_engine=table_engine,
            selective_table_min_spans=_int("SELECTIVE_TABLE_MIN_SPANS", 80),
            selective_table_max_regions=_int("SELECTIVE_TABLE_MAX_REGIONS", 2),
            selective_table_scale=max(1, _int("SELECTIVE_TABLE_SCALE", 2)),
        )


@dataclass
class ParseChunk:
    """统一结果块。

    - 文本块：text 中附带 @@pages\\tx0\\tx1\\ttop\\tbottom## 位置标签（与旧引擎一致，
      便于上层统一 parse_positions / strip_positions）。
    - 表格块：kind="table"，text 为 TSR / 快速重建的行文本。
    - 图像块：kind="figure" | "figure_or_table"，content 为数据 URI 或 PIL 图像。
    """
    text: str = ""
    tag: str = ""
    kind: str = "text"          # text | table | figure | figure_or_table
    page: int = 0               # 0-based 页号
    positions: List[dict] = field(default_factory=list)
    clean_text: str = ""
    # 附加元信息（阶段 B / v2 使用）
    meta: Dict = field(default_factory=dict)


@dataclass
class ParseDocument:
    """统一解析结果模型。"""
    file_name: str = ""
    doc_sha256: str = ""                    # 文件哈希（日志记录用，不记正文）
    total_pages: int = 0
    chunks: List[ParseChunk] = field(default_factory=list)
    media: List[dict] = field(default_factory=list)   # 图像/表格裁剪（PIL 图像）
    page_kinds: List[dict] = field(default_factory=list)  # 每页分类（统计用）
    engine: str = ""                        # route: fitz_fast | hybrid | slow_full
    stats: Dict = field(default_factory=dict)   # 解析统计（耗时/页数等）
    error: Optional[str] = None


_PAGE_TAG_RE = re.compile(
    r"@@([0-9\-]+)\t([0-9.]+)\t([0-9.]+)\t([0-9.]+)\t([0-9.]+)##")

# 部分论文使用分段括号字形；PyMuPDF 会把它们解码为控制符或 Adobe
# 私有区字符。统一成普通方括号，避免 RAG 文本中残留不可见字符。
_TABLE_GLYPH_TRANSLATION = str.maketrans({
    "\x14": "[", "\x15": "]",
    "\uf8ee": "[", "\uf8f0": "[",
    "\uf8f9": "]", "\uf8fb": "]",
})

# 模型按需初始化，普通正文和简单表格不会产生任何模型开销。ONNX session
# 的首次创建与推理分别加锁，避免 API 线程池并发请求时重复加载或争用 session。
_SELECTIVE_TSR = None
_SELECTIVE_TSR_INIT_LOCK = threading.Lock()
_SELECTIVE_TSR_INFER_LOCK = threading.Lock()


def strip_positions(text: str) -> str:
    """去掉 @@...## 位置标签，保留纯文本。"""
    if not text:
        return text
    return _PAGE_TAG_RE.sub("", text)


def parse_positions(text: str) -> List[dict]:
    """从文本中的 @@...## 标签解析位置信息（兼容上层）。"""
    out = []
    if not text or "@@" not in text:
        return out
    for m in _PAGE_TAG_RE.finditer(text):
        pages, x0, x1, top, bottom = m.groups()
        try:
            page_list = [int(p) for p in pages.split("-") if p]
            out.append({
                "pages": page_list,
                "x0": float(x0), "x1": float(x1),
                "top": float(top), "bottom": float(bottom),
            })
        except Exception:
            continue
    return out


def compute_doc_sha256(path: str) -> str:
    """计算文件哈希（日志脱敏：只记哈希与统计，不记正文）。"""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for b in iter(lambda: f.read(65536), b""):
                h.update(b)
    except OSError:
        return ""
    return h.hexdigest()


# ---------------------------------------------------------------------------
# 每页分类器
# ---------------------------------------------------------------------------

_PUNCT_SPACE = set(' \t\r\n　、。，；：？！“”‘’（）《》〈〉【】')


def _text_chars(text: str) -> int:
    if not text:
        return 0
    return sum(1 for ch in text if ch not in _PUNCT_SPACE)


def classify_page(page, cfg: ModelConfig) -> PageInfo:
    """基于 fitz 的轻量单页分类。

    规则（与计划 Step 2 一致）：
      - 文本字符 >= min_text && 有图像块    -> mixed
      - 文本字符 >= min_text && 无图像块    -> text
      - 有图像块（通常无文本层）             -> scan
      - 无文本且无图像                       -> blank
    """
    blocks = page.get_text("blocks")

    text_chars = 0
    has_image = False
    image_count = 0
    for b in blocks:
        t = b[4]
        typ = b[6]
        if typ == 1:
            has_image = True
            image_count += 1
        else:
            text_chars += _text_chars(t)
    # 扫描页通常以 Image XObject 形式存在（insert_image / 扫描渲染），
    # get_text('blocks') 不把它们列为 type=1 块，需用 get_images 兜底。
    try:
        img_xobj = page.get_images(full=True)
    except Exception:
        img_xobj = []
    if img_xobj:
        has_image = True
        image_count = max(image_count, len(img_xobj))
    if text_chars >= cfg.min_text_chars_per_page and has_image:
        kind = "mixed"
    elif text_chars >= cfg.min_text_chars_per_page:
        kind = "text"
    elif has_image:
        kind = "scan"
    else:
        kind = "blank"
    return PageInfo(
        page_no=page.number,
        page_kind=kind,
        text_chars=text_chars,
        image_count=image_count,
        has_text_layer=text_chars >= cfg.min_text_chars_per_page,
    )


def classify_pages(doc, cfg: ModelConfig) -> List[PageInfo]:
    return [classify_page(page, cfg) for page in doc]


def _find_table_regions(page) -> List[Tuple[float, float, float, float]]:
    """返回可信的表格区域，过滤图表坐标轴和网络结构图。

    优先使用严格线框策略，并以行列数、有效单元格占比和区域尺寸过滤。
    对没有线框候选的页面，再用文本对齐策略兜底，以覆盖无框、合并单元格
    和跨页表格。文本策略容易把整张双栏页面识别成表格，因此额外限制候选
    高度和宽度。
    """
    find_tables = getattr(page, "find_tables", None)
    if not callable(find_tables):
        return []

    page_width = float(page.rect.width)
    page_height = float(page.rect.height)
    words = page.get_text("words")

    # 同时缓存长横线：它既用于稠密正文页预筛，也用于把 PyMuPDF 仅识别到
    # 内部竖线的“半张表”扩回完整边界。
    horizontal_edges = []
    try:
        for drawing in page.get_drawings():
            for item in drawing.get("items", []):
                if item[0] == "l":
                    p0, p1 = item[1], item[2]
                    if (abs(p0.y - p1.y) < 1
                            and abs(p0.x - p1.x) >= page_width * 0.12):
                        horizontal_edges.append(
                            (float(min(p0.x, p1.x)), float(p0.y),
                             float(max(p0.x, p1.x))))
                elif item[0] == "re":
                    rect = item[1]
                    if rect.width >= page_width * 0.12:
                        horizontal_edges.extend([
                            (float(rect.x0), float(rect.y0), float(rect.x1)),
                            (float(rect.x0), float(rect.y1), float(rect.x1)),
                        ])
    except Exception:
        logger.debug("PDF 图元预筛失败", exc_info=True)

    # 稠密正文页如果连两条长横线都没有，就不运行昂贵且易误判的表格检测。
    if (len(words) > _TABLE_TEXT_FALLBACK_MAX_WORDS
            and len(horizontal_edges) < 2):
        return []

    def collect(strategy: Optional[str] = None, *, text_fallback: bool = False,
                **find_kwargs):
        accepted = []
        try:
            if strategy is not None:
                find_kwargs["strategy"] = strategy
            tables = find_tables(**find_kwargs).tables
        except Exception:
            logger.debug("PyMuPDF 表格检测失败: strategy=%s kwargs=%s",
                         strategy, find_kwargs,
                         exc_info=True)
            return accepted

        for table in tables:
            rows = int(table.row_count)
            cols = int(table.col_count)
            if rows < 2 or cols < 2:
                continue
            try:
                data = table.extract()
            except Exception:
                continue
            nonempty = sum(
                1 for row in data for cell in row
                if cell is not None and str(cell).strip()
            )
            occupancy = nonempty / max(rows * cols, 1)
            if nonempty < 4 or occupancy < _TABLE_MIN_OCCUPANCY:
                continue

            x0, y0, x1, y1 = (float(v) for v in table.bbox)
            width, height = x1 - x0, y1 - y0
            if width < page_width * 0.12 or height < 18:
                continue
            if text_fallback and (
                width < page_width * 0.35
                or height > page_height * 0.50
                or rows > 40
            ):
                continue
            accepted.append((x0, y0, x1, y1))
        return accepted

    regions = collect("lines_strict")

    # 论文表常只有横线，严格线框策略会漏掉整张表或只返回中间几列。
    # “竖线严格 + 横向文本”能补出这类候选；图表坐标轴通常形成大量空格，
    # 会被上面的单元格占用率过滤掉。
    if (len(horizontal_edges) >= 2
            and (regions or len(words) <= _TABLE_TEXT_FALLBACK_MAX_WORDS)):
        regions.extend(collect(
            vertical_strategy="lines_strict",
            horizontal_strategy="text",
        ))

    if (not regions and len(words) <= _TABLE_TEXT_FALLBACK_MAX_WORDS):
        regions = collect("text", text_fallback=True)

    def should_merge(a, b):
        ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
        iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
        if ix and iy:
            inter = ix * iy
            area = min((a[2] - a[0]) * (a[3] - a[1]),
                       (b[2] - b[0]) * (b[3] - b[1]))
            return inter / max(area, 1.0) >= 0.3
        return False

    # 合并同一表格被不同策略识别出的重叠候选。
    merged = []
    for region in sorted(regions, key=lambda r: (r[1], r[0])):
        for idx, existing in enumerate(merged):
            if should_merge(existing, region):
                merged[idx] = (
                    min(existing[0], region[0]), min(existing[1], region[1]),
                    max(existing[2], region[2]), max(existing[3], region[3]),
                )
                break
        else:
            merged.append(region)

    # 用同一高度范围内的真实横线扩展边界，恢复首列、末列和表头/表尾。
    expanded = []
    for region in merged:
        x0, y0, x1, y1 = region
        for edge_x0, edge_y, edge_x1 in horizontal_edges:
            x_overlap = max(0.0, min(x1, edge_x1) - max(x0, edge_x0))
            if (y0 - 4 <= edge_y <= y1 + 4
                    and x_overlap >= min(x1 - x0, edge_x1 - edge_x0) * 0.25):
                x0, x1 = min(x0, edge_x0), max(x1, edge_x1)
                y0, y1 = min(y0, edge_y), max(y1, edge_y)
        expanded.append((x0, y0, x1, y1))
    return expanded


def _page_has_table_lines(page) -> bool:
    """页面是否包含可信表格区域（兼容原调用名）。"""
    return bool(_find_table_regions(page))


def _route_from_infos(path: str, infos: List[PageInfo],
                      cfg: ModelConfig) -> str:
    """基于预检结果返回路由字符串。

    route:
      - fitz_fast   全文本（含表格区域）     -> Fitz 快速提取
      - hybrid      少量扫描/混合页         -> Fitz 文本页 + 局部慢路径
      - slow_full   扫描/复杂为主            -> 旧引擎全量（TSR）
    """
    scan_cnt = sum(1 for p in infos if p.page_kind in ("scan", "mixed"))
    total = max(len(infos), 1)
    scan_ratio = scan_cnt / total

    # 文本层中的表格现在由 Fitz 快速路径做区域级重建，无需为了表格再次
    # 扫描整本文档或切换到慢路径。
    if scan_cnt == 0:
        return "fitz_fast"
    if (scan_ratio <= cfg.max_mixed_ratio
            and scan_cnt <= cfg.max_ocr_pages_for_partial):
        return "hybrid"
    return "slow_full"


def route_pdf(path: str, cfg: ModelConfig) -> Tuple[str, List[PageInfo]]:
    """顶层路由：预检 + 返回 (route, page_infos)。"""
    import fitz
    doc = fitz.open(path)
    try:
        infos = classify_pages(doc, cfg)
    finally:
        doc.close()
    return _route_from_infos(path, infos, cfg), infos


# ---------------------------------------------------------------------------
# Fitz 快速文本提取
# ---------------------------------------------------------------------------

def _line_tag(pno: int, x0: float, x1: float, top: float, bottom: float) -> str:
    """构造 @@pages\\tx0\\tx1\\ttop\\tbottom## 标签（与旧引擎 _line_tag 兼容）。"""
    return "@@{}\t{:.1f}\t{:.1f}\t{:.1f}\t{:.1f}##".format(
        str(pno + 1), x0, x1, top, bottom)


def _block_to_chunks(page_no: int, block, cfg: ModelConfig) -> List[ParseChunk]:
    """单个文本块 -> 逐行 ParseChunk（保持与旧引擎 per-line 粒度一致）。

    页号标签固定为该块所在页（get_text('blocks') 不会跨页）。
    """
    x0, y0, x1, y1, text, _bno, _btype = block
    out = []
    lines = [ln.rstrip() for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return out
    # 行高近似：整块高度 / 行数（单行块则用块高）
    n = len(lines)
    ystep = (y1 - y0) / max(n, 1)
    for i, ln in enumerate(lines):
        yt = y0 + i * ystep
        yb = yt + ystep
        ck = ParseChunk(
            text=ln,
            tag=_line_tag(page_no, x0, x1, yt, yb),
            kind="text",
            page=page_no,
            clean_text=ln,
        )
        out.append(ck)
    return out


def _detect_columns(page) -> List[float]:
    """粗略检测双栏文本：按文本块左侧 x 聚类，返回栏的左边界 x。"""
    xs = [b[0] for b in page.get_text("blocks") if b[6] == 0 and (b[4] or "").strip()]
    if len(xs) < 8:
        return [72.0]
    xs_sorted = sorted(set(round(x) for x in xs))
    # 分簇：间隔 > 80pt 视为不同栏
    clusters = [[xs_sorted[0]]]
    for x in xs_sorted[1:]:
        if x - clusters[-1][-1] > 80:
            clusters.append([])
        clusters[-1].append(x)
    return [sum(c) / len(c) for c in clusters]


def _get_text_blocks_text(page) -> str:
    """page.get_text('blocks') 中所有文本块拼接（无位置标签）。"""
    return "\n".join(b[4] for b in page.get_text("blocks")
                     if b[6] == 0 and (b[4] or "").strip())


def _reassemble_table_lines(
        page, clip: Optional[Tuple[float, float, float, float]] = None
        ) -> List[Tuple[float, str, float, float, float, float]]:
    """在指定表格区域内从 span 级坐标重建行文本。

    返回 [(row_top, text, x0, x1, top, bottom), ...]（按视觉行 Y 排序，
    同 Y 内按 X 序拼接单元格，实现跨表格列的高质量读取）。
    """
    spans = _native_table_spans(page, clip)
    if not spans:
        return []
    # 行聚类：论文表中的“×2 / ×3”等上标会比基线高约半个字高，容差稍大
    # 于普通正文，仍远小于相邻表格行的间距。
    heights = [b[3] - b[1] for b, _ in spans]
    row_tol = (sorted(heights)[len(heights) // 2]) * 0.8 + 1
    spans.sort(key=lambda s: (s[0][1], s[0][0]))
    rows = []
    for bbox, t in spans:
        placed = False
        for row in rows:
            if abs(row["y"] - (bbox[1] + bbox[3]) / 2) <= row_tol:
                row["items"].append((bbox, t))
                placed = True
                break
        if not placed:
            rows.append({"y": (bbox[1] + bbox[3]) / 2, "items": [(bbox, t)]})
    res = []
    for row in rows:
        row["items"].sort(key=lambda it: it[0][0])
        text = " ".join(t for _, t in row["items"])
        x0 = min(b[0] for b, _ in row["items"])
        x1 = max(b[2] for b, _ in row["items"])
        top = min(b[1] for b, _ in row["items"])
        bottom = max(b[3] for b, _ in row["items"])
        res.append((row["y"], text, x0, x1, top, bottom))
    res.sort(key=lambda r: r[0])
    return res


def _native_table_spans(
        page, clip: Optional[Tuple[float, float, float, float]] = None
        ) -> List[Tuple[Tuple[float, float, float, float], str]]:
    """读取表格区域内的原生 PDF span；TSR 只负责结构，不重复 OCR。"""
    spans = []
    for blk in page.get_text("dict").get("blocks", []):
        if blk.get("type") != 0:
            continue
        for line in blk.get("lines", []):
            for sp in line.get("spans", []):
                text = (sp.get("text") or "").translate(
                    _TABLE_GLYPH_TRANSLATION).strip()
                if not text:
                    continue
                bbox = tuple(float(v) for v in sp["bbox"])
                if clip is not None:
                    cx = (bbox[0] + bbox[2]) / 2
                    cy = (bbox[1] + bbox[3]) / 2
                    if not (clip[0] <= cx <= clip[2]
                            and clip[1] <= cy <= clip[3]):
                        continue
                spans.append((bbox, text))
    return spans


def _native_table_tokens(page, clip):
    """把 PDF span 拆成可映射到单元格的词级 token。

    直接使用 get_text("words") 会吞掉控制区括号，并把上标 9 合并成 109；
    因此在 span 层按空白做等比例切分，同时保留括号和上标语义。
    """
    tokens = []
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                raw_text = span.get("text") or ""
                translated = raw_text.translate(_TABLE_GLYPH_TRANSLATION)
                if not translated.strip():
                    continue
                x0, y0, x1, y1 = (float(v) for v in span["bbox"])
                width = max(x1 - x0, 0.1)
                text_length = max(len(translated), 1)
                for match in re.finditer(r"\S+", translated):
                    token = match.group(0)
                    token_x0 = x0 + width * match.start() / text_length
                    token_x1 = x0 + width * match.end() / text_length
                    bbox = (token_x0, y0, token_x1, y1)
                    cx = (token_x0 + token_x1) / 2
                    cy = (y0 + y1) / 2
                    if not (clip[0] <= cx <= clip[2]
                            and clip[1] <= cy <= clip[3]):
                        continue
                    if int(span.get("flags", 0)) & 1 and token not in ("[", "]"):
                        token = "^" + token
                    tokens.append((bbox, token))
    return tokens


def _table_region_needs_structure(page, region, cfg: ModelConfig) -> bool:
    """只把宽、密、跨多行的复杂表格送入 TSR。

    简单小表直接用坐标重建已经足够准确；这个门槛主要捕获单元格内容被
    拆成大量 PDF span 的网络结构表、财报密集表等，避免全页模型推理。
    """
    if cfg.selective_table_engine != "deepdoc":
        return False
    spans = _native_table_spans(page, region)
    if len(spans) < cfg.selective_table_min_spans:
        return False
    width_ratio = (region[2] - region[0]) / max(float(page.rect.width), 1.0)
    if width_ratio < 0.45:
        return False
    visual_rows = _reassemble_table_lines(page, region)
    return len(visual_rows) >= 5


def _get_selective_table_recognizer():
    """懒加载 DeepDoc 原生 TSR，且不受全局 TABLE_ENGINE 设置影响。"""
    global _SELECTIVE_TSR
    if _SELECTIVE_TSR is not None:
        return _SELECTIVE_TSR
    with _SELECTIVE_TSR_INIT_LOCK:
        if _SELECTIVE_TSR is None:
            from vision.table_structure_recognizer import TableStructureRecognizer
            _SELECTIVE_TSR = TableStructureRecognizer()
    return _SELECTIVE_TSR


def _dedupe_structure_boxes(boxes, start_key: str, end_key: str,
                            overlap_threshold: float = 0.75):
    """删除 TSR 对同一行/列给出的高重叠重复框，保留高置信度框。"""
    valid = []
    for box in boxes:
        start = float(box.get(start_key, 0.0))
        end = float(box.get(end_key, 0.0))
        if end - start > 1.0:
            valid.append(box)
    kept = []
    for box in sorted(valid, key=lambda b: float(b.get("score", 0.0)),
                      reverse=True):
        start = float(box[start_key])
        end = float(box[end_key])
        duplicate = False
        for existing in kept:
            ex_start = float(existing[start_key])
            ex_end = float(existing[end_key])
            overlap = max(0.0, min(end, ex_end) - max(start, ex_start))
            smaller = min(end - start, ex_end - ex_start)
            if overlap / max(smaller, 1.0) >= overlap_threshold:
                duplicate = True
                break
        if not duplicate:
            kept.append(box)
    return sorted(kept, key=lambda b: float(b[start_key]))


def _axis_intervals(boxes, start_key: str, end_key: str, offset: float,
                    scale: float, region_start: float, region_end: float):
    """把模型坐标变换为连续 PDF 行/列区间，并返回原始轴覆盖率。"""
    raw = []
    for box in _dedupe_structure_boxes(boxes, start_key, end_key):
        start = max(region_start, offset + float(box[start_key]) / scale)
        end = min(region_end, offset + float(box[end_key]) / scale)
        if end - start > max((region_end - region_start) * 0.012, 1.0):
            raw.append((start, end, float(box.get("score", 0.0))))
    if not raw:
        return [], 0.0

    # 合并区间仅用于质量检查，防止模型只覆盖了裁剪的一小角。
    union = 0.0
    union_start, union_end = raw[0][0], raw[0][1]
    for start, end, _score in raw[1:]:
        if start <= union_end:
            union_end = max(union_end, end)
        else:
            union += union_end - union_start
            union_start, union_end = start, end
    union += union_end - union_start
    coverage = union / max(region_end - region_start, 1.0)

    # 相邻预测框可能有几像素重叠或留白，以中点作公共边界，保证每个原生
    # span 只进入一个单元格且不会因模型缝隙丢字。
    boundaries = [region_start]
    for idx in range(len(raw) - 1):
        boundary = (raw[idx][1] + raw[idx + 1][0]) / 2
        boundaries.append(max(boundaries[-1], min(boundary, region_end)))
    boundaries.append(region_end)
    intervals = [
        (boundaries[idx], boundaries[idx + 1], raw[idx][2])
        for idx in range(len(raw))
        if boundaries[idx + 1] - boundaries[idx] > 0.5
    ]
    return intervals, coverage


def _join_table_cell(items) -> str:
    """按单元格内的视觉行、横向间距拼回文字。"""
    if not items:
        return ""
    opening = [item for item in items if item[1] == "["]
    closing = [item for item in items if item[1] == "]"]
    content = [item for item in items if item[1] not in ("[", "]")]

    suffix = []
    if closing:
        close_right = min(item[0][2] for item in closing)
        suffix = [item for item in content if item[0][0] >= close_right - 0.5]
        content = [item for item in content if item not in suffix]
    if not content:
        content = []

    heights = sorted(max(1.0, bbox[3] - bbox[1])
                     for bbox, _text in (content or items))
    # 词框中“×”等数学符号会把 bbox 撑高；用较低四分位作基准，避免把
    # 单元格内相邻的 2~3 行卷积参数错误地粘成同一行。
    base_height = heights[max(0, len(heights) // 4)]
    row_tolerance = base_height * 0.35 + 0.35
    lines = []
    for bbox, text in sorted(content, key=lambda item: (item[0][1], item[0][0])):
        baseline = bbox[1]
        for line in lines:
            if abs(line["baseline"] - baseline) <= row_tolerance:
                line["items"].append((bbox, text))
                line["baseline"] = sum(
                    entry[0][1]
                    for entry in line["items"]
                ) / len(line["items"])
                break
        else:
            lines.append({"baseline": baseline, "items": [(bbox, text)]})

    rendered = []
    for line in sorted(lines, key=lambda entry: entry["baseline"]):
        ordered = sorted(line["items"], key=lambda item: item[0][0])
        pieces = []
        previous_right = None
        for bbox, text in ordered:
            if previous_right is not None:
                gap = bbox[0] - previous_right
                if gap > max(0.8, base_height * 0.18):
                    pieces.append(" ")
            pieces.append(text)
            previous_right = max(previous_right or bbox[2], bbox[2])
        value = "".join(pieces).strip()
        value = re.sub(r"\[\s+", "[", value)
        value = re.sub(r"\s+\]", "]", value)
        value = re.sub(r"\s+([,.;:])", r"\1", value)
        rendered.append(value)
    value = " / ".join(value for value in rendered if value)
    if opening:
        value = "[" + value
    if closing:
        value += "]"
    if suffix:
        suffix_text = "".join(
            text for _bbox, text in sorted(suffix, key=lambda item: item[0][0]))
        value += suffix_text
    value = re.sub(r"\bconv([2-5])\s+x\b", r"conv\1_x", value)
    return value


def _structured_table_chunks(page, pno: int, region, components, scale: int,
                             inference_ms: float) -> Optional[List[ParseChunk]]:
    """TSR 行列框 + 原生 PDF span -> 结构化表格行；不通过质量门则返回 None。"""
    column_boxes = [box for box in components
                    if box.get("label") == "table column"]
    row_boxes = [box for box in components
                 if box.get("label") == "table row"]
    columns, column_coverage = _axis_intervals(
        column_boxes, "x0", "x1", region[0], scale, region[0], region[2])
    rows, row_coverage = _axis_intervals(
        row_boxes, "top", "bottom", region[1], scale, region[1], region[3])

    if not (2 <= len(columns) <= 12 and 2 <= len(rows) <= 50):
        return None
    mean_column_score = sum(item[2] for item in columns) / len(columns)
    mean_row_score = sum(item[2] for item in rows) / len(rows)
    if (column_coverage < 0.65 or row_coverage < 0.65
            or mean_column_score < 0.35 or mean_row_score < 0.30):
        return None

    tokens = _native_table_tokens(page, region)
    if not tokens:
        return None
    cells = [[[] for _column in columns] for _row in rows]
    assigned_chars = 0
    total_chars = sum(max(1, len(text.replace(" ", "")))
                      for _bbox, text in tokens)
    for bbox, text in tokens:
        cx = (bbox[0] + bbox[2]) / 2
        cy = (bbox[1] + bbox[3]) / 2
        row_idx = next((idx for idx, (top, bottom, _score) in enumerate(rows)
                        if top <= cy <= bottom), None)
        col_idx = next((idx for idx, (left, right, _score) in enumerate(columns)
                        if left <= cx <= right), None)
        if row_idx is None or col_idx is None:
            continue
        cells[row_idx][col_idx].append((bbox, text))
        assigned_chars += max(1, len(text.replace(" ", "")))

    retention = assigned_chars / max(total_chars, 1)
    values = [[_join_table_cell(cell) for cell in row] for row in cells]
    colspans = [[1 for _column in columns] for _row in rows]
    # 当后半部分有至少 3 个并列数据列，且多行都填满时，稀疏居中的操作行
    # 通常是视觉上横跨这些列的共享单元格（max pool / average pool 等）。
    # 模型偶尔漏报 spanning cell，这里只在表格形态证据充分时做保守修复。
    tail_count = max(0, len(columns) - 2)
    dense_tail_rows = sum(
        sum(bool(cell) for cell in row[2:]) >= max(3, tail_count // 2)
        for row in values
    )
    if tail_count >= 3 and dense_tail_rows >= 2:
        for row_idx, row in enumerate(values):
            occupied = [idx for idx in range(2, len(row)) if row[idx]]
            if 1 <= len(occupied) <= 2:
                shared = " ".join(row[idx] for idx in occupied).strip()
                for idx in range(2, len(row)):
                    row[idx] = ""
                row[2] = shared
                colspans[row_idx][2] = tail_count
                for idx in range(3, len(row)):
                    colspans[row_idx][idx] = 0
    nonempty_rows = sum(any(cell for cell in row) for row in values)
    nonempty_columns = sum(
        any(values[row_idx][col_idx] for row_idx in range(len(values)))
        for col_idx in range(len(columns))
    )
    if (retention < 0.90 or nonempty_rows < max(2, len(rows) // 2)
            or nonempty_columns < max(2, len(columns) // 2)):
        return None

    html_rows = []
    for row_idx, row in enumerate(values):
        html_cells = []
        for col_idx, cell in enumerate(row):
            span = colspans[row_idx][col_idx]
            if span == 0:
                continue
            attr = f' colspan="{span}"' if span > 1 else ""
            html_cells.append(f"<td{attr}>{html.escape(cell)}</td>")
        html_rows.append("<tr>" + "".join(html_cells) + "</tr>")
    table_html = "<table><tbody>" + "".join(html_rows) + "</tbody></table>"

    result = []
    base_meta = {
        "table": True,
        "structured": True,
        "engine": "deepdoc_tsr",
        "rows": len(rows),
        "columns": len(columns),
        "text_retention": round(retention, 4),
        "inference_ms": round(inference_ms, 1),
    }
    for row_idx, row in enumerate(values):
        text = " | ".join(row)
        top, bottom, _score = rows[row_idx]
        meta = dict(base_meta, row_index=row_idx, cells=row)
        meta["colspans"] = colspans[row_idx]
        if row_idx == 0:
            meta["html"] = table_html
        result.append(ParseChunk(
            text=text,
            tag=_line_tag(pno, region[0], region[2], top, bottom),
            kind="table",
            page=pno,
            clean_text=text,
            meta=meta,
        ))
    return result


def _selective_table_outputs(page, pno: int, regions, cfg: ModelConfig):
    """按复杂度选择表格裁剪区域，推理失败或低质量时让调用方快速回退。"""
    if cfg.selective_table_engine != "deepdoc":
        return {}
    selected = [
        idx for idx, region in enumerate(regions)
        if _table_region_needs_structure(page, region, cfg)
    ][:max(0, cfg.selective_table_max_regions)]
    if not selected:
        return {}

    try:
        import fitz
        from PIL import Image
        recognizer = _get_selective_table_recognizer()
    except Exception:
        logger.exception("选择性表格结构模型初始化失败，回退 Fitz 重建")
        return {}

    outputs = {}
    scale = max(1, cfg.selective_table_scale)
    for region_idx in selected:
        region = regions[region_idx]
        try:
            pix = page.get_pixmap(
                matrix=fitz.Matrix(scale, scale),
                clip=fitz.Rect(region), alpha=False)
            image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            started = time.perf_counter()
            with _SELECTIVE_TSR_INFER_LOCK:
                prediction = recognizer([image], thr=0.2)
            inference_ms = (time.perf_counter() - started) * 1000
            components = prediction[0] if prediction else []
            structured = _structured_table_chunks(
                page, pno, region, components, scale, inference_ms)
            if structured:
                outputs[region_idx] = structured
                logger.info(
                    "选择性 TSR 接受 page=%s region=%s rows=%s cols=%s ms=%.1f",
                    pno + 1, region_idx, structured[0].meta["rows"],
                    structured[0].meta["columns"], inference_ms)
            else:
                logger.info(
                    "选择性 TSR 质量门回退 page=%s region=%s ms=%.1f",
                    pno + 1, region_idx, inference_ms)
        except Exception:
            logger.exception(
                "选择性 TSR 推理失败 page=%s region=%s，回退 Fitz 重建",
                pno + 1, region_idx)
    return outputs


def parse_fitz_fast(path: str, cfg: ModelConfig) -> ParseDocument:
    """Fitz 原生快速提取（route=fitz_fast / 文本页部分）。"""
    import fitz
    doc = fitz.open(path)
    try:
        # 是否整本文档都走快速路径（无扫描页）
        infos = classify_pages(doc, cfg)
        only_text = all(p.page_kind in ("text", "blank", "complex") for p in infos)
        return _fitz_extract(doc, infos, cfg, only_text=only_text)
    finally:
        doc.close()


def _fitz_extract(doc, infos: List[PageInfo], cfg: ModelConfig,
                  only_text: bool = False,
                  need_image: bool = False) -> ParseDocument:
    chunks: List[ParseChunk] = []
    media = []
    table_pages = 0
    table_lines = 0
    selective_table_regions = 0

    for pg, info in zip(doc, infos):
        pno = pg.number
        if info.page_kind == "blank":
            continue
        if not info.has_text_layer:
            continue  # 扫描页由慢路径负责，这里跳过

        page_chunks = _page_text_chunks(pg, pno, cfg)
        page_table_lines = sum(1 for ck in page_chunks if ck.kind == "table")
        if page_table_lines:
            table_pages += 1
            table_lines += page_table_lines
        selective_table_regions += sum(
            1 for ck in page_chunks
            if ck.meta.get("engine") == "deepdoc_tsr"
            and ck.meta.get("row_index") == 0
        )
        chunks.extend(page_chunks)
        if need_image:
            media.extend(_crop_page_media(pg, pno, cfg))

    doc_sha = compute_doc_sha256(getattr(doc, "name", "") or "")
    return ParseDocument(
        total_pages=len(infos),
        doc_sha256=doc_sha,
        chunks=chunks,
        media=media,
        page_kinds=[{"page_no": p.page_no, "kind": p.page_kind,
                     "engine": "fitz"} for p in infos],
        engine="fitz_fast" if only_text else "hybrid_fitz_text",
        stats={"text_pages": len(infos), "ocr_pages": 0,
               "table_pages": table_pages, "table_lines": table_lines,
               "selective_table_regions": selective_table_regions},
    )


def _crop_page_media(page, pno: int, cfg: ModelConfig) -> List[dict]:
    """裁剪页面中的图像块（need_image 用）。"""
    from PIL import Image
    import io
    out = []
    for b in page.get_text("blocks"):
        if b[6] != 1:
            continue
        x0, y0, x1, y1 = b[:4]
        try:
            pm = page.get_pixmap(clip=(x0, y0, x1, y1), dpi=cfg.zoom_in * 72)
            img = Image.open(io.BytesIO(pm.tobytes("png"))).convert("RGB")
        except Exception:
            continue
        out.append({
            "image": img,
            "kind": "figure_or_table",
            "positions": [{"pages": [pno + 1], "x0": float(x0), "x1": float(x1),
                           "top": float(y0), "bottom": float(y1)}],
            "meta": {},
        })
    return out


# ---------------------------------------------------------------------------
# 局部慢路径（旧引擎只跑扫描/混合页）
# ---------------------------------------------------------------------------

class PartialPdfParser:
    """旧引擎局部解析器包装：

    仅对 [page_from, page_to) 页面执行 RAGFlowPdfParser 完整管线
    （pdfplumber 渲染 + OCR + YOLO 版面 + TSR + 上下拼接）。
    文本页面不参与，省去 pdfplumber 渲染与版面/OCR 开销。

    策略：不改动旧引擎的私有方法（其内部页码从 1 计且依赖全局 page_images），
    而是让旧引擎只看到 [page_from, page_to) 的局部页：
      - __images__ 按局部页渲染（pdfplumber 天然支持切片）；
      - 旧引擎内部 page_number 从 1 计，恰好映射回局部页；
      - 输出文本里的 @@ 页码标签通过 _global_repage 偏移到全局页号。
    """

    def __init__(self, page_from: int, page_to: int, **kwargs):
        from parser.pdf_parser import RAGFlowPdfParser
        self._inner = RAGFlowPdfParser(**kwargs)
        self._page_from = max(0, page_from)
        self._page_to = max(page_from, page_to)
        self._inner.parse_from = self._page_from
        self._inner.parse_to = self._page_to

    def parse(self, fnm: str, need_image: bool = False,
              zoomin: int = 3, need_position: bool = False):
        p = self._inner
        zoomin = p.__images__(fnm, zoomin, self._page_from, self._page_to)
        p._layouts_rec(zoomin)
        p._table_transformer_job(zoomin)
        p._text_merge()
        p._concat_downward()
        p._filter_forpages()
        tbls = p._extract_table_figure(
            need_image, zoomin, False, need_position)
        text_part = p._RAGFlowPdfParser__filterout_scraps(
            deepcopy(p.boxes), zoomin)
        text_part = _global_repage(text_part, self._page_from)
        return text_part, tbls


def _global_repage(text_part: str, offset: int) -> str:
    """把局部管线输出的 @@ 标签页码偏移为全局页号（局部页 FROM=offset 号开始）。"""
    if not offset:
        return text_part

    def rep(m):
        pages = m.group(1)
        shifted = "-".join(str(int(p) + offset) for p in pages.split("-") if p)
        return "@@{}\t{}\t{}\t{}\t{}##".format(
            shifted, m.group(2), m.group(3), m.group(4), m.group(5))

    return _PAGE_TAG_RE.sub(rep, text_part)


def _contiguous_ranges(pages: List[int]) -> List[Tuple[int, int]]:
    """把无序页号列表切成 [from, to) 连续区段（升序去重）。

    例: [1, 3, 4, 7] -> [(1, 2), (3, 5), (7, 8)]，直接对应
    PartialPdfParser(page_from, page_to) 的切片语义。
    """
    if not pages:
        return []
    pages = sorted(set(pages))
    ranges = []
    lo = hi = pages[0]
    for p in pages[1:]:
        if p == hi + 1:
            hi = p
        else:
            ranges.append((lo, hi + 1))
            lo = hi = p
    ranges.append((lo, hi + 1))
    return ranges


# ---------------------------------------------------------------------------
# 三层路由统一解析入口
# ---------------------------------------------------------------------------

def parse_pdf_document(path: str, cfg: Optional[ModelConfig] = None,
                       need_image: bool = False, max_chunks: Optional[int] = None
                       ) -> ParseDocument:
    """顶层入口：预检 -> 路由 -> 分层解析，产出统一 ParseDocument。

    max_chunks: 结果块上限；None 表示不截断（默认）。解析始终产出
    全量块（total 信息可通过 stats['truncated'] 复原），仅在返回时
    截断 chunks 列表。API 层负责最终的 max_chunks 截断并上报真实总数。
    """
    cfg = cfg or ModelConfig.from_env()

    import fitz
    # 轻量预检收敛到稳定错误码已在 API 层做过，这里再兜底一次
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return ParseDocument(error="空文件或无法读取", engine="reject")
    try:
        doc = fitz.open(path)
    except Exception as e:
        return ParseDocument(error=f"无法打开 PDF: {e}", engine="reject")
    n_pages = doc.page_count
    try:
        infos = classify_pages(doc, cfg)
    finally:
        doc.close()

    route = _route_from_infos(path, infos, cfg)

    if route == "fitz_fast":
        doc = fitz.open(path)
        try:
            result = _fitz_extract(doc, infos, cfg, only_text=True,
                                   need_image=need_image)
            result.engine = "fitz_fast"
        finally:
            doc.close()
        return _truncate_document(result, max_chunks)

    # ---- 需要旧引擎 ----
    from parser.pdf_parser import RAGFlowPdfParser
    ocr_depth = cfg.ocr_depth  # full|fast|skip
    ocr_pages = [i.page_no for i in infos
                 if i.page_kind in ("scan", "mixed")]

    if route == "hybrid":
        # 文本页用 fitz 快速提取，扫描/混合页用 PP-StructureV3
        text_chunks = []
        media = []
        doc = fitz.open(path)
        try:
            for pg in doc:
                info = infos[pg.number]
                if info.page_kind == "blank":
                    continue
                if info.has_text_layer:
                    text_chunks.extend(_page_text_chunks(pg, pg.number, cfg))
                    if need_image:
                        media.extend(_crop_page_media(pg, pg.number, cfg))
        finally:
            doc.close()

        ocr_chunks = []
        ocr_media = []
        if ocr_depth != "skip" and ocr_pages:
            # 扫描/混合页用 PP-StructureV3（替代旧引擎局部慢路径）
            try:
                ocr_chunks, ocr_media = _ppstructure_extract_pages(
                    path, ocr_pages, cfg, need_image=need_image)
            except Exception:
                logger.exception("PP-StructureV3 hybrid OCR 失败，降级跳过")
        all_chunks = _merge_by_position(
            text_chunks,
            ocr_chunks,
            infos,
        )
        return _truncate_document(ParseDocument(
            total_pages=n_pages,
            doc_sha256=compute_doc_sha256(path),
            chunks=all_chunks,
            media=media + ocr_media,
            page_kinds=[{"page_no": i.page_no, "kind": i.page_kind,
                         "engine": "fitz" if i.has_text_layer else "ppstructure_v3"}
                        for i in infos],
            engine="hybrid",
            stats={"text_pages": sum(1 for i in infos if i.has_text_layer),
                   "ocr_pages": len(ocr_pages),
                   "ocr_depth": ocr_depth},
        ), max_chunks)

    # ---- slow_full ----
    if ocr_depth == "skip":
        return ParseDocument(
            total_pages=n_pages,
            doc_sha256=compute_doc_sha256(path),
            chunks=[],
            page_kinds=[{"page_no": i.page_no, "kind": i.page_kind} for i in infos],
            engine="slow_full_skipped",
            stats={"ocr_depth": ocr_depth},
            error="ocr_depth=skip 且文档需要 OCR 路径",
        )
    # 使用 PP-StructureV3 替代旧引擎全量解析
    try:
        all_page_nos = [i.page_no for i in infos if i.page_kind != "blank"]
        ocr_chunks, ocr_media = _ppstructure_extract_pages(
            path, all_page_nos, cfg, need_image=need_image)
    except Exception:
        logger.exception("PP-StructureV3 全量解析失败")
        return ParseDocument(error="PP-StructureV3 全量解析失败", engine="slow_full")
    return _truncate_document(ParseDocument(
        total_pages=n_pages,
        doc_sha256=compute_doc_sha256(path),
        chunks=ocr_chunks,
        media=ocr_media,
        page_kinds=[{"page_no": i.page_no, "kind": i.page_kind,
                     "engine": "ppstructure_v3"} for i in infos],
        engine="slow_full",
        stats={"text_pages": sum(1 for i in infos if i.has_text_layer),
               "ocr_pages": len(ocr_pages), "ocr_depth": ocr_depth},
    ), max_chunks)


def _truncate_document(doc: ParseDocument, max_chunks: Optional[int]) -> ParseDocument:
    """返回时截断 chunks 列表（真实总数可通过 stats['truncated'] 复原）。"""
    if max_chunks is None or max_chunks <= 0:
        return doc
    total = len(doc.chunks)
    if total > max_chunks:
        doc.chunks = doc.chunks[:max_chunks]
        doc.stats["truncated"] = total - max_chunks
    return doc


def _page_text_chunks(page, pno: int, cfg: ModelConfig) -> List[ParseChunk]:
    """提取单页文本，仅在可信表格区域内重建表格行。

    保留 PDF 原始文本块顺序；遇到属于表格区域的第一个文本块时，以表格行
    替换该区域内的普通文本块。这样不会再因为页面上存在一个图表，就把整页
    双栏正文按 Y 坐标横向拼接。
    """
    chunks = []
    table_regions = _find_table_regions(page)
    structured_outputs = _selective_table_outputs(
        page, pno, table_regions, cfg)
    emitted = set()

    def emit_table(region_idx):
        structured = structured_outputs.get(region_idx)
        if structured:
            chunks.extend(structured)
            return
        for _row_y, text, x0, x1, top, bottom in _reassemble_table_lines(
                page, table_regions[region_idx]):
            ck = ParseChunk(
                text=text,
                tag=_line_tag(pno, x0, x1, top, bottom),
                kind="table",
                page=pno,
                clean_text=text,
            )
            ck.meta.update({
                "table": True,
                "structured": False,
                "engine": "fitz_table_lines",
            })
            chunks.append(ck)

    def matching_region(block):
        cx = (block[0] + block[2]) / 2
        cy = (block[1] + block[3]) / 2
        for idx, region in enumerate(table_regions):
            if region[0] <= cx <= region[2] and region[1] <= cy <= region[3]:
                return idx
        return None

    for b in page.get_text("blocks"):
        if b[6] != 0:
            continue
        region_idx = matching_region(b)
        if region_idx is not None:
            if region_idx in emitted:
                continue
            emitted.add(region_idx)
            emit_table(region_idx)
            continue
        chunks.extend(_block_to_chunks(pno, b, cfg))

    # 极少数表格区域可能没有独立文本块，仍保证其内容不会被漏掉。
    for region_idx, region in enumerate(table_regions):
        if region_idx in emitted:
            continue
        emit_table(region_idx)
    return chunks


def _slow_text_to_chunks(text: str, cfg: ModelConfig) -> List[ParseChunk]:
    """旧引擎文本输出（含 @@ 标签的段落）-> 统一 ParseChunk 列表。

    旧引擎每行形如 `正文@@page\\tx0\\tx1\\ttop\\tbottom##`（标签后置），
    拆成单行 ParseChunk，文本与标签分离，位置解析为 positions。
    """
    out = []
    for seg in (text or "").split("\n\n"):
        seg = seg.strip()
        if not seg:
            continue
        for line in seg.splitlines():
            line = line.strip()
            if not line:
                continue
            m = _PAGE_TAG_RE.search(line)
            if m:
                tag = m.group(0)
                body = (line[:m.start()] + line[m.end():]).strip()
            else:
                tag = ""
                body = line
            if not body and not tag:
                continue
            pos = parse_positions(tag) if tag else []
            page = pos[0]["pages"][0] - 1 if pos else 0
            ck = ParseChunk(text=tag if (body == "" and tag) else body,
                            tag=tag, page=page,
                            clean_text=body or strip_positions(tag))
            ck.positions = pos
            out.append(ck)
    if not out and text.strip():
        out.append(ParseChunk(text=text.strip(), clean_text=strip_positions(text),
                              page=0))
    return out


def _merge_by_position(text_chunks: List[ParseChunk],
                       slow_chunks: List[ParseChunk],
                       infos: List[PageInfo]) -> List[ParseChunk]:
    """按 (page,y) 合并 Fitz 文本块与慢路径块，恢复文档顺序。

    慢路径段落先拆分到行（tag 里带 y 信息太粗），因此这里给出一个稳定近似：
    慢路径块按 parse_positions 首页排序，插到对应页之前的 Fitz 块序列末尾。
    """
    text_chunks = list(text_chunks)
    slow_chunks = list(slow_chunks)
    if not slow_chunks:
        return text_chunks
    for ck in slow_chunks:
        ck.meta["engine"] = "slow_ocr"
    merged = []
    by_page_slow = {}
    for ck in slow_chunks:
        pg = ck.page
        by_page_slow.setdefault(pg, []).append(ck)
    cur = list(text_chunks)
    # 简单稳定排序：先按 page 再按 top（Fitz 块带精确 y，慢路径用其 page）
    def _key(ck):
        pos = ck.positions
        top = pos[0]["top"] if pos else 0.0
        return (ck.page, top, 0 if ck.meta.get("engine") != "slow_ocr" else 1)
    merged = cur + slow_chunks
    merged.sort(key=_key)
    return merged


def _render_page_image(page, zoomin: int = 3):
    """渲染 PDF 页面为 PIL Image（用于 PP-StructureV3 输入）。"""
    from PIL import Image
    import io
    # 渲染 DPI = 72 * zoomin（zoomin=3 -> 216 DPI）
    dpi = 72 * zoomin
    pix = page.get_pixmap(dpi=dpi)
    img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
    return img


def _ppstructure_extract_pages(path: str, page_nos: List[int],
                               cfg: ModelConfig, need_image: bool = False
                               ) -> Tuple[List[ParseChunk], List[dict]]:
    """用 PP-StructureV3 对指定页面执行 OCR + 版面分析。

    Returns:
        (chunks, media): 解析出的文本/表格块 和 图像/表格裁剪
    """
    from parser.ppstructure_engine import ppstructure_ocr_page
    import fitz

    all_chunks = []
    all_media = []
    doc = fitz.open(path)
    try:
        for pno in page_nos:
            if pno < 0 or pno >= doc.page_count:
                continue
            page = doc[pno]
            # 渲染页面图像
            page_img = _render_page_image(page, cfg.zoom_in)
            # PP-StructureV3 推理
            result = ppstructure_ocr_page(page_img, pno)
            # 转换为 ParseChunk
            for ck_dict in result.get("chunks", []):
                ck = ParseChunk(
                    text=ck_dict.get("text", ""),
                    tag=ck_dict.get("tag", ""),
                    kind=ck_dict.get("kind", "text"),
                    page=pno,
                    positions=ck_dict.get("positions", []),
                    clean_text=ck_dict.get("text", ""),
                    meta=ck_dict.get("meta", {}),
                )
                all_chunks.append(ck)
            # 收集图像（need_image）
            if need_image and result.get("tables"):
                for tbl in result["tables"]:
                    # 表格裁剪：从页面渲染整页图像（简化处理）
                    all_media.append({
                        "image": page_img,
                        "kind": "figure_or_table",
                        "positions": [{"pages": [pno + 1], "x0": 0, "x1": 612,
                                       "top": 0, "bottom": 792}],
                        "meta": {"html": tbl.get("html", ""), "engine": "ppstructure_v3"},
                    })
    finally:
        doc.close()
    return all_chunks, all_media


def _legacy_media_to_dicts(media_results) -> List[dict]:
    """旧引擎 need_image 产出的 ((img, meta), positions) / (img, meta) -> 统一 dict。"""
    out = []
    for item in media_results or []:
        try:
            if isinstance(item, tuple) and len(item) == 2 and isinstance(item[0], tuple):
                (img, meta), positions = item
            elif isinstance(item, tuple) and len(item) == 2:
                img, meta = item
                positions = None
            else:
                continue
            out.append({
                "image": img,
                "kind": "figure_or_table",
                "positions": positions,
                "meta": meta if isinstance(meta, (list, dict)) else [meta],
            })
        except Exception:
            continue
    return out

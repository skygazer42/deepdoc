# -*- coding: utf-8 -*-
"""
统一结果模型 + Fitz 预检 + 快速路径（升级计划 Step 2）

三层路由：
  1. 全部页面为「可编辑文本」且无表格线条区  -> Fitz 原生快速提取（约 0.01s/页）
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
import json
import logging
import os
import re
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# 表格线条绘制的阈值：页面存在这些数量的矢量线型图元则视为「有表格线的表格页」
_TABLE_LINE_COUNT = 8
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

    @staticmethod
    def from_env() -> "ModelConfig":
        def _int(name: str, default: int) -> int:
            return int(os.getenv(name, str(default)))

        depth = os.getenv("OCR_DEPTH", "full").strip().lower()
        if depth not in ("full", "fast", "skip"):
            depth = "full"
        return ModelConfig(
            min_text_chars_per_page=_int("MIN_TEXT_CHARS_PER_PAGE", 20),
            zoom_in=_int("ZOOM_IN", 3),
            max_ocr_pages_for_partial=_int("MAX_OCR_PAGES_PARTIAL", 4),
            max_mixed_ratio=float(os.getenv("MAX_MIXED_RATIO", "0.35")),
            ocr_depth=depth,
            max_chunks=_int("MAX_CHUNKS", 200),
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


def _page_has_table_lines(page) -> bool:
    """页面存在 >= _TABLE_LINE_COUNT 条矢量线形图元 -> 判定为有框线表格页。"""
    return len(page.get_drawings()) >= _TABLE_LINE_COUNT


def _route_from_infos(path: str, infos: List[PageInfo],
                      cfg: ModelConfig) -> str:
    """基于预检结果返回路由字符串。

    route:
      - fitz_fast   全文本、无表格线        -> Fitz 快速提取
      - hybrid      少量扫描/混合页         -> Fitz 文本页 + 局部慢路径
      - slow_full   扫描/复杂为主            -> 旧引擎全量（TSR）
    """
    scan_cnt = sum(1 for p in infos if p.page_kind in ("scan", "mixed"))
    total = max(len(infos), 1)
    scan_ratio = scan_cnt / total

    # 有框线表格页 -> 表格结构识别交给慢路径 TSR
    has_ruled_table = False
    if scan_cnt == 0:
        import fitz
        d = fitz.open(path)
        try:
            for pg in d:
                if _page_has_table_lines(pg):
                    has_ruled_table = True
                    break
        finally:
            d.close()

    if scan_cnt == 0 and not has_ruled_table:
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


def _reassemble_table_lines(page) -> List[Tuple[float, str, float, float, float, float]]:
    """对带框线表格页：从 span 级坐标重建行文本。

    返回 [(row_top, text, x0, x1, top, bottom), ...]（按视觉行 Y 排序，
    同 Y 内按 X 序拼接单元格，实现跨表格列的高质量读取）。
    """
    dd = page.get_text("dict")
    spans = []
    for blk in dd["blocks"]:
        if blk.get("type") != 0:
            continue
        for line in blk.get("lines", []):
            for sp in line.get("spans", []):
                t = (sp.get("text") or "").strip()
                if t:
                    spans.append((sp["bbox"], t))
    if not spans:
        return []
    # 行聚类：按 bbox[1] 聚类，行间距容差 = 中位行高的一半
    heights = [b[3] - b[1] for b, _ in spans]
    row_tol = (sorted(heights)[len(heights) // 2]) / 2 + 1
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

    for pg, info in zip(doc, infos):
        pno = pg.number
        if info.page_kind == "blank":
            continue
        if not info.has_text_layer:
            continue  # 扫描页由慢路径负责，这里跳过

        # 有框线表格页：走行重建
        if _page_has_table_lines(pg):
            table_pages += 1
            for row_y, text, x0, x1, top, bottom in _reassemble_table_lines(pg):
                ck = ParseChunk(
                    text=text,
                    tag=_line_tag(pno, x0, x1, top, bottom),
                    kind="table",
                    page=pno,
                    clean_text=text,
                )
                ck.meta["table"] = True
                chunks.append(ck)
                table_lines += 1
            if need_image:
                media.extend(_crop_page_media(pg, pno, cfg))
            continue

        # 常规文本页
        blocks = pg.get_text("blocks")
        for b in blocks:
            if b[6] != 0:
                continue
            chunks.extend(_block_to_chunks(pno, b, cfg))
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
               "table_pages": table_pages, "table_lines": table_lines},
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
        p.__images__(fnm, zoomin, self._page_from, self._page_to)
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
    """单页文本块提取（用于 hybrid 文本页）。"""
    chunks = []
    if _page_has_table_lines(page):
        for row_y, text, x0, x1, top, bottom in _reassemble_table_lines(page):
            ck = ParseChunk(text=text, tag=_line_tag(pno, x0, x1, top, bottom),
                            kind="table", page=pno, clean_text=text)
            ck.meta["table"] = True
            chunks.append(ck)
        return chunks
    for b in page.get_text("blocks"):
        if b[6] != 0:
            continue
        chunks.extend(_block_to_chunks(pno, b, cfg))
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
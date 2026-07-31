"""将不可搜索的扫描版 PDF 转为可搜索的双层 PDF（方案 B）核心逻辑。

原理：保留原 PDF 的所有对象（图片、元数据、压缩方式）不动，
只对没有文本层的页面追加 render_mode=3（不可见）的 OCR 文本层，
视觉上与原文逐像素一致，但文字可搜索、可复制。

本模块供 CLI（pdf_to_searchable.py）与 Web API（app.py）共用。
"""

import math
import multiprocessing as mp
import os
import shutil
import tempfile
import warnings
from concurrent.futures import ProcessPoolExecutor, wait
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import fitz

# Paddle 3.x 在部分 CPU 上 oneDNN(PIR) 推理有 bug，统一禁用
os.environ.setdefault("FLAGS_use_mkldnn", "0")
# PaddleX 默认在 CPU 上启用 MKLDNN，会导致上述 bug，一并关闭
os.environ.setdefault("PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT", "False")
# 跳过 PaddleX 每次启动的模型源连通性检查（模型已缓存时纯属浪费时间）
os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
# 压掉 paddle 底层 C++ 日志的 INFO 级噪音（"信息: ..."），保留 WARNING/ERROR
os.environ.setdefault("GLOG_minloglevel", "1")
# 压掉 "No ccache found" 等无害 UserWarning（纯推理场景用不到 JIT 编译）
warnings.filterwarnings("ignore", message="No ccache found")

# OCR 引擎按语言缓存单例：模型加载很慢，Web 场景必须全局复用
_ocr_cache: dict = {}
# 子进程进度队列（由 convert_parallel 的 initializer 注入，spawn 下不能经 submit 参数传递）
_PROGRESS_QUEUE = None


def _chunk_init(progress_queue) -> None:
    """子进程初始化：把进度队列写入模块全局（spawn 只能通过 initializer 继承 Queue）。"""
    global _PROGRESS_QUEUE
    _PROGRESS_QUEUE = progress_queue


def load_ocr(lang: str = "ch", batch_size: int = 16, cpu_threads: int = 0):
    """加载（并缓存）PaddleOCR 引擎，兼容 2.x / 3.x 初始化参数。

    使用 mobile 轻量模型：CPU 推理比 server 版快数倍。
    注意：PP-OCRv5 对中文(含中英混排)官方只映射 server 模型，
    无官方 v5 中文 mobile 识别模型，故识别模型用 PP-OCRv4_mobile_rec
    （官方中文 mobile，识别质量好、体积小），检测用 PP-OCRv5_mobile_det。

    batch_size：识别阶段批量大小，多行文本一次推理（默认 8）；
    cpu_threads：CPU 推理线程数，0 表示使用 PaddleX 默认（10）。
    """
    if lang not in _ocr_cache:
        from paddleocr import PaddleOCR

        try:
            # 3.x 参数
            kwargs = dict(
                lang=lang,
                text_detection_model_name="PP-OCRv5_mobile_det",
                text_recognition_model_name="PP-OCRv4_mobile_rec",
                text_recognition_batch_size=batch_size,
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
            )
            if cpu_threads > 0:
                kwargs["cpu_threads"] = cpu_threads
            _ocr_cache[lang] = PaddleOCR(**kwargs)
        except TypeError:
            # 2.x 参数
            _ocr_cache[lang] = PaddleOCR(lang=lang, use_angle_cls=False, show_log=False)
    return _ocr_cache[lang]


def ocr_page(ocr, pix) -> list:
    """对页面位图做 OCR，返回 [(poly 4角点 numpy, 文本), ...]（像素坐标）。"""
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if img.ndim == 3 and img.shape[2] >= 4:  # 去 alpha
        img = img[:, :, :3]
    elif img.ndim == 3 and img.shape[2] == 1:  # 灰度转 RGB
        img = np.repeat(img, 3, axis=2)

    words = []
    try:
        results = ocr.predict(img)  # 3.x
    except AttributeError:
        results = [ocr.ocr(img, cls=False)]  # 2.x

    for res in results:
        # 3.x: res 是 OCRResult 对象；2.x: res 是 [[box, (text, score)], ...]
        if hasattr(res, "json"):
            data = res.json
            # 3.x 结构: {"res": {"rec_texts": [...], "rec_polys": [...]}}
            if isinstance(data, dict) and isinstance(data.get("res"), dict):
                data = data["res"]
            texts = data.get("rec_texts", [])
            polys = data.get("rec_polys", [])
            for poly, text in zip(polys, texts):
                words.append((np.asarray(poly, dtype=float), text))
        elif isinstance(res, dict):
            texts = res.get("rec_texts", [])
            polys = res.get("rec_polys", [])
            for poly, text in zip(polys, texts):
                words.append((np.asarray(poly, dtype=float), text))
        elif res:
            for line in res:
                if not line:
                    continue
                box, item = line[0], line[1]
                words.append((np.asarray(box, dtype=float), item[0]))
    return words


# 字体字形偏移比例缓存：实测所得（与 font.ascender/descender 属性不一致，以 bbox 实测为准）
_font_metrics_cache: dict = {}


def _measure_font_metrics(fontname: str = "china-s"):
    """实测字体字形偏移比例，返回 (top_ratio, bottom_ratio)。

    top_ratio：基线到字形顶（视觉向上）占字号的比例；
    bottom_ratio：基线到字形底（视觉向下）占字号的比例。
    用与 add_text_layer 相同的 TextWriter 方式写入再读取，保证度量一致。
    """
    if fontname in _font_metrics_cache:
        return _font_metrics_cache[fontname]
    fs = 100.0
    point_y = 500.0
    font = fitz.Font(fontname)
    page = fitz.open().new_page(width=1000, height=1000)
    writer = fitz.TextWriter(page.rect)
    writer.append((100, point_y), "字", font=font, fontsize=fs)
    writer.write_text(page, render_mode=3)
    d = page.get_text("rawdict")
    bbox = d["blocks"][0]["lines"][0]["spans"][0]["bbox"]
    metrics = ((point_y - bbox[1]) / fs, (bbox[3] - point_y) / fs)
    _font_metrics_cache[fontname] = metrics
    return metrics


def add_text_layer(page, words, zoom: float) -> int:
    """把 OCR 词框写进页面不可见文本层，返回写入的文本行数。

    使用实测字体度量计算字号与基线，使搜索高亮框与 OCR 检测框完全贴合。
    用 TextWriter 写入（低层写入器，无 insert_text 的超宽截断问题）。
    """
    top, bottom = _measure_font_metrics("china-s")
    factor = top + bottom  # 字形总视觉高度 / 字号（china-s 实测 = 1.2）
    font = fitz.Font("china-s")
    writer = fitz.TextWriter(page.rect)
    count = 0
    for poly, text in words:
        text = (text or "").strip()
        if not text:
            continue
        xs = poly[:, 0] / zoom
        ys = poly[:, 1] / zoom
        # PyMuPDF 页面坐标与图像坐标一致（左上原点、y 向下），无需翻转
        rect = fitz.Rect(xs.min(), ys.min(), xs.max(), ys.max())
        if page.rotation != 0:
            rect = rect * page.derotation_matrix
        # 字形总高 = fontsize * factor = rect.height → fontsize
        fontsize = rect.height / factor
        # 基线 = rect.y0 + top*fontsize → 字形顶(baseline - top*fs) 恰好 = rect.y0
        baseline = rect.y0 + top * fontsize
        # 检测框含左 padding（实测约 4px @ 渲染分辨率），
        # 换算为固定 pt 内缩（随 dpi 自适应，不随框宽放大）
        start_x = rect.x0 + 4.0 / zoom
        writer.append((start_x, baseline), text, font=font, fontsize=fontsize)
        count += 1
    # render_mode=3: 不可见文本（可搜索、可复制，但不渲染），视觉零变化
    writer.write_text(page, render_mode=3)
    return count


def convert(
    src,
    dst,
    dpi: int = 150,
    lang: str = "ch",
    progress: Optional[Callable[[int, int, str], None]] = None,
) -> int:
    """转换整个 PDF，返回新增文本层的页数。

    progress(pno, total, msg) 可选回调，用于界面展示处理进度。
    """
    zoom = dpi / 72.0
    if progress:
        progress(0, 1, "正在加载 OCR 引擎（首次约 10-30 秒）...")
    ocr = load_ocr(lang)
    if progress:
        progress(0, 1, "OCR 引擎就绪，开始处理")

    doc = fitz.open(src)
    total = doc.page_count
    converted = 0
    try:
        for pno, page in enumerate(doc, start=1):
            if page.get_text("text").strip():
                if progress:
                    progress(pno, total, f"第 {pno}/{total} 页已有文本层，跳过")
                continue
            if progress:
                progress(pno - 0.5, total, f"正在识别第 {pno}/{total} 页...")
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
            words = ocr_page(ocr, pix)
            if not words:
                if progress:
                    progress(pno, total, f"第 {pno}/{total} 页未识别到文字")
                continue
            n = add_text_layer(page, words, zoom)
            if progress:
                progress(pno, total, f"第 {pno}/{total} 页完成（{n} 行文字）")
            converted += 1
        if progress:
            progress(total, total, "正在保存文件...")
        # TextWriter 嵌入的字体是完整字体（Droid Sans Fallback 约 3.5MB），
        # 子集化只保留用到的字形，可大幅减小文件体积
        doc.subset_fonts()
        doc.save(dst, garbage=3, deflate=False)  # 原对象保留，不重压缩
    finally:
        doc.close()
    return converted


def _convert_chunk_worker(args) -> int:
    """子进程 worker：复制指定页为子文档，独立转换后保存。

    必须在模块顶层（Windows spawn 需可 pickle）。
    进度经模块全局 _PROGRESS_QUEUE 上报 (全局页码, msg)，由主进程转发。
    """
    src_path, page_indices, dpi, lang, out_path = args
    chunk_start = page_indices[0]
    doc = fitz.open(src_path)
    sub = fitz.open()
    for i in page_indices:
        sub.insert_pdf(doc, from_page=i, to_page=i)
    doc.close()
    sub_path = out_path + ".sub.pdf"
    sub.save(sub_path)
    sub.close()

    def _cb(pno, total, msg):
        gpno = 0 if pno == 0 else chunk_start + pno
        _PROGRESS_QUEUE.put((gpno, msg))

    n = convert(sub_path, out_path, dpi=dpi, lang=lang, progress=_cb)
    return n


def convert_parallel(
    src,
    dst,
    dpi: int = 150,
    lang: str = "ch",
    workers: Optional[int] = None,
    progress: Optional[Callable[[int, int, str], None]] = None,
) -> int:
    """多进程页级并行转换：按页分片，各子进程独立 OCR，主进程合并。

    每个子进程加载独立 OCR 引擎（内存约 1GB/进程），本地多核场景
    接近线性加速；单页或 workers=1 时退化为串行 convert。
    """
    if workers is None:
        workers = min(4, max(1, (os.cpu_count() or 2) // 3))

    doc = fitz.open(src)
    total = doc.page_count
    doc.close()
    if total <= 1 or workers <= 1:
        return convert(src, dst, dpi=dpi, lang=lang, progress=progress)

    workers = min(workers, total)
    chunk_size = math.ceil(total / workers)
    chunks = [list(range(s, min(s + chunk_size, total))) for s in range(0, total, chunk_size)]

    workdir = Path(tempfile.mkdtemp(prefix="pdfocr_par_"))
    ctx = mp.get_context("spawn")
    progress_queue = ctx.Queue()
    try:
        with ProcessPoolExecutor(
            max_workers=len(chunks), mp_context=ctx,
            initializer=_chunk_init, initargs=(progress_queue,),
        ) as pool:
            futures = {
                pool.submit(
                    _convert_chunk_worker,
                    (str(src), idx, dpi, lang, str(workdir / f"chunk_{n}.pdf")),
                ): n
                for n, idx in enumerate(chunks)
            }
            remaining = set(futures)
            # 主进程统一维护进度：只统计"页完成"事件，页数变化才上报，
            # 保证 percent/页码/ETA 单调递增，不被乱序子进程事件覆盖或回退
            done_pages = set()
            last_n = -1
            while remaining:
                try:
                    while True:
                        gpno, msg = progress_queue.get(timeout=0.1)
                        # 页完成/跳过事件（整数页码）才计入完成页数
                        if gpno >= 1 and float(gpno).is_integer() and (
                            "完成" in msg or "跳过" in msg
                        ):
                            done_pages.add(gpno)
                except Exception:  # queue.Empty
                    pass
                n_done = len(done_pages)
                if progress and n_done != last_n:
                    last_n = n_done
                    progress(n_done, total, f"已完成 {n_done}/{total} 页")
                done, remaining = wait(remaining, timeout=0.1, return_when="FIRST_COMPLETED")
                for fut in done:
                    fut.result()  # 任一子进程失败则抛出中断

        merged = fitz.open()
        for n in range(len(chunks)):
            merged.insert_pdf(fitz.open(workdir / f"chunk_{n}.pdf"))
        merged.subset_fonts()
        merged.save(dst, garbage=3, deflate=False)
        merged.close()
        return total
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

#!/usr/bin/env python3
"""性能对比：串行 convert vs 并行 convert_parallel（需 __main__ 保护，Windows spawn）。

用法: uv run python _perf_test.py
"""
import os
import time

os.environ.setdefault("FLAGS_use_mkldnn", "0")
os.environ.setdefault("PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT", "False")

import numpy as np
import fitz

import converter


def verify(src, dst):
    doc = fitz.open(dst)
    ok_text = all(p.get_text().strip() for p in doc)
    # 逐页像素一致（与原 PDF 对比）
    src_doc = fitz.open(src)
    max_diff = 0
    for i in range(min(src_doc.page_count, doc.page_count)):
        a = src_doc[i].get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
        b = doc[i].get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
        na = np.frombuffer(a.samples, np.uint8).reshape(a.height, a.width, a.n)
        nb = np.frombuffer(b.samples, np.uint8).reshape(b.height, b.width, b.n)
        max_diff = max(max_diff, int(np.abs(na.astype(int) - nb.astype(int)).max()))
    src_doc.close()
    doc.close()
    return ok_text, max_diff


def main() -> int:
    src = "test_multi.pdf"

    t0 = time.time()
    converter.convert(src, "perf_serial.pdf", dpi=150, progress=lambda *a: None)
    t_serial = time.time() - t0
    ok_s, diff_s = verify(src, "perf_serial.pdf")
    print(f"串行 convert:        {t_serial:6.1f}s  可搜索={ok_s} 像素差={diff_s}")

    t0 = time.time()
    converter.convert_parallel(src, "perf_par.pdf", dpi=150, workers=4, progress=lambda *a: None)
    t_par = time.time() - t0
    ok_p, diff_p = verify(src, "perf_par.pdf")
    print(f"并行 convert_parallel:{t_par:6.1f}s  可搜索={ok_p} 像素差={diff_p}")
    print(f"提速比: {t_serial / t_par:.2f}x")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

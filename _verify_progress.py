#!/usr/bin/env python3
"""验证并行模式进度回传：进度事件应持续更新（含页级），结果正确。

用法: uv run python _verify_progress.py
"""
import os

os.environ.setdefault("FLAGS_use_mkldnn", "0")
os.environ.setdefault("PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT", "False")

import numpy as np
import fitz

import converter


def main() -> int:
    src = "test_multi.pdf"
    events = []

    def cb(pno, total, msg):
        events.append((pno, total, msg))

    converter.convert_parallel(src, "verify_par.pdf", dpi=150, workers=4, progress=cb)

    print(f"进度事件总数: {len(events)}")
    pnos = [e[0] for e in events]
    print(f"页码序列: {pnos}")
    print(f"消息序列:")
    for p, t, m in events:
        print(f"  pno={p}/{t}  {m}")

    # 正确性
    doc = fitz.open("verify_par.pdf")
    ok_text = all(p.get_text().strip() for p in doc)
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
    print(f"可搜索={ok_text} 像素差={max_diff}")
    # 断言：进度事件 > 页数（含引擎加载/页完成/保存），且像素一致
    assert len(events) >= 4, "进度事件过少，回传失败!"
    assert ok_text, "输出不可搜索!"
    assert max_diff == 0, "像素不一致!"
    print("验证通过 ✅ 进度回传正常且结果正确")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

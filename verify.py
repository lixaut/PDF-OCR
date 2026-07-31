#!/usr/bin/env python3
"""验证转换结果：可搜索性、逐像素一致、词框坐标正确。

用法: uv run python verify.py [原PDF] [转换后PDF]
"""
import sys
from pathlib import Path

import numpy as np
import fitz

SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("test_scan.pdf")
DST = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("out_searchable.pdf")


def main() -> int:
    ok = True
    src = fitz.open(SRC)
    dst = fitz.open(DST)

    # 1) 可搜索性：输出 PDF 能提取到文字
    texts = [p.get_text("text").strip() for p in dst]
    has_text = any(texts)
    print(f"[1] 可搜索性：输出 PDF 文本层 {'存在' if has_text else '缺失'}")
    if has_text:
        print(f"    提取示例: {texts[0][:80]}...")
    ok &= has_text

    # 2) 逐像素一致：原/新 PDF 逐页渲染对比
    print("[2] 逐像素一致：")
    for pno in range(min(src.page_count, dst.page_count)):
        p_src = src[pno].get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
        p_dst = dst[pno].get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
        a = np.frombuffer(p_src.samples, dtype=np.uint8).reshape(p_src.height, p_src.width, p_src.n)
        b = np.frombuffer(p_dst.samples, dtype=np.uint8).reshape(p_dst.height, p_dst.width, p_dst.n)
        if a.shape != b.shape:
            print(f"    第 {pno+1} 页: 尺寸不一致 {a.shape} vs {b.shape}")
            ok = False
            continue
        diff = int(np.abs(a.astype(int) - b.astype(int)).max())
        same = "完全一致" if diff == 0 else f"存在差异(max={diff})"
        print(f"    第 {pno+1} 页: {same}")
        ok &= diff == 0

    # 3) 词框坐标正确：search_for 应能定位到图片中的文字
    print("[3] 词框坐标：")
    if has_text:
        for pno, page in enumerate(dst):
            for probe in ["第一行", "测试文字", "北京", "发票"]:
                rects = page.search_for(probe)
                if rects:
                    r = rects[0]
                    print(f"    第 {pno+1} 页 搜索 '{probe}' -> 定位 ({r.x0:.0f},{r.y0:.0f})-({r.x1:.0f},{r.y1:.0f}) 命中")
                    break
            else:
                print(f"    第 {pno+1} 页 未命中任何探测词")
                ok = False

    src.close()
    dst.close()
    print("验证结果:", "通过 ✅" if ok else "存在失败项 ❌")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

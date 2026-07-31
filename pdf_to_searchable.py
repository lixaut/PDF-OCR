#!/usr/bin/env python3
"""命令行入口：将不可搜索的扫描版 PDF 转为可搜索的双层 PDF。

核心逻辑见 converter.py。

用法：
    uv run python pdf_to_searchable.py 输入.pdf 输出.pdf [--dpi 200] [--lang ch]
"""

import argparse
import sys
from pathlib import Path

import converter


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", type=Path, help="输入 PDF（不可搜索）")
    ap.add_argument("output", type=Path, help="输出 PDF（可搜索）")
    ap.add_argument("--dpi", type=int, default=200, help="OCR 渲染分辨率，默认 200")
    ap.add_argument("--lang", default="ch", help="OCR 语言，默认 ch")
    args = ap.parse_args()

    if not args.input.exists():
        print(f"错误：输入文件不存在 {args.input}", file=sys.stderr)
        return 1

    n = converter.convert(
        args.input,
        args.output,
        dpi=args.dpi,
        lang=args.lang,
        progress=lambda pno, total, msg: print(f"[{pno}/{total}] {msg}"),
    )
    print(f"完成：{n} 页已追加不可见文本层 -> {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

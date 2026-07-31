#!/usr/bin/env python3
"""生成测试用"扫描型"PDF：纯图片、无文本层，用于验证转换脚本。

用法: uv run python make_test_scan.py [输出路径]
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_PATH = "C:/Windows/Fonts/simhei.ttf"
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("test_scan.pdf")

LINES = [
    "这是第一行测试文字，用于验证中文 OCR。",
    "Second line: English OCR test 2024.",
    "发票号码：No. 1234567890",
    "地址：北京市朝阳区建国路 88 号",
    "The quick brown fox jumps over the lazy dog.",
    "混合内容 Mixed content 混合内容",
    "日期：2024-01-15  金额：¥12,345.67",
]


def main() -> int:
    width, height = 1240, 1754  # A4 @150dpi
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(FONT_PATH, 42)

    y = 120
    for line in LINES:
        draw.text((100, y), line, fill="black", font=font)
        y += 140
    draw.rectangle((100, y + 20, 1140, y + 70), outline="black", width=3)  # 装饰框，模拟表格线

    img.save(OUT, "PDF", resolution=150.0)
    print(f"已生成测试扫描 PDF: {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

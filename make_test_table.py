#!/usr/bin/env python3
"""生成带表格装饰的测试 PDF：行首有表格线/空单元格，模拟用户场景。

每行文字左侧画一个表格边框（竖线 + 小单元格），
检测模型可能把表格线也算进文本行检测框。
用法: uv run python make_test_table.py [输出路径]
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_PATH = "C:/Windows/Fonts/simhei.ttf"
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("test_table.pdf")

ROWS = [
    "序号：001  品名：A4 打印纸",
    "002  规格：70g 白色",
    "003  数量：50 包",
    "004  单价：¥25.00",
    "005  金额：¥1,250.00",
]


def main() -> int:
    width, height = 1240, 800  # A4 宽 @150dpi
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(FONT_PATH, 42)

    # 表格结构：左侧空列（宽 200px）+ 右侧文字列（宽 700px）
    col_x = 200
    col_w = 700
    top = 120
    row_h = 100

    for i in range(len(ROWS) + 1):
        y = top + i * row_h
        draw.line([(0, y), (width, y)], fill="black", width=3)  # 横线
    for i, (x0, x1) in enumerate([(0, col_x), (col_x, col_x + col_w), (col_x + col_w, width)]):
        if i == 0:
            continue  # 左列只画右边线
        draw.line([(x0, top), (x0, top + len(ROWS) * row_h)], fill="black", width=3)  # 竖线

    for i, text in enumerate(ROWS):
        y = top + i * row_h + 25
        draw.text((col_x + 30, y), text, fill="black", font=font)

    img.save(OUT, "PDF", resolution=150.0)
    print(f"已生成带表格测试 PDF: {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

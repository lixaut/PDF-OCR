# PDF 可搜索化转换服务 —— 容器化部署
FROM python:3.12-slim

# PaddleOCR(paddlepaddle/open-cv) 运行所需的系统库
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 libgl1 libglib2.0-0 fontconfig \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先安装依赖（利用 Docker 层缓存，改动代码时无需重装）
RUN pip install --no-cache-dir \
    paddlepaddle paddleocr pymupdf numpy \
    fastapi "uvicorn[standard]" python-multipart

# 复制项目代码
COPY app.py converter.py pdf_to_searchable.py ./
COPY static ./static

# 构建时预下载 OCR 模型（ch/en），运行时无需外网
RUN python -c "import converter; converter.load_ocr('ch'); converter.load_ocr('en')"

EXPOSE 8000

# 单 worker：OCR 引擎在进程内缓存，多 worker 会重复加载模型
CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]

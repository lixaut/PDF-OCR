# pdf-ocr

将**不可搜索**的扫描版 PDF 转换为**可搜索**的双层 PDF：视觉上与原文逐像素一致（原图、元数据、压缩方式全部保留），只为没有文本层的页面追加一层不可见的 OCR 文本，文字可以搜索、复制、高亮。

提供两种使用方式：

- **网页版**：FastAPI 服务 + 浏览器上传界面，上传 → 实时进度 → 自动下载
- **命令行**：单文件批量转换

## 原理

PDF 页面的内容分两类：

- **文本对象**：真实存储字符+编码，可搜索可复制（Word/浏览器打印导出的 PDF）；
- **图像对象**：整页是一张位图（扫描件、传真、拍照），没有任何文字信息，无法搜索。

本工具对后者做处理：PaddleOCR 识别页面图像 → 把识别出的文字按原始坐标写回页面，使用 PDF 规范中的 `render_mode=3`（不可见文本）——文字存在于内容流中、可被搜索/提取，但不会被渲染，因此视觉零变化。已有文本层的页面自动跳过（不重复 OCR）。

## 功能特性

- **双层 PDF 输出**：原图、元数据、压缩方式原样保留，视觉逐像素一致；
- **高精度位置校准**：基于字体度量（实测 ascender/descender）计算字号与基线，并内缩检测框左 padding，搜索高亮框与 OCR 检测框贴合（偏差 <1.5pt）；
- **多进程页级并行**：按页分片、多进程独立 OCR 后合并，本地多核接近线性加速（单页自动退化为串行）；
- **实时进度与 ETA**：后端按页上报进度，EMA 平滑估算剩余时间，前端双进度条（上传/处理）+ 分钟级剩余时间；
- **字体子集化**：只嵌入用到的字形，输出体积小（如 3.5MB → 约 500KB）；
- **长文本不截断**：用 TextWriter 写入，规避 insert_text 超宽静默截断问题。

## 项目结构

```
pdf-ocr/
├── app.py                 # FastAPI Web 服务（异步任务：创建/查询进度/下载 + 静态页面）
├── converter.py           # 核心转换逻辑（OCR 引擎、坐标校准、串行/并行转换）
├── pdf_to_searchable.py   # 命令行入口
├── static/index.html      # 网页界面（拖拽上传、双进度条、ETA、自动下载）
├── make_test_scan.py      # 生成测试扫描 PDF
├── make_test_table.py     # 生成带表格装饰的测试 PDF（验证行首偏移）
├── verify.py              # 转换结果验证脚本（可搜索/像素一致/词框贴合）
├── _perf_test.py          # 串行 vs 并行性能对比脚本
├── _verify_progress.py    # 并行进度回传验证脚本
├── pyproject.toml         # uv 项目配置与依赖声明
├── Dockerfile             # 容器化部署
└── .venv/                 # uv 管理的项目虚拟环境（自动生成）
```

## 环境要求

- Windows / macOS / Linux（本项目在 Windows 上开发验证）
- [uv](https://docs.astral.sh/uv/)（Python 包与项目环境管理器，项目级 Python 环境，不依赖系统 Python）

## 快速开始（本地）

```bash
# 1. 初始化项目环境（自动创建 .venv 并安装依赖，含托管 Python 3.12）
uv sync

# 2a. 网页版：启动服务后浏览器访问 http://127.0.0.1:8000
uv run uvicorn app:app --host 127.0.0.1 --port 8000

# 2b. 命令行：将不可搜索的 PDF 变为可搜索
uv run python pdf_to_searchable.py 输入.pdf 输出.pdf [--dpi 150] [--lang ch]
```

首次运行 PaddleOCR 会自动下载识别模型（约几十 MB，缓存于 `~/.paddlex`）。

## 接口说明（异步任务架构）

| 接口 | 方法 | 说明 |
|---|---|---|
| `/` | GET | 前端上传页面 |
| `/api/convert` | POST | multipart 上传：`file`(必填)、`dpi`(默认150)、`lang`(默认ch)；返回 `{task_id}` |
| `/api/tasks/{task_id}` | GET | 查询进度：`{status, percent, eta_seconds, message}`（status: queued/processing/done/error） |
| `/api/tasks/{task_id}/download` | GET | 下载转换结果（仅 done 后可用，下载完自动清理） |
| `/api/health` | GET | 健康检查 |

示例：

```bash
# 1) 上传创建任务
TASK_ID=$(curl -s -X POST http://127.0.0.1:8000/api/convert \
  -F "file=@扫描件.pdf" -F "dpi=150" -F "lang=ch" \
  | python -c "import sys,json; print(json.load(sys.stdin)['task_id'])")

# 2) 轮询进度（percent/eta_seconds 实时更新）
curl -s http://127.0.0.1:8000/api/tasks/$TASK_ID

# 3) 完成后下载
curl -s -o 可搜索.pdf http://127.0.0.1:8000/api/tasks/$TASK_ID/download
```

## 部署（Docker）

```bash
# 构建镜像（构建时会预下载 OCR 模型，运行时无需外网）
docker build -t pdf-ocr .

# 运行
docker run -d --name pdf-ocr -p 8000:8000 pdf-ocr

# 浏览器访问 http://服务器IP:8000 即可上传使用
```

说明：

- 镜像内固定单 worker 运行——OCR 引擎在进程内缓存，多 worker 会重复加载模型、浪费内存；
- 若需 HTTPS、反向代理，建议在前面挂 Nginx/Caddy，转发到容器 8000 端口；
- 上传大小限制取决于代理/网关配置（默认无限制）。

## 验证

```bash
# 生成测试扫描 PDF（纯图片、无文本层）
uv run python make_test_scan.py

# 命令行转换
uv run python pdf_to_searchable.py test_scan.pdf out_searchable.pdf

# 验证三项指标：文本可搜索 / 逐像素一致 / 词框定位正确
uv run python verify.py test_scan.pdf out_searchable.pdf

# 性能对比：串行 vs 多进程并行（输出提速比）
uv run python _perf_test.py

# 并行进度回传验证（页码单调、结果正确）
uv run python _verify_progress.py
```

预期输出：

```
[1] 可搜索性：输出 PDF 文本层 存在
[2] 逐像素一致：
    第 1 页: 完全一致
[3] 词框坐标：
    第 1 页 搜索 '第一行' -> 定位 (...) 命中
验证结果: 通过 ✅
```

## 性能说明

- **模型**：检测 `PP-OCRv5_mobile_det` + 识别 `PP-OCRv4_mobile_rec`（官方中文 mobile），CPU 推理比 server 版快数倍；
- **多进程并行**：`convert_parallel` 按页分片（默认 4 进程），多页文档实测约 1.8 倍加速，页数越多越接近进程数；
- **批量识别**：`batch_size=16`，多行文本一次推理；
- **dpi 自适应**：默认 150（快速），清晰扫描件足够；小字/低清文档可调 200/300 提升精度；
- 服务启动时预热 OCR 引擎（`lifespan`），首个请求不等待模型加载。

## 注意事项

- 识别质量取决于 PaddleOCR，模糊/低对比度扫描件可调高 `dpi`（如 300）提升准确率；
- 处理的是"整页无文本"的扫描件；页面内混排的少量矢量文字（如页码）会被跳过页判定而整体不处理；
- 输出文件会略大于原文件（新增字体子集与文本内容流）；
- 多进程并行每个子进程加载独立 OCR 引擎（约 1GB 内存/进程），默认 4 进程约 4GB；可在 `app.py` 传 `workers` 参数调整；
- Paddle 3.x 在部分 CPU 上 oneDNN(PIR) 推理有 bug，脚本已在导入时自动禁用 MKLDNN。

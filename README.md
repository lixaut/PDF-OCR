# pdf-ocr

将**不可搜索**的扫描版 PDF 转换为**可搜索**的双层 PDF：视觉上与原文逐像素一致（原图、元数据、压缩方式全部保留），只是为没有文本层的页面追加一层不可见的 OCR 文本，文字可以搜索、复制、高亮。

提供两种使用方式：

- **网页版**：FastAPI 服务 + 浏览器上传界面（部署后可在网页直接使用）
- **命令行**：单文件批量转换

## 原理

PDF 页面的内容分两类：

- **文本对象**：真实存储字符+编码，可搜索可复制（Word/浏览器打印导出的 PDF）；
- **图像对象**：整页是一张位图（扫描件、传真、拍照），没有任何文字信息，无法搜索。

本工具对后者做处理：用 PaddleOCR 识别页面图像 → 把识别出的文字按原始坐标写回页面，使用 PDF 规范中的 `render_mode=3`（不可见文本）——文字存在于内容流中、可被搜索/提取，但不会被渲染，因此视觉零变化。已有文本层的页面会自动跳过（不重复 OCR）。

## 项目结构

```
pdf-ocr/
├── app.py                 # FastAPI Web 服务（上传/转换/下载接口 + 静态页面）
├── converter.py           # 核心转换逻辑（CLI 与 API 共用）
├── pdf_to_searchable.py   # 命令行入口
├── static/index.html      # 网页界面（拖拽上传、进度、自动下载）
├── make_test_scan.py      # 生成测试扫描 PDF
├── verify.py              # 转换结果验证脚本
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
uv run python pdf_to_searchable.py 输入.pdf 输出.pdf [--dpi 300] [--lang ch]
```

首次运行 PaddleOCR 会自动下载识别模型（约几十 MB，缓存于 `~/.paddlex`）。

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
# 生成一张测试扫描 PDF（纯图片、无文本层）
uv run python make_test_scan.py

# 命令行转换
uv run python pdf_to_searchable.py test_scan.pdf out_searchable.pdf

# 验证三项指标：文本可搜索 / 逐像素一致 / 词框定位正确
uv run python verify.py test_scan.pdf out_searchable.pdf
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

## 接口说明

| 接口 | 方法 | 说明 |
|---|---|---|
| `/` | GET | 前端上传页面 |
| `/api/convert` | POST | multipart 上传：`file`(必填)、`dpi`(默认200)、`lang`(默认ch)；成功返回转换后的 PDF 文件流 |
| `/api/health` | GET | 健康检查 |

示例：

```bash
curl -X POST http://127.0.0.1:8000/api/convert \
  -F "file=@扫描件.pdf" -F "dpi=300" -F "lang=ch" \
  -o 可搜索.pdf
```

## 注意事项

- 识别质量取决于 PaddleOCR，模糊/低对比度扫描件可调高 `--dpi`（如 300）提升准确率；
- 处理的是"整页无文本"的扫描件；页面内混排的少量矢量文字（如页码）会被跳过页判定而整体不处理；
- 输出文件会略大于原文件（新增字体子集与文本内容流）；
- Paddle 3.x 在部分 CPU 上 oneDNN(PIR) 推理有 bug，脚本已在导入时自动禁用 MKLDNN。

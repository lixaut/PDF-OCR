#!/usr/bin/env python3
"""PDF 可搜索化 Web 服务（FastAPI）。

能力：
- GET  /                              前端页面（static/index.html）
- POST /api/convert                   上传 PDF → 创建异步任务，立即返回 task_id
- GET  /api/tasks/{task_id}           查询任务状态与进度（percent/message）
- GET  /api/tasks/{task_id}/download  下载转换结果（仅 status=done 时可用）

说明：转换在后台线程执行，OCR 引擎为进程内单例（非线程安全），
因此同一时刻只处理一个任务，其余排队——内部工具场景足够。

启动（开发）:
    uv run uvicorn app:app --host 0.0.0.0 --port 8000
"""

import shutil
import tempfile
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import converter
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """应用启动时预热 OCR 引擎，避免首个请求等待模型加载。"""
    print("正在预热 OCR 引擎（加载模型）...")
    converter.load_ocr("ch")
    print("OCR 引擎预热完成")
    yield


app = FastAPI(title="PDF 可搜索化转换服务", version="2.0.0", lifespan=lifespan)

# 允许跨域：前端与 API 可分开部署（同源部署时无影响）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 内存任务表：task_id -> 任务状态（单 worker 部署，进程内有效）
TASKS: dict = {}
# 转换锁：OCR 引擎为进程内单例，同一时刻只处理一个任务，其余排队
_TASK_LOCK = threading.Lock()
# 已完成/失败任务的最长保留时间（秒），超时懒清理
_TASK_TTL = 3600


def _validate_pdf(data: bytes) -> None:
    """粗略校验文件是 PDF（魔数 %PDF 或 fitz 能打开）。"""
    if data[:4] == b"%PDF":
        return
    try:
        import fitz

        fitz.open(stream=data, filetype="pdf").close()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="不是有效的 PDF 文件") from exc


def _sweep_expired() -> None:
    """懒清理：删除创建超过 TTL 的任务及其临时目录。"""
    now = time.time()
    for tid, task in list(TASKS.items()):
        if now - task["created_at"] > _TASK_TTL:
            TASKS.pop(tid, None)
            shutil.rmtree(task["workdir"], ignore_errors=True)


def _run_task(task_id: str, src: Path, dst: Path, dpi: int, lang: str) -> None:
    """后台线程：执行转换并更新任务状态。"""
    task = TASKS[task_id]
    t_start = time.time()
    task["started_at"] = t_start
    # 引擎就绪前（加载期）不计算 ETA：_last_page_ts 在"引擎就绪"回调时初始化

    def on_progress(pno, total, msg):
        # pno 可能为中间值（如 pno-0.5），按比例计算百分比
        task["percent"] = int(pno / total * 100)
        task["message"] = msg
        now = time.time()
        # 引擎就绪：以此为第一页计时的起点，引擎加载耗时不计入页耗时
        if pno == 0 and "就绪" in msg:
            task["_last_page_ts"] = now
            return
        # 跳过页/保存阶段不参与页耗时统计
        if "跳过" in msg or "保存" in msg:
            return
        # 整页完成（OCR 页）：用上一页到本页的实际耗时做 EMA 平滑
        if pno >= 1 and float(pno).is_integer():
            prev = task.get("_last_page_ts", t_start)
            page_time = max(0.001, now - prev)
            task["_last_page_ts"] = now
            old = task.get("_ema_page")
            task["_ema_page"] = page_time if old is None else 0.7 * page_time + 0.3 * old
            task["eta_seconds"] = max(0, int(task["_ema_page"] * (total - pno)))
        # 中间态（正在识别某页）：用 EMA 估算剩余
        elif task.get("_ema_page") is not None:
            task["eta_seconds"] = max(0, int(task["_ema_page"] * (total - pno)))

    task["status"] = "processing"
    task["percent"] = 0
    task["eta_seconds"] = None
    task["message"] = "准备 OCR 引擎..."
    try:
        with _TASK_LOCK:
            # 多进程页级并行（本地多核）；单页自动退化为串行
            n = converter.convert_parallel(src, dst, dpi=dpi, lang=lang, progress=on_progress)
        if n == 0:
            task["status"] = "error"
            task["message"] = "没有需要处理的页面（已全部可搜索或未识别到文字）"
            return
        task["status"] = "done"
        task["percent"] = 100
        task["eta_seconds"] = 0
        task["total_seconds"] = int(time.time() - task["started_at"])
        task["message"] = f"转换完成，共处理 {n} 页"
    except Exception as exc:  # noqa: BLE001
        task["status"] = "error"
        task["message"] = f"转换失败：{exc}"
        task["error"] = str(exc)


@app.post("/api/convert")
def create_convert_task(
    file: UploadFile = File(...),
    dpi: int = Form(200),
    lang: str = Form("ch"),
) -> JSONResponse:
    """接收 PDF，创建异步转换任务，立即返回 task_id。"""
    filename = file.filename or "input.pdf"
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="仅支持 .pdf 文件")

    data = file.file.read()
    _validate_pdf(data)

    _sweep_expired()
    task_id = uuid.uuid4().hex
    workdir = Path(tempfile.mkdtemp(prefix="pdfocr_"))
    src = workdir / "input.pdf"
    dst = workdir / "searchable.pdf"
    src.write_bytes(data)

    TASKS[task_id] = {
        "status": "queued",
        "percent": 0,
        "message": "等待处理...",
        "workdir": workdir,
        "dst": dst,
        "out_name": f"searchable_{Path(filename).stem}.pdf",
        "created_at": time.time(),
    }
    threading.Thread(
        target=_run_task,
        args=(task_id, src, dst, dpi, lang),
        daemon=True,
    ).start()
    return JSONResponse({"task_id": task_id, "status": "queued"})


@app.get("/api/tasks/{task_id}")
def get_task_status(task_id: str) -> JSONResponse:
    """查询任务进度（供前端轮询）。"""
    task = TASKS.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在（可能已过期或服务重启）")
    # 实时耗时：处理中按 started_at 实时计算，完成后取 total_seconds
    elapsed = task.get("total_seconds")
    if task["status"] in ("processing", "queued") and task.get("started_at"):
        elapsed = int(time.time() - task["started_at"])
    return JSONResponse(
        {
            "status": task["status"],
            "percent": task["percent"],
            "eta_seconds": task.get("eta_seconds"),
            "elapsed_seconds": elapsed,
            "message": task["message"],
        }
    )


@app.get("/api/tasks/{task_id}/download")
def download_task(task_id: str) -> FileResponse:
    """下载转换结果；下载完成后清理任务与临时目录。"""
    task = TASKS.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在（可能已过期或服务重启）")
    if task["status"] != "done":
        raise HTTPException(status_code=409, detail="任务尚未完成")

    def _cleanup() -> None:
        TASKS.pop(task_id, None)
        shutil.rmtree(task["workdir"], ignore_errors=True)

    return FileResponse(
        task["dst"],
        media_type="application/pdf",
        filename=task["out_name"],
        background=BackgroundTask(_cleanup),
    )


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    """返回 204，避免浏览器请求图标产生 404 日志噪音。"""
    return Response(status_code=204)


@app.get("/.well-known/appspecific/com.chrome.devtools.json", include_in_schema=False)
def chrome_devtools_probe() -> Response:
    """Chrome DevTools 探测路径，返回 204 消除 404 日志噪音。"""
    return Response(status_code=204)


@app.get("/api/health")
def health() -> JSONResponse:
    return JSONResponse({"status": "ok"})


# 前端静态页面（注册在 API 路由之后，避免抢占 /api 前缀）
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

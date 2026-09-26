# -*- coding: utf-8 -*-
"""量化交易平台 P0 一期 — FastAPI 应用入口。

启动：uvicorn app.main:app --host 127.0.0.1 --port 8000
"""
import logging
import logging.handlers
import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .api import cron, datasets, strategies, tasks
from .config import Config, load_config
from .database import init_db
from .services import scanner
from .services.task_service import init_task_service

# ---------- 日志（UTF-8，避免 Windows 乱码） ----------
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stderr)],
)

cfg: Config = load_config()

app = FastAPI(title="量化交易平台", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=cfg.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(datasets.router)
app.include_router(strategies.router)
app.include_router(tasks.router)
app.include_router(cron.router)


@app.on_event("startup")
def on_startup():
    # 建表 + 初始化目录
    init_db()
    cfg.logs_dir.mkdir(parents=True, exist_ok=True)
    # 启动任务执行线程
    service = init_task_service(cfg)
    service.start()
    # 启动时用独立会话扫描登记脚本
    from .database import SessionLocal
    with SessionLocal() as db:
        scanner.scan(cfg, db)


@app.on_event("shutdown")
def on_shutdown():
    from .services.task_service import get_service
    get_service().stop()


@app.get("/api/health")
def health():
    return {"status": "ok", "app": "量化交易平台", "version": "0.1.0"}


# 托管前端静态资源（web/）
web_dir = cfg.ROOT / "web"
if web_dir.is_dir():
    app.mount("/", StaticFiles(directory=str(web_dir), html=True), name="web")
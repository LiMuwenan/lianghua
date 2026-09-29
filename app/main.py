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

from .api import cron, datasets, strategies, tasks, stocks
from .config import Config, load_config
from .database import init_db
from .datasources.baostock import BaostockDataSource
from .services import dataset_scan, ingest, scanner
from .services.scheduler import get_scheduler
from .services.task_service import init_task_service, get_service

# ---------- 日志（UTF-8，避免 Windows 乱码） ----------
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stderr)],
)

cfg: Config = load_config()

# 数据源单例：baostock（主数据）。连接延迟到 startup，避免导入即触发网络。
_ds = BaostockDataSource()


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
app.include_router(stocks.router)


def _cron_trigger(ct) -> None:
    """cron 触发回调：复用任务执行路径，落到 TaskService。"""
    from .database import SessionLocal
    from .models import Dataset, Strategy
    db = SessionLocal()
    try:
        svc = get_service()
        if ct.kind == "ingest":
            ds = db.query(Dataset).get(ct.ref_id)
            if ds is not None:
                svc.enqueue_ingest(db, ds.name, ds.id, params=ct.params or {})
        else:
            strategy = db.query(Strategy).get(ct.ref_id)
            if strategy is not None:
                svc.enqueue(db, strategy, params=ct.params or {})
    finally:
        db.close()


@app.on_event("startup")
def on_startup():
    # 建表 + 初始化目录
    init_db()
    cfg.logs_dir.mkdir(parents=True, exist_ok=True)
    # 连接数据源并把真实实例绑定为摄取钩子
    _ds.connect()
    app.state.ds = _ds                       # 供个股初始化等服务使用
    ingest.bind_source(_ds)
    # 启动任务执行线程
    service = init_task_service(cfg)
    service.start()
    # 启动时用独立会话扫描登记脚本
    from .database import SessionLocal
    with SessionLocal() as db:
        scanner.scan(cfg, db)
        dataset_scan.scan_all(cfg, db)
    # 定时调度：绑定触发回调 + 从库恢复已启用任务
    sched = get_scheduler()
    sched.bind(_cron_trigger)
    with SessionLocal() as db:
        sched.restore(db)
    sched.start()


@app.on_event("shutdown")
def on_shutdown():
    from .services.task_service import get_service
    get_service().stop()
    get_scheduler().shutdown()
    _ds.disconnect()


@app.get("/api/health")
def health():
    return {"status": "ok", "app": "量化交易平台", "version": "0.1.0"}


# 托管前端静态资源：优先 web/dist（Vue3 构建产物，P1），缺省回退 web/（原生 JS 开发期）
web_dir = cfg.ROOT / "web"
dist_dir = cfg.ROOT / "web" / "dist"
static_dir = dist_dir if dist_dir.is_dir() else (web_dir if web_dir.is_dir() else None)
if static_dir is not None:
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="web")
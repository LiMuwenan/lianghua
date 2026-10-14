# -*- coding: utf-8 -*-
"""报告 API：立即生成并发送 / 预览 HTML（不发信）/ 最近一次记录。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..config import load_config
from ..database import get_db
from ..models import TaskRun
from ..services import report_service
from ..services.task_service import get_service

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("/daily/preview")
def preview(db: Session = Depends(get_db)):
    """渲染当日汇总 HTML（不发送、不落库；不含指数补抓等写操作）。"""
    cfg = load_config()
    trade_date = report_service.resolve_trade_date()
    if not trade_date:
        raise HTTPException(409, "无法确定最近交易日（数据源未就绪）")
    gen = report_service.generate(cfg, db, trade_date)
    return {"trade_date": trade_date, "html": gen["html"]}


@router.post("/daily/run")
def run(db: Session = Depends(get_db)):
    """立即生成并发送当日汇总（入队，走任务日志/状态）。"""
    svc = get_service()
    task = svc.enqueue_report(db)
    return {"task_id": task.id, "kind": task.kind}


@router.get("/daily/latest")
def latest(db: Session = Depends(get_db)):
    """最近一次 report 任务的摘要（含归档 HTML 路径与状态）。"""
    row = (db.query(TaskRun).filter(TaskRun.kind == "report")
           .order_by(TaskRun.id.desc()).first())
    if row is None:
        return {"task_id": None, "status": "", "result_summary": {}, "output_files": []}
    return {"task_id": row.id, "status": row.status,
            "result_summary": row.result_summary or {},
            "output_files": row.output_files or []}

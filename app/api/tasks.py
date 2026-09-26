# -*- coding: utf-8 -*-
"""任务 API：列表、详情（含日志）。"""
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..config import load_config
from ..database import get_db
from ..models import TaskRun
from ..schemas import TaskOut
from ..services.task_service import get_service

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router.get("", response_model=list[TaskOut])
def list_tasks(limit: int = 100, db: Session = Depends(get_db)):
    """任务/运行记录列表，按创建时间倒序。"""
    rows = (
        db.query(TaskRun).order_by(TaskRun.id.desc()).limit(min(limit, 500)).all()
    )
    return rows


@router.get("/running")
def running_task():
    """当前正在运行的任务 id（供前端提示）。"""
    svc = get_service()
    return {"task_id": svc.running_task_id}


@router.post("/{tid}/terminate")
def terminate_task(tid: int, svc = Depends(get_service)):
    """请求终止运行中的任务（挂起为 aborted/子进程 kill）。"""
    ok = svc.terminate(tid)
    if not ok:
        raise HTTPException(409, "任务不在运行中，无法终止")
    return {"ok": True, "task_id": tid}


@router.get("/{tid}", response_model=TaskOut)
def get_task(tid: int, db: Session = Depends(get_db)):
    task = db.query(TaskRun).get(tid)
    if task is None:
        raise HTTPException(404, "任务不存在")
    return task


@router.get("/{tid}/log")
def get_task_log(tid: int, db: Session = Depends(get_db)):
    """读取任务日志文件。前端据此实时刷新进度。"""
    task = db.query(TaskRun).get(tid)
    if task is None:
        raise HTTPException(404, "任务不存在")
    if not task.log_path:
        return {"content": "", "status": task.status}

    cfg = load_config()
    # 兼容相对/绝对路径
    p = Path(task.log_path)
    if not p.is_absolute():
        p = cfg.ROOT / p
    if not p.is_file():
        return {"content": "(日志文件尚未生成)", "status": task.status}

    # 只返回末尾 600KB，避免超大文件
    data = p.read_text(encoding="utf-8", errors="replace")
    if len(data) > 600_000:
        data = "(日志过长，仅显示末尾)\n\n" + data[-600_000:]
    return {"content": data, "status": task.status}


@router.get("/running")
def running_task():
    """当前正在运行的任务 id（供前端提示）。"""
    svc = get_service()
    return {"task_id": svc.running_task_id}
# -*- coding: utf-8 -*-
"""策略/脚本 API：列表、触发运行、重新扫描登记。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..config import load_config
from ..database import get_db
from ..models import Strategy
from ..schemas import RunRequest, StrategyOut, TaskOut
from ..services import scanner
from ..services.task_service import get_service

router = APIRouter(prefix="/api/strategies", tags=["strategies"])


@router.get("", response_model=list[StrategyOut])
def list_strategies(db: Session = Depends(get_db)):
    """策略/脚本列表（含数据脚本），按 kind 排序。"""
    rows = db.query(Strategy).order_by(Strategy.kind, Strategy.id).all()
    return rows


@router.post("/scan")
def rescan(db: Session = Depends(get_db)):
    """重新扫描 manifest 目录并登记。"""
    cfg = load_config()
    stats = scanner.scan(cfg, db)
    return {"message": "扫描完成", **stats}


@router.post("/{sid}/run", response_model=TaskOut)
def run_strategy(sid: int, req: RunRequest, db: Session = Depends(get_db)):
    """触发脚本/策略运行，可携带参数覆盖。"""
    strategy = db.query(Strategy).get(sid)
    if strategy is None:
        raise HTTPException(404, "策略不存在")
    service = get_service()
    task = service.enqueue(db, strategy, params=req.params)
    return TaskOut.model_validate(task)
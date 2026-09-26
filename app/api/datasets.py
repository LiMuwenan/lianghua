# -*- coding: utf-8 -*-
"""数据集 API：列表/覆盖状态、触发更新。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..config import load_config
from ..database import get_db
from ..models import Dataset, Strategy
from ..schemas import DatasetOut, RunRequest, TaskOut
from ..services import dataset_scan
from ..services.task_service import get_service

router = APIRouter(prefix="/api/datasets", tags=["datasets"])


@router.get("", response_model=list[DatasetOut])
def list_datasets(db: Session = Depends(get_db)):
    """数据集列表，并同步一次文件扫描（覆盖度/最新日期/滞后天数）。"""
    cfg = load_config()
    dataset_scan.scan_all(cfg, db)
    return db.query(Dataset).all()


@router.post("/{ds_id}/run", response_model=TaskOut)
def run_dataset(ds_id: int, req: RunRequest, db: Session = Depends(get_db)):
    """触发数据集更新：需先登记对应的数据脚本。kind=script。"""
    ds = db.query(Dataset).get(ds_id)
    if ds is None:
        raise HTTPException(404, "数据集不存在")

    # 找到指向该数据集（data_dep 匹配）的 script 类登记脚本，取第一个可运行
    script = (
        db.query(Strategy)
        .filter(Strategy.kind == "script", Strategy.data_dep == ds.name)
        .order_by(Strategy.id)
        .first()
    )
    if script is None:
        raise HTTPException(409, f"未找到与数据集「{ds.name}」关联的数据脚本，请先在 manifest 中登记 data_dep")

    service = get_service()
    task = service.enqueue(db, script, params=req.params)
    # 记录数据集最近触发时间
    ds.last_run_at = task.started_at
    db.commit()
    return TaskOut.model_validate(task)
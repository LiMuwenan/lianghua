# -*- coding: utf-8 -*-
"""数据集 API：列表/覆盖状态、触发更新（全量/增量）。"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..config import load_config
from ..database import get_db
from ..models import Dataset
from ..schemas import DatasetOut
from ..services import dataset_scan
from ..services.task_service import get_service

router = APIRouter(prefix="/api/datasets", tags=["datasets"])


@router.get("", response_model=list[DatasetOut])
def list_datasets(db: Session = Depends(get_db)):
    """数据集列表，并同步一次文件扫描（覆盖度/最新日期/滞后天数）。"""
    cfg = load_config()
    dataset_scan.scan_all(cfg, db)
    return db.query(Dataset).all()


@router.post("/{ds_id}/run")
def run_dataset(ds_id: int, mode: str = Query("full"),
                db: Session = Depends(get_db)):
    """触发内建摄取任务：mode=full|incremental，kind=ingest。"""
    if mode not in ("full", "incremental"):
        raise HTTPException(422, f"mode 仅支持 full|incremental，收到: {mode}")
    ds = db.query(Dataset).get(ds_id)
    if ds is None:
        raise HTTPException(404, "数据集不存在")

    svc = get_service()
    task = svc.enqueue_ingest(db, ds.name, ds.id, mode=mode)
    return {"task_id": task.id, "kind": task.kind}
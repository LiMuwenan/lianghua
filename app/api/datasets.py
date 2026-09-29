# -*- coding: utf-8 -*-
"""数据集 API：列表/覆盖状态、触发获取数据（起始日期可选）。"""
from datetime import date

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
    """数据集列表，并同步一次文件扫描（最新日期/滞后天数）。"""
    cfg = load_config()
    dataset_scan.scan_all(cfg, db)
    return db.query(Dataset).all()


@router.post("/{ds_id}/run")
def run_dataset(ds_id: int, start_date: str = Query(""),
                db: Session = Depends(get_db)):
    """触发获取数据（kind=ingest）。start_date 可选；留空=按每股断点续传/空库默认2005-01-01。"""
    start_date = (start_date or "").strip()
    if start_date:
        try:
            date.fromisoformat(start_date)
        except ValueError:
            raise HTTPException(422, f"start_date 格式应为 YYYY-MM-DD，收到: {start_date}")
    ds = db.query(Dataset).get(ds_id)
    if ds is None:
        raise HTTPException(404, "数据集不存在")

    svc = get_service()
    task = svc.enqueue_ingest(db, ds.name, ds.id,
                              params={"start_date": start_date} if start_date else {})
    return {"task_id": task.id, "kind": task.kind}
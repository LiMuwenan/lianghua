# -*- coding: utf-8 -*-
"""定时任务 API（P0 仅建表 + 只读列出，不实例化调度）。"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import CronTask
from ..schemas import CronOut

router = APIRouter(prefix="/api/cron", tags=["cron"])


@router.get("", response_model=list[CronOut])
def list_cron(db: Session = Depends(get_db)):
    """定时任务列表（P0 预留）。"""
    return db.query(CronTask).all()
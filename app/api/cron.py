# -*- coding: utf-8 -*-
"""定时任务 API（P1）：列表/新建/更新(改名·改cron·启停)/删除/立即执行。

所有变更同步到调度器（sync_all 重建 job），并实时刷新 next_run_at。
触发统一走 P0 任务执行路径（串行队列/日志/手动终止）。
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session

from ..database import SessionLocal, get_db
from ..models import CronTask
from ..schemas import CronCreate, CronOut, CronUpdate
from ..services.scheduler import TZ, compute_next, get_scheduler

router = APIRouter(prefix="/api/cron", tags=["cron"])

_sched = get_scheduler()


def _validate_cron(expr: str, name: str) -> None:
    """校验标准 cron 5 字段表达式，非法则 422。"""
    expr = (expr or "").strip()
    try:
        CronTrigger.from_crontab(expr, timezone=TZ)
    except Exception:  # noqa: BLE001
        raise HTTPException(422, f"无效的 cron 表达式({name}): {expr!r}")


def _cron_out(db, ct) -> CronOut:
    db.refresh(ct)
    return CronOut(id=ct.id, name=ct.name, kind=ct.kind, ref_id=ct.ref_id,
                   cron_expr=ct.cron_expr, params=ct.params or {}, enabled=ct.enabled,
                   last_run_at=ct.last_run_at, next_run_at=ct.next_run_at)


@router.get("", response_model=list[CronOut])
def list_cron(db: Session = Depends(get_db)):
    """定时任务列表，输出附带每次计算的下次运行时间。"""
    out = []
    for ct in db.query(CronTask).order_by(CronTask.id).all():
        ct.next_run_at = compute_next(ct.cron_expr) if ct.enabled else None
        out.append(_cron_out(db, ct))
    db.commit()
    return out


@router.post("", response_model=CronOut)
def create_cron(req: CronCreate, db: Session = Depends(get_db)):
    """新建定时任务；校验 ref_id 存在性，创建后同步调度器。"""
    _validate_cron(req.cron_expr, req.name)
    kind = req.kind or "ingest"
    # 校验引用对象存在
    from ..models import Dataset, Strategy
    if not db.query(Dataset if kind == "ingest" else Strategy).get(req.ref_id):
        raise HTTPException(404, f"引用的{'数据集' if kind == 'ingest' else '策略'}不存在: ref_id={req.ref_id}")
    ct = CronTask(name=req.name, kind=kind, ref_id=req.ref_id,
                  cron_expr=req.cron_expr, params=req.params or {},
                  enabled=req.enabled)
    db.add(ct)
    db.commit()
    db.refresh(ct)
    _sched.sync_all(db)
    return _cron_out(db, ct)


@router.patch("/{cid}", response_model=CronOut)
def update_cron(cid: int, req: CronUpdate, db: Session = Depends(get_db)):
    """更新定时任务：改名 / 改 cron / 启停 / 改参数。"""
    ct = db.query(CronTask).get(cid)
    if ct is None:
        raise HTTPException(404, "定时任务不存在")
    if req.cron_expr is not None:
        _validate_cron(req.cron_expr, ct.name)
        ct.cron_expr = req.cron_expr.strip()
    if req.name is not None:
        ct.name = req.name.strip()
    if req.enabled is not None:
        ct.enabled = req.enabled
    if req.params is not None:
        ct.params = req.params
    db.commit()
    _sched.sync_all(db)
    return _cron_out(db, ct)


@router.delete("/{cid}")
def delete_cron(cid: int, db: Session = Depends(get_db)):
    """删除定时任务（并移除调度器 job）。"""
    ct = db.query(CronTask).get(cid)
    if ct is None:
        raise HTTPException(404, "定时任务不存在")
    db.delete(ct)
    db.commit()
    _sched.sync_all(db)
    return {"ok": True, "id": cid}


@router.post("/{cid}/trigger", response_model=CronOut)
def trigger_cron(cid: int, db: Session = Depends(get_db)):
    """立即执行一次（复用任务执行路径），并在 next_run_at 体现。"""
    ct = db.query(CronTask).get(cid)
    if ct is None:
        raise HTTPException(404, "定时任务不存在")
    from ..services.task_service import get_service
    svc = get_service()
    if ct.kind == "ingest":
        from ..models import Dataset
        ds = db.query(Dataset).get(ct.ref_id)
        if ds is None:
            raise HTTPException(404, "引用的数据集不存在")
        svc.enqueue_ingest(db, ds.name, ds.id, params=ct.params or {})
    else:
        from ..models import Strategy
        strategy = db.query(Strategy).get(ct.ref_id)
        if strategy is None:
            raise HTTPException(404, "引用的策略不存在")
        svc.enqueue(db, strategy, params=ct.params or {})
    ct.last_run_at = datetime.now()
    db.commit()
    _sched.sync_all(db)
    return _cron_out(db, ct)
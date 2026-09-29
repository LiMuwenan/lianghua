# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, "d:/Project/lianghua")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401  注册所有表
from app.database import Base
from app.models import CronTask
from app.services.scheduler import CronScheduler, compute_next


def _db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/meta.db")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def test_compute_next_standard_cron():
    """标准 5 字段 cron（minute hour dom month dow）算出正确下次触发小时。"""
    nrt = compute_next("0 17 * * *")
    assert nrt is not None and nrt.hour == 17


def test_compute_next_empty_or_invalid():
    assert compute_next("") is None
    assert compute_next("not a cron") is None


def test_scheduler_sync_all_registers_enabled(tmp_path):
    """sync_all 只注册 enabled 任务，disabled 不注册；并回填 next_run_at。"""
    db = _db(tmp_path)
    on = CronTask(name="每日增量", kind="ingest", ref_id=1, cron_expr="0 17 * * *", enabled=True)
    off = CronTask(name="停用", kind="ingest", ref_id=2, cron_expr="0 8 * * *", enabled=False)
    db.add_all([on, off])
    db.commit()
    on_id, off_id = on.id, off.id

    s = CronScheduler()
    s.sync_all(db)

    assert s.scheduler.get_job(str(on_id)) is not None
    assert s.scheduler.get_job(str(off_id)) is None
    # 刷新后 next_run_at 已回填，且小时为 17
    db.refresh(on)
    assert on.next_run_at is not None and on.next_run_at.hour == 17
    # 停用那条不参与（next_run_at 为 None）
    db.refresh(off)
    assert off.next_run_at is None
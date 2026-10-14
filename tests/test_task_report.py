# -*- coding: utf-8 -*-
"""task_service 的 kind=report：入队一条 report 任务。"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import TaskRun
from app.services.task_service import TaskService


class _Cfg:
    ROOT = None
    logs_dir = None


def test_enqueue_report(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/meta.db")
    Base.metadata.create_all(bind=engine)
    DB = sessionmaker(bind=engine)
    db = DB()

    svc = TaskService.__new__(TaskService)      # 只测入队，不启动线程
    svc._q = __import__("queue").Queue()
    task = svc.enqueue_report(db)
    assert task.kind == "report" and task.ref_id == 0
    assert task.ref_name == "每日大盘汇总"
    assert db.query(TaskRun).count() == 1
    assert svc._q.qsize() == 1

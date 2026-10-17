# -*- coding: utf-8 -*-
"""任务终止行为测试：terminate 命中运行中任务、置取消事件、返回结果。"""
import sys
import threading
import time

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, "d:/Project/lianghua")

from app.config import Config, load_config
from app.database import Base
from app.models import TaskRun
from app.services import task_service as ts
from app.services.task_service import TaskService


class _FakeDB:
    """仅提供 _run_report 用到的 close()。"""

    def close(self):
        pass


def _tmp_db(tmp_path):
    """临时元数据库，返回 sessionmaker。"""
    engine = create_engine(f"sqlite:///{tmp_path}/meta.db")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)


def test_terminate_cancel_event():
    """terminate 未登记返回 False；登记后返回 True 且取消事件被置位。"""
    svc = TaskService(load_config())
    assert svc.terminate(999) is False

    svc._register(100)
    assert svc.terminate(100) is True
    assert svc.cancel_event(100).is_set()
    svc._unregister(100)
    assert svc.terminate(100) is False


def test_terminate_kills_subprocess(tmp_path):
    """运行一个 sleep 子进程，terminate 应 kill 并返回 aborted 摘要。"""
    svc = TaskService(load_config())
    script = tmp_path / "sleep_script.py"
    script.write_text("import time; time.sleep(30)\n", encoding="utf-8")
    result = {}

    def worker():
        svc._register(200)
        ev = svc.cancel_event(200)
        # 模拟 _run_subprocess 的轮询：等待取消事件或进程结束
        proc = None
        try:
            import subprocess
            proc = subprocess.Popen(
                [sys.executable, str(script)],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            )
            svc._procs[200] = proc
            deadline = time.time() + 60
            while proc.poll() is None:
                # 每次循环先判取消（与 task_service._run_subprocess 一致）
                if ev.is_set():
                    proc.kill()
                    proc.wait()
                    result["status"] = "aborted"
                    return
                if time.time() > deadline:
                    result["status"] = "timeout"
                    return
                time.sleep(0.05)
            if ev.is_set():
                result["status"] = "aborted"
        finally:
            svc._unregister(200)

    t = threading.Thread(target=worker)
    t.start()
    time.sleep(0.5)
    assert svc.terminate(200) is True
    t.join(timeout=5)
    assert result.get("status") == "aborted"


def test_terminate_marks_pending_row_aborted(tmp_path, monkeypatch):
    """未在运行中登记的行也要能终止：进程重启残留的 running 行、排队中的任务，
    统一落库标记为已终止；已结束的任务不再重复标记。"""
    DB = _tmp_db(tmp_path)
    db = DB()
    db.add(TaskRun(kind="ingest", ref_id=1, ref_name="平台日K数据", status="running", params={}))
    db.add(TaskRun(kind="report", ref_id=0, ref_name="每日大盘汇总", status="queued", params={}))
    db.add(TaskRun(kind="report", ref_id=0, ref_name="已结束", status="success", params={}))
    db.commit()
    db.close()

    monkeypatch.setattr(ts, "SessionLocal", lambda: DB())
    svc = TaskService(Config({"output_dir": str(tmp_path / "out")}))

    assert svc.terminate(1) is True    # 残留 running 行
    assert svc.terminate(2) is True    # 排队中任务
    assert svc.terminate(3) is False   # 已成功任务
    assert svc.terminate(999) is False  # 不存在

    db = DB()
    assert db.query(TaskRun).get(1).status == "aborted"
    assert db.query(TaskRun).get(1).result_summary.get("canceled") is True
    assert db.query(TaskRun).get(2).status == "aborted"
    assert db.query(TaskRun).get(3).status == "success"
    db.close()


def test_execute_task_skips_finalized_task(tmp_path, monkeypatch):
    """排队期间被终止的任务，worker 取出后不得再执行（不能被覆盖回 running）。"""
    DB = _tmp_db(tmp_path)
    db = DB()
    db.add(TaskRun(kind="report", ref_id=0, ref_name="每日大盘汇总", status="aborted", params={}))
    db.commit()
    db.close()

    monkeypatch.setattr(ts, "SessionLocal", lambda: DB())
    svc = TaskService(Config({"output_dir": str(tmp_path / "out")}))
    called = {"n": 0}
    monkeypatch.setattr(svc, "_run_report", lambda *a, **k: called.update(n=1) or {})

    svc._execute_task(1)

    assert called["n"] == 0
    db = DB()
    assert db.query(TaskRun).get(1).status == "aborted"
    db.close()


def test_reconcile_orphans_on_startup(tmp_path, monkeypatch):
    """启动清理：上个进程遗留的 queued/running 行统一标记为已终止，已结束行不受影响。"""
    DB = _tmp_db(tmp_path)
    db = DB()
    db.add(TaskRun(kind="ingest", ref_id=1, ref_name="中断运行", status="running", params={}))
    db.add(TaskRun(kind="report", ref_id=0, ref_name="遗留排队", status="queued", params={}))
    db.add(TaskRun(kind="report", ref_id=0, ref_name="已完成", status="success", params={}))
    db.commit()
    db.close()

    monkeypatch.setattr(ts, "SessionLocal", lambda: DB())
    svc = TaskService(Config({"output_dir": str(tmp_path / "out")}))

    assert svc.reconcile_orphans() == 2
    assert svc.reconcile_orphans() == 0   # 幂等：再跑没有可清理的行

    db = DB()
    assert db.query(TaskRun).get(1).status == "aborted"
    assert db.query(TaskRun).get(1).result_summary.get("canceled") is True
    assert db.query(TaskRun).get(2).status == "aborted"
    assert db.query(TaskRun).get(3).status == "success"
    db.close()


def test_run_report_passes_cancel(tmp_path, monkeypatch):
    """kind=report 必须把任务取消事件接进报告编排，终止后状态为 aborted。"""
    from app.services import report_service

    captured = {}

    def fake_report(cfg_, db, log=None, send=True, today=None, cancel=None):
        captured["cancel"] = cancel
        return {"status": "aborted", "output_files": []}

    monkeypatch.setattr(report_service, "run_daily_report", fake_report)
    monkeypatch.setattr(ts, "SessionLocal", lambda: _FakeDB())

    svc = TaskService(Config({"output_dir": str(tmp_path / "out")}))
    svc._register(7)
    svc.cancel_event(7).set()
    task = __import__("types").SimpleNamespace(id=7, ref_id=0, params={})

    res = svc._run_report(task, tmp_path / "task_7.log")

    assert res["status"] == "aborted"
    assert captured["cancel"] is not None
    assert captured["cancel"]() is True